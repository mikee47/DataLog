#
#!/usr/bin/env python3
#
# Script to manage data logs
#

import os, json, sys, struct, time, array
import argparse
from enum import IntEnum
import http.client

verbose = False

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
    @classmethod
    def read(cls, block, offset, ctx):
        map = {
            Kind.boot: Boot,
            Kind.time: Time,
            Kind.domain: Domain,
            Kind.field: Field,
            Kind.data: Data,
            Kind.exception: Exception,
            Kind.map: Map,
        }

        entry = None
        (entrySize, kind, flags) = struct.unpack("<HBB", block.content[offset:offset+4])
        if kind == Kind.pad:
            return None, 0

        content = block.content[offset+4:offset+4+entrySize]
        if flags == 0xfe:
            if kind in map:
                # print(f"{str(Kind(kind))}, {entrySize}, {flags:#x}")
                try:
                    entry = map[kind](content, ctx)
                except (UnicodeDecodeError, IndexError, struct.error) as err:
                    entry = None
                    print(f"seq {block.sequence:#x} @{offset:#010x} {str(Kind(kind))}, size {entrySize}, flags {flags}, {type(err).__name__}: {err}")
            if entry is None:
                entry = UnknownEntry(kind, content, ctx)
        elif flags != 0xff:
            print(f"Corrupt block {block.sequence:#x}, skipping from offset {offset:#x}")
            return None, 0

        if entry is not None:
            entry.block = block
            entry.blockOffset = offset
        return entry, 4 + entrySize

    def isValid(self):
        return True

    def fixup(self, ctx):
        pass


class UnknownEntry(Entry):
    def __init__(self, kind, content, ctx):
        self.kind = Kind(kind)
        self.content = content

    def __str__(self):
        return f"{len(self.content)} bytes"


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
        self.systemTime = ctx.checkTime(self.systemTime)

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

    typemap = {
        (Type.Float, 4): ("float", "f"),
        (Type.Float, 8): ("double", "d"),
        (Type.Unsigned, 1): ("uint8_t", "B"),
        (Type.Unsigned, 2): ("uint16_t", "H"),
        (Type.Unsigned, 4): ("uint32_t", "I"),
        (Type.Unsigned, 8): ("uint64_t", "Q"),
        (Type.Signed, 1): ("int8_t", "b"),
        (Type.Signed, 2): ("int16_t", "h"),
        (Type.Signed, 4): ("int32_t", "i"),
        (Type.Signed, 8): ("int64_t", "q"),
    }

    def __init__(self, content, ctx):
        self.kind = Kind.field
        self.domain = ctx.domain
        (self.id, self.type, self.size) = struct.unpack("<HBB", content[:4])
        self.name = content[4:].decode()
        self.offset = ctx.fieldOffset
        ctx.fieldOffset += self.size
        if self.domain is not None:
            self.domain.fields.append(self)

    def typestr(self):
        t = Field.typemap.get((self.type, self.size))
        return t[0] if t else f"{str(Field.Type(self.type))}{self.size*8}"

    def getValue(self, data):
        try:
            fmt = "<" + Field.typemap[(self.type, self.size)][1]
        except:
            print(self.__dict__)
            print(f"type {self.type}, size {self.size}, name {self.name}")
            return 0
        (value,) = struct.unpack(fmt, data[self.offset:self.offset+self.size])
        return round(value, 3)

    def __str__(self):
        s = "?" if self.domain is None else self.domain.name
        s += f", id {self.id}, {self.typestr()}, name '{self.name}'"
        return s


class Data(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.data
        self.time = ctx.time
        (self.systemTime, self.domain_id, self.reserved) = struct.unpack("<IHH", content[:8])
        self.domain = ctx.domains.get(self.domain_id)
        self.data = content[8:]

        self.systemTime = ctx.checkTime(self.systemTime)

    def getUtc(self):
        return self.time.getUtc(self.systemTime) if self.time else 0

    def __str__(self):
        s = f"systemTime {self.systemTime}"
        if self.time:
            s += f", {timestr(self.getUtc())}"
        if self.domain is None:
            s += f", domain {self.domain_id}, {len(self.data)} bytes: "
            s += " ".join("%02x" % x for x in self.data)
        else:
            s += f", domain {self.domain}: "
            s += ", ".join(str(f.getValue(self.data)) for f in self.domain.fields)
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
        return ", ".join(f"{e:#x}" for e in self.map)


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
        if verbose:
            print(f"Block {b.sequence:#010x}, size {b.size}, {b.kind}, magic {b.magic:#010x}")
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
    def loadFromFile(self, filename):
        f = open(filename, "rb")
        f.seek(0, os.SEEK_END)
        fileSize = f.tell()
        f.seek(0, os.SEEK_SET)
        ft = time.strftime("%x %X", time.gmtime(os.path.getmtime(filename)))
        if verbose:
            print(f"Scanning '{os.path.basename(filename)}', {fileSize} bytes, {fileSize // Block.SIZE} blocks, {ft}")
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

        if verbose:
            print(f"{os.path.basename(filename)}: {blockCount} new blocks, {dupes} dupes")

    def append(self, block):
        self[block.sequence] = block

    def saveToFile(self, filename):
        with open(filename, "wb") as f:
            for b in sorted(self):
                block = self[b]
                f.write(block.header)
                f.write(block.content)


class DataLog:
    def __init__(self):
        self.entries = []
        self.reset()

    def reset(self):
        self.time = None
        self.domains = {}
        self.domain = None
        self.fieldOffset = 0
        self.prevSystemTime = 0
        self.highTime = 0 # Compensate for incorrect time wrapping

    def checkTime(self, t):
        # Fixup bad systemtime overflows
        if t < self.prevSystemTime:
            self.highTime += 1
        self.prevSystemTime = t
        return t + self.highTime * round((1 << 32) / 1000)

    def loadBlock(self, block):
        off = 0
        while off < len(block.content):
            # print(f"offset {12+off:#x}: {' '.join(hex(x) for x in block.content[off:off+8])}")
            entry, size = Entry.read(block, off, self)
            if size <= 0:
                # Can't read any more from this block
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
    parser.add_argument('--fetch', metavar='PATH', help='http path to datalog server')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--export', action='store_true')

    global verbose

    args = parser.parse_args()
    verbose = args.verbose

    blocks = BlockList()
    for f in args.input:
        blocks.loadFromFile(f)

    if len(blocks) == 0:
        lastBlock = 0
    else:
        seq = sorted(blocks.keys())
        cur = seq[0]
        for n in seq[1:]:
            cur += 1
            while n != cur:
                print(f"Missing {cur:#x}")
                cur += 1
        lastBlock = seq[len(seq)-1]
        print(f"lastBlock {lastBlock} ({lastBlock:#x})")
        # print("\r\n".join(str(n) for n in seq))

    if args.fetch:
        print(f"FETCH {args.fetch}")
        server, path = args.fetch.split('/', 1)
        conn = http.client.HTTPConnection(server,  timeout=10)
        block = lastBlock + 1
        conn.request("GET", f"/{path}?start={block}")
        rsp = conn.getresponse()
        print(rsp.status, rsp.reason)
        data = rsp.read()
        print(f"{len(data)} bytes received")
        if len(data) != 0:
            with open("logs/datalog-%08x.bin" % block, "wb") as f:
                f.write(data)
            newBlockCount = 0
            off = 0
            while off < len(data):
                block = Block.parse(data[off:off+Block.SIZE])
                off += Block.SIZE
                if block is None:
                    continue
                print(block)
                blocks.append(block)
                newBlockCount += 1

            # blocks.saveToFile("archive.bin")

    log = DataLog()
    for b in sorted(blocks):
        log.loadBlock(blocks[b])
    print(f"{len(log.entries)} entries loaded")

    if args.dump:
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
            print(f"{entry.block.sequence:#x} @ {entry.blockOffset:#x} {str(entry.kind)}: {entry}")
            # if entry.kind == Kind.data and entry.domain is not None:
            #     for f in entry.domain.fields:
            #         print(f"{f.name}[{f.id}] = {f.getValue(entry.data)}")

        printData()


    outputFieldMap = {
        "sunsynk/inverter": [
            # 'RunState',
            # 'ActiveEnergyToday',
            # 'ReactiveEnergyToday',
            # 'GridWorkTimeToday',
            # 'ActiveEnergyTotal',
            # 'ActiveEnergyTotalHigh',
            # 'BatChargeToday',
            # 'BatDischargeToday',
            # 'BatChargeTotal',
            # 'BatChargeTotalHigh',
            # 'BatDischargeTotal',
            # 'BatDischargeTotalHigh',
            # 'GridImportToday',
            # 'GridExportToday',
            # 'GridFrequency',
            # 'GridExportTotal',
            # 'GridExportTotalHigh',
            # 'LoadEnergyToday',
            # 'LoadEnergyTotal',
            # 'LoadEnergyTotalHigh',
            'DcTemp',
            'IgbtTemp',
            # 'PvEnergyToday',
            # 'Pv1Voltage',
            # 'Pv1Current',
            # 'Pv2Voltage',
            # 'Pv2Current',
            # 'GridVoltage',
            # 'InverterVoltage',
            # 'LoadVoltage',
            # 'GridCurrentL1',
            # 'InverterOutputCurrentL1',
            'AuxPower',
            # 'GridPowerTotal',
            'InverterPowerTotal',
            # 'LoadPowerTotal',
            # 'LoadCurrentL1',
            # 'LoadCurrentL2',
            # 'BatteryTemp',
            # 'BatteryVoltage',
            # 'BatterySOC',
            'BatteryPower',
            # 'BatteryCurrent',
            # 'InverterFrequency',
            # 'GridRelayStatus',
            # 'AuxRelayStatus',
        ]
    }

    if args.export:
        def rnd(x):
            return round(x * 100) / 100

        def writeValues(file, utc, values):
            secs = 60 * (utc // 60)
            file.write(time.strftime("%Y-%m-%d %H:%M", time.gmtime(secs)))
            file.write(',')
            file.write(",".join(str(rnd(v)) for v in values))
            file.write("\r\n")

        fieldMap = {}
        valueMap = {}
        fileMap = {}
        timeMap = {}
        for entry in log.entries:
            if entry.kind != Kind.data:
                continue
            if entry.domain is None:
                continue
            utc = entry.getUtc()
            fields = fieldMap.get(entry.domain.name)
            if fields is None:
                flt = outputFieldMap.get(entry.domain.name)
                if flt is None:
                    fields = entry.domain.fields
                else:
                    fields = list(filter(lambda f: f.name in flt, entry.domain.fields))
                fieldMap[entry.domain.name] = fields
                valueMap[entry.domain.name] = [f.getValue(entry.data) for f in fields]
                filename = entry.domain.name.replace('/', '.')
                filename = f"data/{filename}.csv"
                file = fileMap[entry.domain.name] = open(filename, "w")
                file.write("time,")
                file.write(",".join(f'"{f.name}"' for f in fields))
                file.write("\r\n")
            else:
                lastTime = timeMap.get(entry.domain.name, 0)
                values = valueMap[entry.domain.name]
                file = fileMap[entry.domain.name]
                try:
                    if utc // 60 == lastTime // 60:
                        for i, f in enumerate(fields):
                            # print(f"{i}, {f.name}")
                            values[i] = (values[i] + f.getValue(entry.data)) / 2
                    else:
                        if lastTime != 0:
                            writeValues(file, lastTime, values)
                        valueMap[entry.domain.name] = [f.getValue(entry.data) for f in fields]
                except:
                    raise
                    pass

            timeMap[entry.domain.name] = utc

        for d in fileMap:
            lastTime = timeMap[d]
            file = fileMap[d]
            values = valueMap[d]
            writeValues(file, lastTime, values)


if __name__ == "__main__":
    main()
