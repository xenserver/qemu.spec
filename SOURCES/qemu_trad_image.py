#! /usr/bin/python

import string
import struct
import sys
import re

QEMU_VM_FILE_MAGIC = 0x5145564d
QEMU_VM_FILE_VERSION = 0x00000003

QEMU_VM_EOF = 0x00
QEMU_VM_SECTION_START = 0x01
QEMU_VM_SECTION_PART = 0x02
QEMU_VM_SECTION_END = 0x03
QEMU_VM_SECTION_FULL = 0x04
QEMU_VM_SUBSECTION = 0x05

PCI_COMMAND = 0x04
PCI_COMMAND_INTX_DISABLE = 0x400

class Image(object):
    def __init__(self, f):
        self.f = f
        self.sections = []
        self.irq_count = [0] * 128

    def error(self, msg):
        sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
        sys.exit(1)

    def read_u8(self):
        buf = self.f.read(1)
        result = struct.unpack(">B", buf)
        return result[0]

    def read_be16(self):
        buf = self.f.read(2)
        result = struct.unpack(">H", buf)
        return result[0]

    def read_be32(self):
        buf = self.f.read(4)
        result = struct.unpack(">I", buf)
        return result[0]

    def read_be64(self):
        buf = self.f.read(8)
        result = struct.unpack(">Q", buf)
        return result[0]

    def read_buffer(self, length):
        return self.f.read(length)

    def save(self, f):
        f.write(struct.pack(">II", self.magic, self.version))

        for section in self.sections:
            section.save(f)

        f.write(struct.pack(">B", QEMU_VM_EOF))
        f.flush()

    def load(self, args):
        self.args = args
        self.load_header()
        while self.load_one_section():
            pass

    def load_header(self):
        self.magic = self.read_be32()
        if self.magic != QEMU_VM_FILE_MAGIC:
            self.error("not a qemu image")

        self.version = self.read_be32()
        if self.version != QEMU_VM_FILE_VERSION:
            self.error("save version %d not supported" % (self.version))

    def load_one_section(self):
        section_type = self.read_u8()

        if section_type in (QEMU_VM_SECTION_START,
                            QEMU_VM_SECTION_PART,
                            QEMU_VM_SECTION_END):
            # Need to load these section and then discard them.
            #
            # Repeat sections could have a body, but "ram" ones have
            # an empty one.
            if section_type == QEMU_VM_SECTION_START:
                section = CompleteSection(section_type)
                section.load_header(self)
                section.load_body(self)
            else:
                section = RepeatSection(section_type)
                section.load_header(self)

        elif section_type in (QEMU_VM_SECTION_FULL,):
            section = CompleteSection(section_type)
            section.load_header(self)
            section.load_body(self)
            self.sections.append(section)

        elif section_type == QEMU_VM_EOF:
            return False

        else:
            self.error("bad section: 0x%02x" % (section_type))

        return True

    def find_section(self, name):
        for s in self.sections:
            if s.idstr == name:
                return s
        return None

    def find_last_section(self, name):
        for s in reversed(self.sections):
            if s.idstr == name:
                return s
        return None

    def get_pci_addr(self, dev, id):
        addrs = []
        for arg in self.args:
            r = re.match(dev + ".*,addr=([0-9a-fA-F]+).*", arg)
            if r:
                addrs.append(int(r.group(1), 16))

        addrs.sort()
        if id < len(addrs):
            return addrs[id]
        else:
            self.error("no pci addr supplied: %s:%d" % (dev, id))

class Section(object):
    def __init__(self, section_type):
        self.section_type = section_type
        self.data = ""

    def load_body(self, image):
        t = string.maketrans(" :./-", "_____")
        n = "load_" + string.translate(self.idstr, t)
        try:
            func = getattr(self, n)
        except:
            image.error("no handler for '%s' section" % (self.idstr))

        self.data = ""
        func(image)

    def load_idstr(self, image):
        length = image.read_u8()
        self.idstr = image.read_buffer(length)
        self.new_idstr = self.idstr

    def packed(self):
        packed = struct.pack(">B", self.section_type)
        packed += self.packed_header()
        packed += self.data
        return packed

    def idstr_packed(self):
        return struct.pack(">B", len(self.new_idstr)) + self.new_idstr

    def save(self, f):
        if self.data:
            f.write(self.packed())

    def load_ram(self, i):
        # "ram" section is empty.
        pass

    def load_timer(self, i):
        cpu_clock_offset = i.read_be64()

        self.data = struct.pack(">QQQ", 0, 0, cpu_clock_offset)

    def load_fw_cfg(self, i):
        # cur_entry + cur_offset
        i.read_buffer(2*2)

        # Discarding as not present in QEMU
        self.data = None

    def load_I440FX(self, i):
        self.load_generic_pci_device(i, 0)
        self.data += struct.pack(">B", 0) # smm_enabled

        self.new_idstr = "0000:00:00.0/I440FX"
        self.version_id = 3

    def load_PIIX3(self, i):
        self.load_generic_pci_device(i, 1)

        self.new_idstr = "0000:00:01.0/PIIX3"

    def load_vga(self, i):
        self.load_generic_pci_device(i, 2)
        # latch + sr_index + sr + gr_index + gr + ar_index + ar + ar_flip_flop
        # + cr_index + cr + msr + fcr + st00 + st01 + dac_state + dac_sub_index
        # + dac_read_index + dac_write_index + dac_cache + palette + bank_offset
        self.data += i.read_buffer(4+1+8+1+16+1+21+4+1+256+1*8+3+768+4)

        have_vbe = i.read_u8()
        self.data += struct.pack(">B", have_vbe)
        if have_vbe:
            vbe_index = i.read_be16()
            vbe_regs = []
            for r in range(13):
                vbe_regs.append(i.read_be16())
            vbe_start_addr = i.read_be32()
            vbe_line_offset = i.read_be32()
            vbe_bank_mask = i.read_be32()

            self.data += struct.pack(">H", vbe_index)
            for r in range(10):
                self.data += struct.pack(">H", vbe_regs[r])
            self.data += struct.pack(">III", vbe_start_addr, vbe_line_offset, vbe_bank_mask)

        i.read_buffer(4+8) # vram_size + vram_phys_addr

        self.new_idstr = "0000:00:02.0/vga"
        self.version_id = 2

    def load_cirrus_vga(self, i):
        self.load_generic_pci_device(i, 2)
        # latch + sr_index + sr + gr_index + cirrus_shadow_gr0 + cirrus_shadow_gr1
        # + gr + ar_index + ar + ar_flip_flop + cr_index + cr + msr + fcr + st00
        # + st01 + dac_state + dac_sub_index + dac_read_index + dac_write_index
        # + dac_cache + palette + bank_offset + cirrus_hidden_dac_lockindex
        # + cirrus_hidden_dac_data + hw_cursor_x + hw_cursor_y
        self.data += i.read_buffer(4+1+256+1+1+1+254+1+21+4+1+256+1*8+3+768+4+1*2+4*2)

        # vga_acc + "some rubbish" + lfb_addr + "some rubbish" + lfb_end + vram_gfn
        i.read_buffer(1+4*4+8)

        # FIXME: need to consider older versions that appended the frame buffer

        self.new_idstr = "0000:00:02.0/cirrus_vga"
        self.version_id = 2

    def load_mc146818rtc(self, i):
        cmos_data = i.read_buffer(128)
        cmos_index = i.read_u8()

        i.read_buffer(28) # tm_sec + tm_min + tm_hour + tm_wday + tm_mday + tm_mon + tm_year

        periodic_timer = self.load_generic_timer(i)
        next_periodic_time = i.read_be64()
        i.read_be64() # next_second_time
        self.load_generic_timer(i) # second_timer
        self.load_generic_timer(i) # second_timer2

        # Qemu trad doesn't use irq coalescing for periodic interrupts.
        irq_coalesced = 0
        period = 0

        self.data += cmos_data
        self.data += struct.pack(">B", cmos_index)
        self.data += struct.pack(">IIIIIII", 0, 0, 0, 0, 0, 0, 0) # unused[0]
        self.data += periodic_timer.packed()
        self.data += struct.pack(">Q", next_periodic_time)
        self.data += struct.pack(">QQQ", 0, 0, 0) # unused[1]
        self.data += struct.pack(">II", irq_coalesced, period)

        # set version to 2 to recalculate missing fields from cmos in post_load
        self.version_id = 2

    def load_platform(self, i):
        self.load_generic_pci_device(i, 3)

        i.read_be64() # Discard padding

    def load_platform_fixed_ioport(self, i):
        flags = i.read_u8()

        # Merge this section into the platform section.
        platform_section = i.find_section("platform")
        if not platform_section:
            i.error("need section 'platform'")

        platform_section.data += struct.pack(">B", flags)
        platform_section.new_idstr = "0000:00:03.0/platform"
        platform_section.version_id = 4

        self.data = None

    def load_serial(self, i):
        # divider + rbr + ier + iir + lcr + mcr + lsr + msr + scr + fcr
        self.data = i.read_buffer(2+1*9)

        self.version_id = 3

    def load_rtl8139(self, i):
        addr = i.get_pci_addr("rtl8139", self.instance_id)
        self.load_generic_pci_device(i, addr)

        # phys + mult + TxStatus*4 + TxAddr*4 + RxBuf + RxBufferSize + RxBufPtr
        # + RxBufAddr + IntrStatus + IntrMask + TxConfig + RxConfig + RxMissed
        # + CSCR + Cfg9346 + Config0 + Config1 + Config3 + Config4 + Config5
        # + clock_enabled + bChipCmdState + MultiIntr + BasicModeCtrl + BasicModeStatus
        # + NWayAdvert + NWayLPAR + NWayExpansion + CpCmd + TxThresh + *unused*
        # + macaddr + rtl8139_mmio_io_addr + currTxDesc + currCPlusRxDesc + currCPlusTxDesc
        # + RxRingAddrLO + RxRingAddrHI
        self.data += i.read_buffer(6+8+4*12+2*2+4*3+2+1*8+2*7+1+4+6+4*6)

        # eeprom.contents*64 + eeprom.mode + eeprom.tick + eeprom.address + eeprom.input
        # + eeprom.output + eeprom.eecs + eeprom.eesk + eeprom.eedi + eeprom.eedo
        self.data += i.read_buffer(2*64+4*2+1+2*2+1*4)

        # TCTR + TimerInt + TCTR_base + tally_counters.(TxOk + RxOk + TxERR + RxERR
        # + MissPkt + FAE + Tx1Col + TxMCol + RxOkPhy + RxOkBrd + RxOkMul + TxAbt
        # + TxUndrn) + cplus_enabled
        self.data += i.read_buffer(4*2+8*4+4+2*2+4*2+8*2+4+2*2+4)

        self.new_idstr = "0000:00:%02x.0/rtl8139" % addr
        self.new_instance_id = 0
        self.version_id = 5

    def load_ide(self, i):
        self.load_generic_pci_device(i, 1)

        # dma_state
        for s in range(2):
            # cmd + status + addr + sector_num + nsector + ifidx
            self.data += i.read_buffer(1*2+4+8+4+1)

        # if_state
        for s in range(2):
            # cmd + drive1_selected
            self.data += i.read_buffer(1*2)

        # drive_state
        for s in range(4):
            mult_sectors = i.read_be32()
            identify_set = i.read_be32()
            self.data += struct.pack(">II", mult_sectors, identify_set)
            if identify_set:
                self.data += i.read_buffer(512) # identify_data
            i.read_u8() # write_cache
            feature = i.read_u8()
            error = i.read_u8()
            nsector = i.read_be32()
            sector = i.read_u8()
            lcyl = i.read_u8()
            hcyl = i.read_u8()
            hob_feature = i.read_u8()
            hob_nsector = i.read_u8()
            hob_sector = i.read_u8()
            hob_lcyl = i.read_u8()
            hob_hcyl = i.read_u8()
            select = i.read_u8()
            status = i.read_u8()
            lba48 = i.read_u8()
            sense_key = i.read_u8()
            asc = i.read_u8()
            self.data += struct.pack(">BBIBBBBBBBBBBBBBB",
                                     feature, error, nsector, sector, lcyl, hcyl,
                                     hob_feature, hob_sector, hob_nsector, hob_lcyl, hob_hcyl,
                                     select, status, lba48, sense_key, asc, 0)

        self.new_idstr = "0000:00:01.1/ide"

    def load_pckbd(self, i):
        # write_cmd + status + mode + pending
        self.data += i.read_buffer(1*4)

    def load_ps2kbd(self, i):
        self.load_generic_ps2_device(i)
        # scan_enabled + translate + scancode_set
        self.data += i.read_buffer(4*3)

    def load_ps2mouse(self, i):
        self.load_generic_ps2_device(i)
        # mouse_status + mouse_resolution + mouse_sample_rate + mouse_wrap + mouse_type
        # mouse_detect_state + mouse_dx + mouse_dy + mouse_dz + mouse_buttons
        self.data += i.read_buffer(1*6+3*4+1)

    def load_dma(self, i):
        # command + mask + flip_flop + dshift
        self.data += i.read_buffer(1*3+4)

        for c in range(4):
            # now[0] + now[1] + base[0] + base[1] + mode + page + pageh + dack + eop
            self.data += i.read_buffer(2*4+2*2+1*5)

    def load_fdc(self, i):
        # sra + srb + dor + tdr + dsr + msr + status0 + status1 + status2 + fifo
        # + data_pos + data_len + data_state + data_dir + eot + timer0 + timer1
        # + precomp_trk + config + lock + pwrd
        self.data += i.read_buffer(1*9+512+2*4+1*9)

        num_floppies = i.read_u8()
        self.data += struct.pack(">B", num_floppies)

        for d in range(num_floppies):
            # head + track + sect
            self.data += i.read_buffer(1*3)

    def load_UHCI_usb_controller(self, i):
        self.load_generic_pci_device(i, 1)

        num_ports = i.read_u8()
        self.data += struct.pack(">B", num_ports)

        # ports.ctrl
        self.data += i.read_buffer(2 * num_ports)
        # cmd + status + intr + frnum + fl_base_addr + sof_timing + status2
        self.data += i.read_buffer(2*4+4+1*2)

        # Add frame_timer
        frame_timer = self.load_generic_timer(i) # expire_time
        self.data += frame_timer.packed() # frame_timer

        self.new_idstr = "0000:00:01.2/uhci"
        self.version_id = 1

    def load_gpe(self, i):
        # Read in the bits to end up in the piix4_pm section.
        # ACPI_GPE0_BLK_LEN_V1 == ACPI_GPE0_BLK_LEN_V0 / 2
        self.gpe_sts = i.read_u8()
        self.gpe_en = i.read_u8()
        self.gpe_sts |= (i.read_u8() << 8)
        self.gpe_en |= (i.read_u8() << 8)

        # (gpe_sts + gpe_en) * 2 + sci_asserted
        i.read_buffer((1+1)*2+1) # discard remaining fields
        if self.version_id > 1:
            # For versions after Clearwater
            # gpe0_blk_address + gpe0_blk_half_len
            i.read_buffer(4*2) # discard remaining fields

        # This section is merged into the piix4_pm section.
        self.data = None

    def load_pci_devfn(self, i):
        # hotplug_devfn->status + hotplug_devfn->plug_evt + hotplug_devfn->plug_devfn
        i.read_buffer(128+1+1)

        # Discarding as we do not upgrade this section
        self.data = None

    def load_piix4acpi(self, i):
        self.load_generic_pci_device(i, 1)
        pm1_control = i.read_be16()
        if self.version_id > 2:
            # For versions after Clearwater
            i.read_buffer(4) # pm1a_evt_blk_address

        pm1_evt_sts = 0
        pm1_evt_en = 0
        pm1_cnt_cnt = pm1_control
        apmc = 0
        apms = 0
        self.data += struct.pack(">HHHBB",
                                 pm1_evt_sts, pm1_evt_en, pm1_cnt_cnt, apmc, apms)

        tmr_timer = GenericTimer()
        tmr_overflow = 0
        pci_status_up = 0
        pci_status_down = 0

        gpe_section = i.find_section("gpe")
        if not gpe_section:
            i.error("need section 'gpe'")

        self.data += tmr_timer.packed()
        self.data += struct.pack(">QHHII",
                                 tmr_overflow, gpe_section.gpe_sts, gpe_section.gpe_en,
                                 pci_status_up, pci_status_down)

        self.new_idstr = "0000:00:01.3/piix4_pm"
        self.version_id = 3

    def load_xen_pvdevice(self, i):
        addr = i.get_pci_addr("xen-pvdevice", 0)
        self.load_generic_pci_device(i, addr)

        self.new_idstr = "0000:00:%02x.0/xen-pvdevice" % addr

    def load_generic_pci_device(self, i, addr):
        # version + config + 4 * irq_state
        self.data = i.read_buffer(4 + 256 + 4*4)

        fields = struct.unpack('>I256B4I', self.data)
        config = fields[1:257]
        irq_state = fields[257:301]

        command = config[PCI_COMMAND] + (config[PCI_COMMAND + 1] << 8)
        if command & PCI_COMMAND_INTX_DISABLE:
            return

        for x in range(4):
            if irq_state[x] != 0:
                i.irq_count[x + (addr << 2)] += 1

    def load_generic_timer(self, i):
        timer = GenericTimer()
        timer.load(i)
        return timer

    def load_generic_ps2_device(self, i):
        # write_cmd + queue.rptr + queue.wptr + queue.count + queue.data
        self.data += i.read_buffer(4*4 + 256)


class CompleteSection(Section):
    def __init__(self, section_type):
        Section.__init__(self, section_type)

    def load_header(self, image):
        self.section_id = image.read_be32()
        self.load_idstr(image)

        image.read_be32() # instance_id
        # recalculate instance_id according to the image layout
        prev_section = image.find_last_section(self.idstr)
        if prev_section:
            self.instance_id = prev_section.instance_id + 1
        else:
            self.instance_id = 0
        self.new_instance_id = self.instance_id

        self.version_id = image.read_be32()

    def new(self, section_id, idstr, instance_id, version_id):
        self.section_id = section_id
        self.idstr = idstr
        self.new_idstr = idstr
        self.instance_id = instance_id
        self.new_instance_id = instance_id
        self.version_id = version_id

    def packed_header(self):
        packed = struct.pack(">I", self.section_id)
        packed += self.idstr_packed()
        packed += struct.pack(">II", self.new_instance_id, self.version_id)
        return packed


class RepeatSection(Section):
    def __init__(self, section_type):
        Section.__init__(self, section_type)

    def load_header(self, image):
        self.section_id = image.read_be32()


class GenericTimer(object):
    def __init__(self):
        self.expires = 0xffffffffffffffff

    def load(self, image):
        self.expires = image.read_be64()

    def expired(self):
        return self.expires == 0xffffffffffffffff

    def packed(self):
        return struct.pack(">Q", self.expires)


def convert_file(f1, f2, args):
    image = Image(f1)
    image.load(args)

    pci_bus = CompleteSection(QEMU_VM_SECTION_FULL)
    pci_bus.new(0, "PCIBUS", 0, 1)
    pci_bus.data += struct.pack(">I", 128) # nirq
    for i in range(128):
        pci_bus.data += struct.pack(">I", image.irq_count[i]) # irq_count
    image.sections.insert(0, pci_bus)

    if image.find_section("UHCI usb controller"):
        usb_ptr = CompleteSection(QEMU_VM_SECTION_FULL)
        usb_ptr.new(0, "2/usb-ptr", 0, 1)
        # addr + state + remote_wakeup + setup_state + setup_len + setup_index
        usb_ptr.data += struct.pack(">BIIIII", 0, 3, 0, 0, 0, 0)
        for i in range(8):
            usb_ptr.data += struct.pack(">B", 0) # setup_buf
        for i in range(16):
            usb_ptr.data += struct.pack(">IIII", 0, 0, 0, 0) # ptr_queue
        # head + n + protocol + idle
        usb_ptr.data += struct.pack(">IIIB", 0, 0, 1, 0)
        image.sections.append(usb_ptr)

    image.save(f2)
    return image


def is_trad_image(s1):
    f1 = open(s1, 'rb')

    image = Image(f1)
    image.load_header()
    section_type = image.read_u8()

    f1.close()

    return section_type == QEMU_VM_SECTION_START


def trad_upgrade(s1, s2, args):
    f1 = open(s1, 'rb')
    f2 = open(s2, 'wb')

    image = convert_file(f1, f2, args)

    f1.close()
    f2.close()

    return image
