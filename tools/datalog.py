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


def timestr(utc):
    if utc is None:
        return "?"
    secs = int(utc)
    ms = 1000 * (utc - secs)
    s = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(secs))
    s += ".%03u" % ms
    return s


class Entry:
    def __init__(self, kind, content, ctx):
        self.kind = kind
        self.content = content

    @classmethod
    def read(cls, arr, ctx):
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
        entry = None
        if (flags & 0x01) != 0 or kind == Kind.pad:
            pass
        else:
            if kind in map:
                # print(f"{str(Kind(kind))}, {entrySize}, {flags:#x}")
                try:
                    entry = map[kind](content, ctx)
                except (UnicodeDecodeError, IndexError) as err:
                    entry = None
                    print(f"{type(err).__name__}: {err}")
            if entry is None:
                entry = Entry(kind, content, ctx)
        return entry, 4 + entrySize

    def __str__(self):
        return "%u bytes" % len(self.content)

    def fixup(self, ctx):
        pass


class Boot(Entry):
    class Reason(IntEnum):
        Default = 0,
        WDT = 1,
        Exception = 2,
        SoftWDT = 3,
        SoftRestart = 4,
        DeepSleepAwake = 5,
        ExtSysReset = 6,

    def __init__(self, content, ctx):
        self.utc = None
        self.kind = Kind.boot
        self.reason = content[0]
        ctx.reset()

    def __str__(self):
        return "reason %s, %s" % (Boot.Reason(self.reason), timestr(self.utc))

    def fixup(self, ctx):
        self.utc = ctx.time.getUtc(0)

class Time(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.time
        (self.systemTime, self.utc) = struct.unpack("<II", content)

    def __str__(self):
        return "systemTime %u, %s" % (self.systemTime, timestr(self.utc))

    def getUtc(self, systemTime):
        return self.utc + (systemTime - self.systemTime) / 1000


class Domain(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.domain
        (self.id,) = struct.unpack("<H", content[:2])
        self.name = content[2:].decode()
        self.fields = []
        self.dataEntryCount = 0
        ctx.domains[self.id] = self
        ctx.domain = self
        ctx.fieldOffset = 0

        # Bugfixes in development
        if self.name == '':
            self.name = {
                1: 'sunsynk/inverter',
                2: "stsfan/inverter",
                3: "meter/immersion",
                4: "nt18b07/immersion"
            }[self.id]


    def __str__(self):
        return "id %u, name '%s'" % (self.id, self.name)

class Field(Entry):
    class Type(IntEnum):
        Unsigned = 0,
        Signed = 1,
        Float = 2,

    def __init__(self, content, ctx):
        self.kind = Kind.field
        self.domain = ctx.domain
        self.domain.fields.append(self)
        (self.id, self.type, self.size) = struct.unpack("<HBB", content[:4])
        self.name = content[4:].decode()
        self.offset = ctx.fieldOffset
        ctx.fieldOffset += self.size

        # Bugfixes in development
        if self.domain.name == 'nt18b07/immersion':
            self.type = Field.Type.Signed


    def typestr(self):
        if self.type == Field.Type.Float:
            return {4: "float", 8: "double"}[self.size]
        elif self.type == Field.Type.Unsigned:
            return f"uint{self.size*8}_t"
        elif self.type == Field.Type.Signed:
            return f"int{self.size*8}_t"
        else:
            return f"{Type(self.type)}{self.size*8}"

    def getValue(self, data):
        map = {
            Field.Type.Float: {
                4: "f", 8: "d"
            },
            Field.Type.Unsigned: {
                1: "B", 2: "H", 4: "I", 8: "Q"
            },
            Field.Type.Signed: {
                1: "b", 2: "h", 4: "i", 8: "q"
            }
        }
        fmt = "<" + map[self.type][self.size]
        (value,) = struct.unpack(fmt, data[self.offset:self.offset+self.size])
        return round(value, 3)

    def __str__(self):
        return "%s id %u, %s, name '%s'" % (self.domain.name, self.id, self.typestr(), self.name)


class Data(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.data
        self.time = ctx.time
        (self.systemTime, domain, self.reserved) = struct.unpack("<IHH", content[:8])
        self.domain = ctx.domains.get(domain)
        self.data = content[8:]

        # Bugfixes in development
        if self.domain is not None:
            self.domain.dataEntryCount += 1
            if self.domain.id == 1:
                if len(self.data) == 46:
                    self.data = array.array('H', [x for x in self.data]).tobytes()
            elif self.domain.id == 4:
                temps = array.array('h', self.data)
                if temps[0] < 100:
                    self.data = array.array('h', [t*10  for t in temps]).tobytes()


    def __str__(self):
        utc = f", {timestr(self.time.getUtc(self.systemTime))}" if self.time else ""
        s = f"systemTime {self.systemTime}{utc}, domain {self.domain}"
        if self.domain is None:
            s += f", {len(self.data)} bytes"
        else:
            s += ": " + ", ".join(str(f.getValue(self.data)) for f in self.domain.fields)
        return s

    def fixup(self, ctx):
        if self.time is None:
            self.time = ctx.time


class Exception(Entry):
    def __init__(self, content, ctx):
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

class BlockList(dict):
    def load(self, filename):
        f = open(filename, "rb")
        f.seek(0, os.SEEK_END)
        fileSize = f.tell()
        f.seek(0, os.SEEK_SET)
        if fileSize % BLOCK_SIZE != 0:
            print("WARNING! File size is not a multiple of block size")

        dupes = 0
        blockCount = 0
        for b in range(fileSize // BLOCK_SIZE):
            pos = f.tell()
            block = f.read(BLOCK_SIZE)
            (size, kind, flags, magic, sequence) = struct.unpack("<HBBII", block[:12])
            if magic != MAGIC:
                print("** BAD MAGIC")
                continue
            if kind != Kind.block:
                print("** BAD BLOCK kind")
                continue
            if sequence in self:
                dupes += 1
                continue
            self[sequence] = block[12:]
            blockCount += 1

        print(f"{os.path.basename(filename)}: {blockCount} new blocks, {dupes} dupes")


class DataLog:
    def __init__(self):
        self.entries = []
        self.reset()

    def reset(self):
        self.time = None
        self.domains = {}
        self.domain = None
        self.fieldOffset = 0

    def loadBlock(self, block):
        off = 0
        while off < len(block):
            entry, size = Entry.read(block[off:], self)
            off += alignup4(size)

            if entry is None:
                continue

            if entry.kind == Kind.time:
                self.time = entry
                # Iterate previous DATA records as timeref now known
                for e in reversed(self.entries):
                    e.fixup(self)
                    if e.kind == Kind.boot:
                        break

            self.entries.append(entry)


def main():
    parser = argparse.ArgumentParser(description='DataLog tool')
    parser.add_argument('input', nargs='*', help='Log file to read')

    args = parser.parse_args()
    blocks = BlockList()
    for f in args.input:
        blocks.load(f)

    log = DataLog()
    for b in sorted(blocks.keys()):
        log.loadBlock(blocks[b])

    dataCount = 0

    def printData():
        if dataCount != 0:
            print(f"Kind.data x {dataCount}")

    for entry in log.entries:
        # if entry.kind == Kind.data:
        #     dataCount += 1
        #     continue
        # printData()
        # dataCount = 0
        print(f"{str(entry.kind)}: {entry}")
        if entry.kind == Kind.data and entry.domain is not None:
            for f in entry.domain.fields:
                print(f"{f.name}[{f.id}] = {f.getValue(entry.data)}")

    printData()

    print(f"{len(log.entries)} entries loaded")


if __name__ == "__main__":
    main()
