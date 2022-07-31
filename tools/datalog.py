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

class Type(IntEnum):
    Unsigned = 0,
    Signed = 1,
    Float = 2,

class Reason(IntEnum):
    Default = 0,
    WDT = 1,
    Exception = 2,
    SoftWDT = 3,
    SoftRestart = 4,
    DeepSleepAwake = 5,
    ExtSysReset = 6,


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
                (entrySize, kind, flags) = struct.unpack("<HBB", block[off:off+4])
                line = "  @0x%08x size %4u, %s, flags %02x" % (off, entrySize, Kind(kind), flags)
                off += 4
                if (flags & 0x01) != 0:
                    print("skipping %s" % line)
                else:
                    if kind == Kind.boot:
                        reason = block[off]
                        line += ", reason %s" % Reason(reason)
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
                        line += ", id %u, %s(%u), name '%s'" % (id, Type(type), size, name)
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
