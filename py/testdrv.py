'''
    ThunderGate - an open source toolkit for PCI bus exploration
    Copyright (C) 2015  Saul St. John

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import ctypes
from ctypes import *
import random
import tglib as tg
import reutils
from ctypes import cast, POINTER, sizeof, c_char

from tapdrv import TapDriver
from time import sleep
usleep = lambda x: sleep(x / 1000000.0)

class TestDriver(object):
    def __init__(self, dev):
        self.dev = dev

    def __enter__(self):
        return self

    def __exit__(self, t, v, traceback):
        pass

    def run(self):
        self.dev.init()

        #with TapDriver(self.dev) as tap:
        #    self.test_send(tap)
        #self.test_dmar()
        #self.test_rdmar()
        self.test_dmaw()

    def clear_txmbufs(self):
        for i in range(0x8000, 0x10000, 4):
            self.dev.mem.write_dword(i, 0)

    def clear_txbds(self):
        for i in range(0x4000, 0x4800, 4):
            self.dev.mem.write_dword(i, 0)

    def block_pump(self, block):
        bn = block.block_name
        if bn.endswith("_x"):
            bn = bn[:-2]
        if bn.endswith("_regs"):
            bn = bn[:-5]
        print "[+] pumping %s" % bn
        block.block_enable(quiet = 1)
        usleep(10)
        block.block_disable(quiet = 1)

    def spy_read(self, tap):
        dev = self.dev
        dev.sbdi.block_disable()
        dev.sbdc.block_disable()
        dev.sbds.block_disable()
        dev.sdi.block_disable()
        dev.sdc.block_disable()
        dev.rxcpu.halt()

        state = reutils.state_save(dev)

        test_buf_v = dev.interface.mm.alloc(128)
        test_buf_p = dev.interface.mm.get_paddr(test_buf_v)
        print "[+] allocated test buffer at vaddr %x, paddr %x" % (test_buf_v, test_buf_p)
        for i in range(128):
            cast(test_buf_v, POINTER(c_char))[i] = '\xb4'

        dev.mem.txbd[0].addr_hi = test_buf_p >> 32
        dev.mem.txbd[0].addr_low = test_buf_p & 0xffffffff
        
        dev.mem.txbd[0].flags.l4_cksum_offload = 0
        dev.mem.txbd[0].flags.ip_cksum_offload = 0
        dev.mem.txbd[0].flags.jumbo_frame = 0
        dev.mem.txbd[0].flags.hdrlen_2 = 0
        dev.mem.txbd[0].flags.snap = 0
        dev.mem.txbd[0].flags.vlan_tag = 0
        dev.mem.txbd[0].flags.coalesce_now = 0
        dev.mem.txbd[0].flags.cpu_pre_dma = 0
        dev.mem.txbd[0].flags.cpu_post_dma = 0
        dev.mem.txbd[0].flags.hdrlen_3 = 0
        dev.mem.txbd[0].flags.hdrlen_4 = 0
        dev.mem.txbd[0].flags.hdrlen_5 = 0
        dev.mem.txbd[0].flags.hdrlen_6 = 0
        dev.mem.txbd[0].flags.hdrlen_7 = 0
        dev.mem.txbd[0].flags.no_crc = 0
        dev.mem.txbd[0].flags.packet_end = 1

        dev.mem.txbd[0].length = 64
        dev.mem.txbd[0].vlan_tag = 0
        dev.mem.txbd[0].reserved = 0

        print "[+] txbd[0] forged"
        state = reutils.state_diff(dev, state)

        dev.sbdi.ofs_48 = 0x210
        print "[+] sbdi mailbox msg delivered"
        state = reutils.state_diff(dev, state)


        self.block_pump(dev.sdi)
        state = reutils.state_diff(dev, state)

    def test_send(self, tap):
        dev = self.dev
        tap._link_detect()
        dev.rxcpu.halt()
        dev.sbds.block_disable()
        dev.sdi.block_disable()

        dev.sdi.mode.pre_dma_debug = 1

        print "[+] saving initial state"
        state = reutils.state_save(dev)
        
        print "[+] submitting test packet to tap driver"
        tap.send('\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x88\xb5' + ('\xaa\x55' * 25), flags=("cpu_post_dma"))
        usleep(10)
        state = reutils.state_diff(dev, state)

        dev.rdma.block_disable()
        usleep(10)
        state = reutils.state_diff(dev, state)

        dev.sbds.block_enable()
        usleep(10)
        state = reutils.state_diff(dev, state)

        dev.sdi.block_enable()
        usleep(10)
        state = reutils.state_diff(dev, state)

        dev.rdma.block_enable()
        usleep(10)
        state = reutils.state_diff(dev, state)

    def test_rdmar(self, init=1, reset=0):
        dev = self.dev
        dev.reset()

        print "[+] testing remote dma read"

        vaddr = dev.interface.mm.alloc(8)
        paddr = dev.interface.mm.get_paddr(vaddr)
        buf = ctypes.cast(vaddr, ctypes.POINTER(ctypes.c_uint32))

        buf[0] = 0xdeadbeee
        buf[1] = 0xdeadbeef

        cnt = 0

        while dev.mem.read_dword(0xb50) >> 16 != 0x88b5:
            cnt += 1
            if cnt > 10000:
                print "[-] fw signature not found in gencomm"
                return
            usleep(10)

        dev.mem.write_dword(0xb54, paddr >> 32)
        dev.mem.write_dword(0xb58, paddr & 0xffffffff)
        dev.mem.write_dword(0xb50, 0x88b50007)

        while dev.mem.read_dword(0xb50) & 0xff:
            pass

        if buf[0] != dev.mem.read_dword(0xb54) or buf[1] != dev.mem.read_dword(0xb58):
            print "[-] remote dma read test failed"
        else:
            print "[+] remote dma read test complete"

        dev.interface.mm.free(vaddr)

    def test_dmar(self, size=0x80, init=1, reset=0):
        dev = self.dev
        dev.rxcpu.halt()
        end = 0x6000 + (size * 4)
        print "[+] clearing device memory from 0x6000 to 0x%04x" % end
        for i in range(0x6000, end, 4):
                dev.mem.write_dword(i, 0)

        for i in range(0x6000, end, 4):
                if dev.mem.read_dword(i) != 0:
                        raise Exception("buffer not clear")
        
        print "[+] zapping rdi std rcb"
        dev.rdi.std_rcb.host_addr_hi = 0
        dev.rdi.std_rcb.host_addr_low = 0
        dev.rdi.std_rcb.ring_size = 0
        dev.rdi.std_rcb.max_frame_len = 0
        dev.rdi.std_rcb.disable_ring = 0
        dev.rdi.std_rcb.nic_addr = 0

        vaddr = dev.interface.mm.alloc(4 * size)
        paddr = dev.interface.mm.get_paddr(vaddr)
        buf = ctypes.cast(vaddr, ctypes.POINTER(ctypes.c_uint32))

        for i in range(size):
                buf[i] = 0xaabbccdd;

        if init:
            dev.bufman.block_enable(reset=reset)
            dev.ftq.block_reset()
            dev.hpmb.box[tg.mb_rbd_standard_producer].low = 0


        print "[+] setting up standard rcb"

        dev.rdi.std_rcb.host_addr_hi = (paddr >> 32)
        dev.rdi.std_rcb.host_addr_low = (paddr & 0xffffffff)
        dev.rdi.std_rcb.ring_size = 0x200
        dev.rdi.std_rcb.max_frame_len = 0
        dev.rdi.std_rcb.disable_ring = 0
        dev.rdi.std_rcb.nic_addr = 0x6000
        
        print "[+] initiating dma read of sz %x to buffer at vaddr %x, paddr %x" % (size, vaddr, paddr)
        dev.hpmb.box[tg.mb_rbd_standard_producer].low = size >> 3

        if init:
            blocks = ["rbdi", "rdma"]
            for b in blocks:
                    o = getattr(dev, b)
                    o.block_enable(reset=reset)
        
        usleep(100)
        

        for i in range(0x6000, end, 4):
                if dev.mem.read_dword(i) != buf[(i - 0x6000) >> 2]:
                        raise Exception("dma read test failed")

        print "[+] dma read test complete"

        dev.interface.mm.free(vaddr)

    def test_dmaw(self):
        dev = self.dev
        dev.rxcpu.halt()
        dev.bufman.block_enable()
        dev.grc.mode.pcie_tl_sel = 1
        dev.grc.mode.pcie_hi1k_en = 0
        

        buf_v = dev.interface.mm.alloc(0x80)
        buf_p = dev.interface.mm.get_paddr(buf_v)
        sbuf = cast(buf_v, POINTER(c_char * 0x80))

        dev.wdma.block_disable()
        dev.wdma.reset()

        dev.hc.block_disable()
        dev.hc.reset()

        dev.hc.status_block_host_addr_hi = buf_p >> 32
        dev.hc.status_block_host_addr_low = buf_p & 0xffffffff
        dev.hc.mode.no_int_on_force_update = 1

        usleep(10)
        
        state = reutils.state_save(dev)

        dev.hc.mode.coalesce_now = 1
        usleep(10)

        print "[*] status block is now"
        print "    %s" % repr(sbuf.contents.raw)
        print

        state = reutils.state_diff(dev, state)

        dev.wdma.block_enable()
        state = reutils.state_diff(dev, state)

        print "[*] status block is now"
        print "    %s" % repr(sbuf.contents.raw)
        print
        
        dev.hc.mode.coalesce_now = 1
        usleep(10)
        state = reutils.state_diff(dev, state)

        print "[*] status block is now"
        print "    %s" % repr(sbuf.contents.raw)
        print
        dev.hc.mode.coalesce_now = 1
        usleep(10)
        state = reutils.state_diff(dev, state)

        print "[*] status block is now"
        print "    %s" % repr(sbuf.contents.raw)
        print
