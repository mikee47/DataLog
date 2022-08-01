#
#!/usr/bin/env python3
# Script to build Firmware Filesystem image
#
# See readme.md for further information
#

import os, json, sys, struct, time, array
import argparse
from enum import IntEnum
import http.client

class Kind(IntEnum):
    pad = 0,        # Unused padding
    block = 1,      # Identifies start of block
    boot = 2,       # System boot
    time = 3,       # Contains RTC value and corresponding system time
    domain = 4,     # Qualifies following fields (e.g. name of device)
    field = 5,      # Field identification record
    data = 6,       # Data record
    exception = 7,  # Exception information
    map = 8,        # Map of sequence numbers
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
            Kind.map: Map,
        }

        (entrySize, kind, flags) = struct.unpack("<HBB", arr[:4])
        content = arr[4:4+entrySize]
        entry = None
        if flags != 0xfe or kind == Kind.pad:
            return None, -1

        if kind in map:
            print(f"{str(Kind(kind))}, {entrySize}, {flags:#x}")
            try:
                entry = map[kind](content, ctx)
            except (UnicodeDecodeError, IndexError, struct.error) as err:
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
        (self.id, self.type, self.size) = struct.unpack("<HBB", content[:4])
        self.name = content[4:].decode()
        self.offset = ctx.fieldOffset
        ctx.fieldOffset += self.size
        self.domain.fields.append(self)

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
        try:
            fmt = "<" + map[self.type][self.size]
        except:
            print(self.__dict__)
            print(f"type {self.type}, size {self.size}, name {self.name}")
            raise
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


class Map(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.map
        self.map = array.array("I", content)

    def __str__(self):
        return ", ".join(str(e) for e in self.map)


def alignup4(n):
    return (n + 3) & ~3


class Block:
    SIZE = 16384
    MAGIC = 0xa78be044

    @classmethod
    def parse(cls, data):
        b = Block()
        b.header = data[:12]
        (b.size, b.kind, b.flags, b.magic, b.sequence) = struct.unpack("<HBBII", b.header)
        if b.magic != Block.MAGIC:
            print("** BAD MAGIC")
            return None
        if b.kind != Kind.block:
            print("** BAD BLOCK kind")
            return None
        b.content = data[12:]
        return b

    def __str__(self):
        return f"{self.sequence:#010x} {Kind(self.kind)}, {self.flags:#02x}, {self.magic:#08x}"


class BlockList(dict):
    def load(self, filename):
        f = open(filename, "rb")
        f.seek(0, os.SEEK_END)
        fileSize = f.tell()
        f.seek(0, os.SEEK_SET)
        if fileSize % Block.SIZE != 0:
            print("WARNING! File size is not a multiple of block size")

        dupes = 0
        blockCount = 0
        for b in range(fileSize // Block.SIZE):
            pos = f.tell()
            block = Block.parse(f.read(Block.SIZE))
            if block is None:
                continue
            if block.sequence in self:
                dupes += 1
                continue
            self.append(block)
            blockCount += 1

        print(f"{os.path.basename(filename)}: {blockCount} new blocks, {dupes} dupes")

    def append(self, block):
        self[block.sequence] = block

    def save(filename):
        f = open(filename, "wb")
        # for b in sorted(self):



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
        while off < len(block.content):
            print(f"offset {12+off:#x}: {' '.join(hex(x) for x in block.content[off:off+8])}")
            entry, size = Entry.read(block.content[off:], self)
            if size < 0:
                print(f"Skipping block {block.sequence:#x} from offset {off:#x}")
                break
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

    seq = sorted(blocks.keys())
    if len(seq) != 0:
        cur = seq[0]
        for n in seq[1:]:
            cur += 1
            if n != cur:
                print(f"Missing {cur:#x}")

        # print("\r\n".join(str(n) for n in seq))

        lastBlock = seq[len(seq)-1]
        conn = http.client.HTTPConnection("192.168.1.115",  timeout=10)
        print(f"lastBlock {lastBlock} ({lastBlock:#x})")
        conn.request("GET", f"/datalog?start={lastBlock+1}")
        rsp = conn.getresponse()
        print(rsp.status, rsp.reason)
        while chunk := rsp.read(Block.SIZE):
            block = Block.parse(chunk)
            if block is None:
                continue
            print(block)
            blocks.append(block)
            

    # return  ###


    log = DataLog()
    seq = sorted(blocks.keys())
    for b in seq:
        print(f"Block {str(blocks[b])}")
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
