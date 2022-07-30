#
#!/usr/bin/env python3
# Script to build Firmware Filesystem image
#
# See readme.md for further information
#

import os, json, sys, struct, time
import argparse
from enum import IntEnum

blockSize = 16384

class Kind(IntEnum):
	pad = 0,        # Unused padding
	block = 1,      # Identifies start of block
	boot = 2,       # System boot
	time = 3,       # Contains RTC value and corresponding system time
	domain = 4,     # Qualifies following fields (e.g. name of device)
	field = 5,      # Field identification record
	data = 6,       # Data record
	erased = 0xff,  # Erased

class Type(IntEnum):
    Unsigned = 0,
    Signed = 1,
    Float = 2,

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
        blockCount = fileSize // blockSize
        if fileSize % blockSize != 0:
            print("WARNING! File size is not a multiple of block size")
        print("File contains %u blocks" % blockCount)

        systemTimeRef = 0
        for b in range(blockCount):
            block = f.read(blockSize)
            (size, kind, flags, magic, sequence) = struct.unpack("<HBBII", block[:12])
            print("size %u, %s, flags %02x, magic 0x%08x, sequence %u" % (size, Kind(kind), flags, magic, sequence))
            off = 12
            while off < blockSize:
                (entrySize, kind, flags) = struct.unpack("<HBB", block[off:off+4])
                off += 4
                line = "  @0x%08x size %4u, %s, flags %02x" % (off, entrySize, Kind(kind), flags)
                if kind == Kind.boot:
                    reason = block[off]
                    line += ", reason %u" % reason
                    systemTimeRef = 0
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

                print(line)
                off += alignup4(entrySize)


def main():
    parser = argparse.ArgumentParser(description='DataLog tool')
    parser.add_argument('input', help='Log file to read')

    args = parser.parse_args()
    log = DataLog(args.input)


if __name__ == "__main__":
    main()
