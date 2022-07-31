#
#!/usr/bin/env python3
# Script to build Firmware Filesystem image
#
# See readme.md for further information
#

import os, json, sys, struct, time, array
import argparse
from enum import IntEnum

BLOCK_SIZE = 16384
MAGIC = 0xa78be044

class Kind(IntEnum):
	pad = 0,        # Unused padding
	block = 1,      # Identifies start of block
	boot = 2,       # System boot
	time = 3,       # Contains RTC value and corresponding system time
	domain = 4,     # Qualifies following fields (e.g. name of device)
	field = 5,      # Field identification record
	data = 6,       # Data record
	exception = 7,  # Exception information
	erased = 0xff,  # Erased

class Entry:
    def __init__(self, kind, content):
        self.kind = kind
        self.content = content

    @classmethod
    def read(cls, arr, systemTimeRef):
        map = {
            Kind.boot: Boot,
            Kind.time: Time,
            Kind.domain: Domain,
            Kind.field: Field,
            Kind.data: Data,
            Kind.exception: Exception,
        }

        (entrySize, kind, flags) = struct.unpack("<HBB", arr[:4])
        content = arr[4:4+entrySize]
        if (flags & 0x01) != 0 or kind == Kind.pad:
            entry = None
        elif kind in map:
            entry = map[kind](content, systemTimeRef)
        else:
            entry = Entry(kind, content)
        return entry, 4 + entrySize

    def __str__(self):
        return "%u bytes" % len(self.content)


class Boot(Entry):
    class Reason(IntEnum):
        Default = 0,
        WDT = 1,
        Exception = 2,
        SoftWDT = 3,
        SoftRestart = 4,
        DeepSleepAwake = 5,
        ExtSysReset = 6,

    def __init__(self, content, systemTimeRef):
        self.kind = Kind.boot
        self.reason = content[0]

    def __str__(self):
        return "reason %s" % Boot.Reason(self.reason)


class Time(Entry):
    def __init__(self, content, systemTimeRef):
        self.kind = Kind.time
        (self.systemTime, self.utc) = struct.unpack("<II", content)

    def __str__(self):
        return "systemTime %u, %s" % (self.systemTime, time.ctime(self.utc))


class Domain(Entry):
    def __init__(self, content, systemTimeRef):
        self.kind = Kind.domain
        (self.id,) = struct.unpack("<H", content[:2])
        self.name = content[2:].decode()

    def __str__(self):
        return "id %u, name '%s'" % (self.id, self.name)

class Field(Entry):
    class Type(IntEnum):
        Unsigned = 0,
        Signed = 1,
        Float = 2,

    def __init__(self, content, systemTimeRef):
        self.kind = Kind.field
        (self.id, self.type, self.size) = struct.unpack("<HBB", content[:4])
        self.name = content[4:].decode()

    def __str__(self):
        return "id %u, %s(%u), name '%s'" % (self.id, Field.Type(self.type), self.size, self.name)


class Data(Entry):
    def __init__(self, content, systemTimeRef):
        self.kind = Kind.data
        self.systemTimeRef = systemTimeRef
        (self.systemTime, self.domain, self.reserved) = struct.unpack("<IHH", content[:8])
        self.data = content[8:]

    def __str__(self):
        if self.systemTimeRef == 0:
            utc = ""
        else:
            utc = f", {time.ctime(self.systemTimeRef + (self.systemTime / 1000))}"
        return f"systemTime {self.systemTime}{utc}, domain {self.domain}, {len(self.data)} bytes"


class Exception(Entry):
    def __init__(self, content, systemTimeRef):
        self.kind = Kind.exception
        (self.cause, self.epc1, self.epc2, self.epc3, self.excvaddr, self.depc) = struct.unpack("<6I", content[:24])
        self.stack = array.array("I", content[24:])

    def __str__(self):
        s = f"cause {self.cause:#010x}, epc1 {self.epc1:#010x}, epc2 {self.epc2:#010x}, epc3 {self.epc3:#010x}, excvaddr {self.excvaddr:#010x}, depc {self.depc:#010x}, stack {len(self.stack)}"
        if len(self.stack) > 0:
            s += "\r\n"
            s += ", ".join(f"{e:#010x}" for e in self.stack)
        return s


def alignup4(n):
    return (n + 3) & ~3

class DataLog:
    def __init__(self, filename):
        self.load(filename)

    def load(self, filename):
        f = open(filename, "rb")
        f.seek(0, os.SEEK_END)
        fileSize = f.tell()
        f.seek(0, os.SEEK_SET)
        blockCount = fileSize // BLOCK_SIZE
        if fileSize % BLOCK_SIZE != 0:
            print("WARNING! File size is not a multiple of block size")
        print("File contains %u blocks" % blockCount)

        systemTimeRef = 0
        for b in range(blockCount):
            pos = f.tell()
            block = f.read(BLOCK_SIZE)
            (size, kind, flags, magic, sequence) = struct.unpack("<HBBII", block[:12])
            print("@0x%08x size %u, %s, flags %02x, magic 0x%08x, sequence %u" % (pos, size, Kind(kind), flags, magic, sequence))
            if magic != MAGIC:
                print("** BAD MAGIC")
            off = 12
            while off < BLOCK_SIZE:
                entry, size = Entry.read(block[off:], systemTimeRef)
                off += alignup4(size)
                if entry is None:
                    continue
                if entry.kind == Kind.time:
                    systemTimeRef = entry.utc - (entry.systemTime / 1000)

                print(f"{str(entry.kind)}: {entry}")

                continue

                (entrySize, kind, flags) = struct.unpack("<HBB", block[off:off+4])
                line = "  @0x%08x size %4u, %s, flags %02x" % (off, entrySize, Kind(kind), flags)
                off += 4
                if (flags & 0x01) != 0:
                    print("skipping %s" % line)
                else:
                    if kind == Kind.boot:
                        reason = block[off]
                        line += ", reason %s" % Boot.Reason(reason)
                        systemTimeRef = 0
                    elif kind == Kind.exception:
                        (cause, epc1, epc2, epc3, excvaddr, depc) = struct.unpack("<6I", block[off:off+24])
                        stack = array.array("I", block[off+24:off+entrySize])
                        line += ", cause %u, epc1 0x%08x, epc2 0x%08x, epc3 0x%08x, excvaddr 0x%08x, depc 0x%08x, stack %u" % (cause, epc1, epc2, epc3, excvaddr, depc, len(stack))
                        if len(stack) > 0:
                            line += "\r\n"
                            line += ", ".join(hex(e) for e in stack)
                    elif kind == Kind.time:
                        (systemTime, utc) = struct.unpack("<II", block[off:off+8])
                        systemTimeRef = utc - (systemTime / 1000)
                        line += ", systemTime %u, %s" % (systemTime, time.ctime(utc))
                    elif kind == Kind.domain:
                        (id,) = struct.unpack("<H", block[off:off+2])
                        name = block[off+2:off+entrySize].decode()
                        line += ", id %u, name '%s'" % (id, name)
                    elif kind == Kind.field:
                        (id, type, size) = struct.unpack("<HBB", block[off:off+4])
                        name = block[off+4:off+entrySize].decode()
                        line += ", id %u, %s(%u), name '%s'" % (id, Field.Type(type), size, name)
                    elif kind == Kind.data:
                        (systemTime, domain, reserved) = struct.unpack("<IHH", block[off:off+8])
                        line += ", systemTime %u, %s, domain %u" % (systemTime, time.ctime(systemTimeRef + systemTime / 1000), domain)

                    if kind in [Kind.boot, Kind.time, Kind.exception, Kind.pad]:
                        print(line)

                off += alignup4(entrySize)


def main():
    parser = argparse.ArgumentParser(description='DataLog tool')
    parser.add_argument('input', help='Log file to read')

    args = parser.parse_args()
    log = DataLog(args.input)


if __name__ == "__main__":
    main()
