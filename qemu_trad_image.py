#! /usr/bin/python

import string
import struct
import sys
import time

QEMU_VM_FILE_MAGIC = 0x5145564d
QEMU_VM_FILE_VERSION = 0x00000003

QEMU_VM_EOF = 0x00
QEMU_VM_SECTION_START = 0x01
QEMU_VM_SECTION_PART = 0x02
QEMU_VM_SECTION_END = 0x03
QEMU_VM_SECTION_FULL = 0x04
QEMU_VM_SUBSECTION = 0x05

class Image(object):
    def __init__(self, f):
        self.f = f
        self.sections = []

        # Data extracted from the qemu-trad save image that must be
        # passed OOB to QEMU.
        self.vram_size = 0
        self.vram_phys_addr = 0

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

    def load(self):
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
        self.error("need section '%s'" % (self.sections))

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

    def packed(self):
        packed = struct.pack(">B", self.section_type)
        packed += self.packed_header()
        packed += self.data
        return packed

    def idstr_packed(self):
        return struct.pack(">B", len(self.idstr)) + self.idstr

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
        i.read_buffer(2*2)
        self.data = None

    def load_I440FX(self, i):
        self.load_generic_pci_device(i)
        self.data += struct.pack(">B", 0) # smm_enabled

        self.idstr = "0000:00:00.0/I440FX"
        self.version_id = 3

    def load_PIIX3(self, i):
        self.load_generic_pci_device(i)
        self.data += struct.pack(">IIII", 0, 0, 0, 0) # Unused pci_irq_levels_vmstate

        self.idstr = "0000:00:01.0/PIIX3"
        self.version_id = 3

    def load_vga(self, i):
        self.load_generic_pci_device(i)

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

        i.vram_size = i.read_be32()
        i.vram_phys_addr = i.read_be64()

        self.idstr = "0000:00:02.0/vga"
        self.version_id = 2

    def load_cirrus_vga(self, i):
        self.load_generic_pci_device(i)

        self.data += i.read_buffer(4+1+256+1+1+1+254+1+21+4+1+256+1*8+3+768+4+1*2+2*4)

        i.read_u8()
        i.read_be32()
        lfb_addr = i.read_be32()
        i.read_be32()
        lfb_end = i.read_be32()
        vram_gmfn = i.read_be64()
        i.vram_size = lfb_end - lfb_addr
       i.vram_phys_addr = vram_gmfn

        # FIXME: need to consider older versions that appended the frame buffer

        self.idstr = "0000:00:02.0/cirrus_vga"
        self.version_id = 2

    def load_mc146818rtc(self, i):
        cmos_data = i.read_buffer(128)
        cmos_index = i.read_u8()

        tm_sec = i.read_be32()
        tm_min = i.read_be32()
        tm_hour = i.read_be32()
        tm_wday = i.read_be32()
        tm_mday = i.read_be32()
        tm_mon = i.read_be32() + 1
        tm_year = i.read_be32() + 1900
        dst = -1

        current_tm = time.struct_time((tm_year, tm_mon, tm_mday, tm_hour, tm_min, tm_sec, 0, 0, -1))

        periodic_timer = self.load_generic_timer(i)
        next_periodic_time = i.read_be64()
        next_second_time = i.read_be64()
        second_timer = self.load_generic_timer(i)
        second_timer2 = self.load_generic_timer(i)

        #
        # Ick.  The upstream save record is quite different.
        #

        # 1. Qemu trad doesn't use irq coalescing for periodic
        #    interrupts.
        irq_coalesced = 0
        period = 0

        # 2. The update timer is now a single timer. Use whichever
        #    timer is not expired.
        #
        # FIXME: or generate a new timer with suitable expiry?
        if second_timer.expired():
            if second_timer2.expired():
                update_timer = GenericTimer()
            else:
                update_timer = second_timer2
        else:
            update_timer = second_timer

        # 3. Calculate next_alarm_time from CMOS data.
        next_alarm_time = 0 # FIXME

        # 4. Current time is recorded quite differently and the best
        #    we can do is within 1 second.
        timer_section = i.find_section("timer")
        base_rtc = int(time.mktime(current_tm))
        last_update = int(time.mktime(current_tm) * 1000000000)
        offset = 0

        self.data += cmos_data
        self.data += struct.pack(">BIIIIIII", cmos_index, 0, 0, 0, 0, 0, 0, 0)
        self.data += periodic_timer.packed()
        self.data += struct.pack(">QQQQ", next_periodic_time, 0, 0, 0)
        self.data += struct.pack(">II", irq_coalesced, period)
        self.data += struct.pack(">QQQ", base_rtc, last_update, offset)
        self.data += update_timer.packed()
        self.data += struct.pack(">Q", next_alarm_time)

        self.version_id = 3

    def load_platform(self, i):
        self.load_generic_pci_device(i)

        i.read_be64() # Discard padding

    def load_platform_fixed_ioport(self, i):
        flags = i.read_u8()

        # Merge this section into the platform section.
        platform_section = i.find_section("platform")
        platform_section.data += struct.pack(">B", flags)
        platform_section.idstr = "0000:00:03.0/platform"
        platform_section.version_id = 4

        self.data = None

    def load_serial(self, i):
        self.data = i.read_buffer(2 + 9)

        self.version_id = 3

    def load_rtl8139(self, i):
        self.load_generic_pci_device(i)

        self.data += i.read_buffer(366)

        self.idstr = "0000:00:04.0/rtl8139"
        self.version_id = 5

    def load_ide(self, i):
        self.load_generic_pci_device(i)

        # dma_state
        for s in range(2):
            self.data += i.read_buffer(2+4+8+4+1)
        # if_state
        for s in range(2):
            self.data += i.read_buffer(2)
        # drive_state
        for s in range(4):
            self.data += i.read_buffer(4)
            identify_set = i.read_be32()
            self.data += struct.pack(">I", identify_set)
            if identify_set:
                self.data += i.read_buffer(512)
            write_cache = i.read_u8()
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

        self.idstr = "0000:00:01.1/ide"

    def load_pckbd(self, i):
        self.data += i.read_buffer(4)

    def load_ps2kbd(self, i):
        self.load_generic_ps2_device(i)
        self.data += i.read_buffer(3*4)

    def load_ps2mouse(self, i):
        self.load_generic_ps2_device(i)
        self.data += i.read_buffer(6+3*4+1)

    def load_dma(self, i):
        self.data += i.read_buffer(3+4+4*(2*4+2*2+5))

    def load_fdc(self, i):
        self.data += i.read_buffer(9+512+2*4+9)
        num_drives = i.read_u8()
        self.data += struct.pack(">B", num_drives)
        for d in range(num_drives):
            self.data += i.read_buffer(3)

    def load_UHCI_usb_controller(self, i):
        self.load_generic_pci_device(i)
        num_ports = i.read_u8()
        self.data += struct.pack(">B", num_ports)
        self.data += i.read_buffer(2 * num_ports)
        self.data += i.read_buffer(4*2+4+2)

        # Add frame_timer
        frame_timer = GenericTimer()
        frame_timer.expires = i.read_be64()
        self.data += frame_timer.packed()
        self.data += struct.pack(">Q", frame_timer.expires)

        # Add pending_int_mask
        self.data += struct.pack(">I", 0)

       self.idstr = "0000:00:01.2/uhci"
        self.version_id = 3

    def load_gpe(self, i):
        # Read in the bits to end up in the piix4_pm section.
        self.gpe_sts = i.read_u8()
        self.gpe_en = i.read_u8()
        self.gpe_sts |= (i.read_u8() << 8)
        self.gpe_en |= (i.read_u8() << 8)

        i.read_buffer(2*2+1+2*4) # discard remaining fields

        self.data = None

    def load_pci_devfn(self, i):
        i.read_buffer(128+2)
        self.data = None

    def load_piix4acpi(self, i):
        self.load_generic_pci_device(i)
        pm1_control = i.read_be16()
        i.read_buffer(4)

        pm1_evt_sts = 0
        pm1_evt_en = 0
        pm1_cnt_cnt = pm1_control
        apmc = 0
        apms = 0
        tmr_timer = GenericTimer()
        tmr_overflow = 0
        pci_status_up = 0
        pci_status_down = 0

        gpe_section = i.find_section("gpe")

        self.data += struct.pack(">HHHBB",
                                 pm1_evt_sts, pm1_evt_en, pm1_cnt_cnt, apmc, apms)
        self.data += tmr_timer.packed()
        self.data += struct.pack(">QHHII",
                                 tmr_overflow, gpe_section.gpe_sts, gpe_section.gpe_en,
                                 pci_status_up, pci_status_down)

        memhp_subsection = SubSection("piix4_pm/memhp", 1)
        memhp_subsection.data += struct.pack(">I", 0)

        self.data += memhp_subsection.packed()

        self.idstr = "0000:00:01.3/piix4_pm"
        self.version_id = 3

    def load_xen_pvdevice(self, i):
        self.load_generic_pci_device(i)
        self.idstr = "0000:00:05.0/xen-pvdevice"

        # FIXME: Discard this section as QEMU does not yet save/load
        # any state for this device.
        self.data = None

    def load_generic_pci_device(self, i):
        self.data = i.read_buffer(4 + 256 + 4*4)

    def load_generic_timer(self, i):
        timer = GenericTimer()
        timer.load(i)
        return timer

    def load_generic_ps2_device(self, i):
        self.data += i.read_buffer(4*4 + 256)


class CompleteSection(Section):
    def __init__(self, section_type):
        Section.__init__(self, section_type)

    def load_header(self, image):
       self.section_id = image.read_be32()
        self.load_idstr(image)
        self.instance_id = image.read_be32()
        self.version_id = image.read_be32()

    def new(self, section_id, idstr, instance_id, version_id):
        self.section_id = section_id
        self.idstr = idstr
        self.instance_id = instance_id
        self.version_id = version_id

    def packed_header(self):
        packed = struct.pack(">I", self.section_id)
        packed += self.idstr_packed()
        packed += struct.pack(">II", self.instance_id, self.version_id)
        return packed

class RepeatSection(Section):
    def __init__(self, section_type):
        Section.__init__(self, section_type)

    def load_header(self, image):
        self.section_id = image.read_be32()

class SubSection(Section):
    def __init__(self, idstr, version):
        Section.__init__(self, QEMU_VM_SUBSECTION)

        self.idstr = idstr
        self.version_id = version

    def packed_header(self):
        packed = self.idstr_packed()
        packed += struct.pack(">I", self.version_id)
        return packed

class GenericTimer(object):
    def __init__(self):
        self.expires = 0xffffffffffffffff

    def load(self, image):
        self.expires = image.read_be64()

    def expired(self):
        return self.expires == 0xffffffffffffffff

    def packed(self):
        return struct.pack(">Q", self.expires)

def convert_file(f1, f2):
    image = Image(f1)
    image.load()

    usb_ptr = CompleteSection(QEMU_VM_SECTION_FULL)
    usb_ptr.new(0, "2/usb-ptr", 0, 1)
    usb_ptr.data += struct.pack(">BIIIII", 1, 3, 0, 0, 0, 0)
    for i in range(8):
        usb_ptr.data += struct.pack(">B", 0)
    for i in range(16):
        usb_ptr.data += struct.pack(">IIII", 0, 0, 0, 0)
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


def trad_upgrade(s1, s2):
    f1 = open(s1, 'rb')
    f2 = open(s2, 'wb')

    image = convert_file(f1, f2)

    f1.close()
    f2.close()

    return image
