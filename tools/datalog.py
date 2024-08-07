#
#!/usr/bin/env python3
#
# DataLog.py: Script to manage data logs
#
# Copyright 2022 mikee47 <mike@sillyhouse.net>
#
# This file is part of the DataLog Library
#
# This library is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, version 3 or later.
#
# This library is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this library.
# If not, see <https://www.gnu.org/licenses/>.
#

import os, json, sys, struct, time, array, argparse, pickle, sqlite3, http.client, socket
from enum import IntEnum

verbose = False

FILE_DATALOG = "logs/datalog-%08x-%08x.bin"
FILE_NEXTSEQ = "logs/next.seq"
FILE_TAIL = "logs/tail.bin"


def printProperties(obj):
    print(f"Properties for {type(obj)}:")
    for prop, val in vars(obj).items():
        print(f"  {prop} = {val}")


class Kind(IntEnum):
    pad = 0,        # Unused padding
    block = 1,      # Identifies start of block
    boot = 2,       # System boot
    time = 3,       # Contains RTC value and corresponding system time
    table = 4,     # Qualifies following fields (e.g. name of device)
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
            Kind.table: Table,
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
                try:
                    entry = map[kind](content, ctx)
                except (UnicodeDecodeError, IndexError, struct.error, ValueError) as err:
                    entry = None
                    print(f"seq {block.sequence:#x} @{offset:#010x} {Kind(kind).name}, size {entrySize}, flags {flags}, {type(err).__name__}: {err}")
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
        return True


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
        self.time = None
        self.kind = Kind.boot
        self.reason = Boot.Reason(content[0])
        ctx.reset()

    def __str__(self):
        return f"reason {self.reason.name}"

    def fixup(self, ctx):
        if self.time is None:
            self.time = ctx.time.getUtc(0)
            return True
        return False

class Time(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.time
        if isinstance(content, dict):
            self.systemTime = content['systemTime']
            self.utc = content['utc']
        else:
            (self.systemTime, self.utc) = struct.unpack("<II", content)
            self.systemTime = ctx.checkTime(self.systemTime)

    def __str__(self):
        return "systemTime %u, %s" % (self.systemTime, timestr(self.utc))

    def dict(self):
        return dict(
            systemTime=self.systemTime,
            utc=self.utc,
        )

    def getUtc(self, systemTime):
        return self.utc + (systemTime - self.systemTime) / 1000


class Table(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.table
        self.fields = []
        self.fieldDataSize = 0
        ctx.table = self
        if isinstance(content, dict):
            self.id = content['id']
            self.name = content['name']
            for f in content['fields']:
                Field(f, ctx)
        else:
            (self.id,) = struct.unpack("<H", content[:2])
            self.name = content[2:].decode()
        ctx.tables[self.id] = self

    def __str__(self):
        return "id %u, name '%s'" % (self.id, self.name)

    def dict(self):
        return dict(
            id=self.id,
            name=self.name,
            fields=[field.dict() for field in self.fields],
        )


class Field(Entry):
    class Type(IntEnum):
        Unsigned = 0,
        Signed = 1,
        Float = 2,
        Char = 3,

    typemap = {
        (Type.Float, 4): ("float", "f", "REAL"),
        (Type.Float, 8): ("double", "d", "DOUBLE"),
        (Type.Unsigned, 1): ("uint8_t", "B", "TINYINT"),
        (Type.Unsigned, 2): ("uint16_t", "H", "SMALLINT"),
        (Type.Unsigned, 4): ("uint32_t", "I", "INT"),
        (Type.Unsigned, 8): ("uint64_t", "Q", "BIGINT"),
        (Type.Signed, 1): ("int8_t", "b", "TINYINT"),
        (Type.Signed, 2): ("int16_t", "h", "SMALLINT"),
        (Type.Signed, 4): ("int32_t", "i", "INT"),
        (Type.Signed, 8): ("int64_t", "q", "BIGINT"),
        (Type.Char, 1): ("char", "s", "TEXT"),
    }

    def __init__(self, content, ctx):
        self.kind = Kind.field
        self.table = ctx.table
        if isinstance(content, dict):
            self.id = content['id']
            self.name = content['name']
            self.type = Field.Type[content['type']]
            self.size = content['size']
            self.isVariable = content['isVariable']
        else:
            (self.id, type, self.size) = struct.unpack("<HBB", content[:4])
            if self.size == 0:
                raise ValueError('Bad field')
            self.type = Field.Type(type & 0x7f)
            self.isVariable = (type & 0x80) != 0
            self.name = content[4:].decode()
        if self.table is not None:
            self.offset = self.table.fieldDataSize
            self.table.fieldDataSize += 2 if self.isVariable else self.size
            self.table.fields.append(self)

    def typestr(self):
        t = Field.typemap.get((self.type, self.size))
        return t[0] if t else f"{self.type.name}{self.size*8}"

    def sqltype(self):
        t = Field.typemap.get((self.type, self.size))
        return t[2]

    def getValue(self, data):
        try:
            fmt = Field.typemap[(self.type, self.size)][1]
        except:
            print(f"Bad field type! type {self.type}, size {self.size}, name {self.name}, table {self.table}")
            return 0
        if not self.isVariable:
            try:
                (value,) = struct.unpack(f"<{fmt}", data[self.offset:self.offset+self.size])
            except:
                print("fmt:", fmt)
                print("offset", self.offset, ", size", self.size, ", len", len(data))
                print(self)
                return 0
            return value
        fieldLength = 0
        off = self.table.fieldDataSize
        value = None
        for f in self.table.fields:
            if not f.isVariable:
                continue
            (fieldLength,) = struct.unpack("<H", data[f.offset:f.offset+2])
            fieldLength *= f.size
            if f is self:
                if fmt == 's':
                    value = data[off:off+fieldLength].decode()
                else:
                    value = array.array(fmt, data[off:off+fieldLength])
                break
            off += fieldLength
        return value

    def __str__(self):
        s = "?" if self.table is None else self.table.name
        s += f", id {self.id}, {self.typestr()}, name '{self.name}'"
        return s

    def dict(self):
        return dict(
            id=self.id,
            name=self.name,
            type=self.type.name,
            size=self.size,
            isVariable=self.isVariable,
        )


class Data(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.data
        self.time = ctx.time
        (self.systemTime, self.table_id, self.reserved) = struct.unpack("<IHH", content[:8])
        self.table = ctx.tables.get(self.table_id)
        self.data = content[8:]
        self.systemTime = ctx.checkTime(self.systemTime)

    def getUtc(self):
        return self.time.getUtc(self.systemTime) if self.time else 0

    def __str__(self):
        s = f"systemTime {self.systemTime}"
        if self.time:
            s += f", {timestr(self.getUtc())}"
        if self.table is None:
            s += f", table {self.table_id}, {len(self.data)} bytes: "
            s += " ".join("%02x" % x for x in self.data)
        else:
            s += f", table {self.table}: "
            s += ", ".join(str(f.getValue(self.data)) for f in self.table.fields)
        return s

    def fixup(self, ctx):
        if self.time is None:
            self.time = ctx.time
            return True
        return False


class Exception(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.exception
        self.time = None
        (self.cause, self.epc1, self.epc2, self.epc3, self.excvaddr, self.depc) = struct.unpack("<6I", content[:24])
        self.stack = array.array("I", content[24:])

    def __str__(self):
        s = f"cause {self.cause:#010x}, epc1 {self.epc1:#010x}, epc2 {self.epc2:#010x}, epc3 {self.epc3:#010x}, excvaddr {self.excvaddr:#010x}, depc {self.depc:#010x}, stack {len(self.stack)}"
        if len(self.stack) > 0:
            s += "\r\n"
            s += ", ".join(f"{e:#010x}" for e in self.stack)
        return s

    def fixup(self, ctx):
        if self.time is None:
            self.time = ctx.time.getUtc(0)


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
            print(f"Block {b.sequence:#010x}, entrySize {b.size}, data size {len(data) - b.size}, {b.kind}, magic {b.magic:#010x}")
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

    def dict(self):
        return dict(

        )

    def isFull(self):
        return 4 + self.size + len(self.content) == Block.SIZE


class BlockList(dict):
    def loadFromFile(self, filename):
        f = open(filename, "rb")
        f.seek(0, os.SEEK_END)
        fileSize = f.tell()
        f.seek(0, os.SEEK_SET)
        ft = time.strftime("%x %X", time.gmtime(os.path.getmtime(filename)))
        if verbose:
            print(f"Scanning '{os.path.basename(filename)}', {fileSize} bytes, {(fileSize + Block.SIZE - 1) // Block.SIZE} blocks, {ft}")
        if fileSize % Block.SIZE != 0:
            print(f"WARNING! File '{os.path.basename(filename)}' size {fileSize:#x} is not a multiple of block size {Block.SIZE:#x}")

        dupes = 0
        blockCount = 0
        while True:
            pos = f.tell()
            data = f.read(Block.SIZE)
            if len(data) == 0:
                break
            block = Block.parse(data)
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
        self.lastBlockSequence = 0
        self.lastBlockLength = 0
        self.entries = []
        self.reset()

    def reset(self):
        self.time = None
        self.tables = {}
        self.table = None
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
        if block.sequence < self.lastBlockSequence:
            return
        if block.sequence == self.lastBlockSequence:
            off = self.lastBlockLength
        self.lastBlockSequence, self.lastBlockLength = block.sequence, len(block.content)

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
                    if not e.fixup(self) and e.kind == Kind.data:
                        break

            self.entries.append(entry)

    def saveContext(self, filename):
        context = dict(
            time=self.time.dict(),
            prevSystemTime=self.prevSystemTime,
            highTime=self.highTime,
            lastBlockSequence=self.lastBlockSequence,
            lastBlockLength=self.lastBlockLength,
            tables=[table.dict() for table in self.tables.values()],
        )
        with open(filename, "w") as f:
            json.dump(context, f, indent=2)

    def loadContext(self, filename):
        try:
            with open(filename, "r") as f:
                context = json.load(f)
            self.time = Time(context['time'], self)
            self.prevSystemTime = context['prevSystemTime']
            self.highTime = context['highTime']
            self.lastBlockSequence = context['lastBlockSequence']
            self.lastBlockLength = context['lastBlockLength']
            self.tables = {}
            for t in context['tables']:
                Table(t, self)
            if verbose:
                print(f"lastBlock = {self.lastBlockSequence:#x}, {self.lastBlockLength}")
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description='DataLog tool')
    parser.add_argument('input', nargs='*', help='Log file to read')
    parser.add_argument('--fetch', metavar='PATH', help='http path to datalog server')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--export', action='store_true', help='Export data to sqlite')
    parser.add_argument('--compact', action='store_true')

    global verbose

    args = parser.parse_args()
    verbose = args.verbose

    if args.compact:
        files = []
        for f in args.input:
            blocks = BlockList()
            blocks.loadFromFile(f)
            if len(blocks) != 0:
                files.append((f, blocks))

        endBlock = 0
        files.sort(key = lambda x: (min(x[1]) << 16) + max(x[1]))
        for f in files:
            filename = f[0]
            blocks = f[1]
            first = min(blocks)
            last = max(blocks)
            print(filename)
            while first <= endBlock and first <= last:
                if first in blocks:
                    del blocks[first]
                first += 1
            if last < endBlock:
                print("GAP!!!")
            if first > last:
                print("EMPTY!!")
            endBlock = last
            st = os.stat(filename)
            blocks.saveToFile(filename)
            os.utime(filename, (st.st_atime, st.st_mtime))
            newFileName = os.path.split(filename)[0] + f"/datalog-%08x-%08x.bin" % (first, last)
            if newFileName != filename:
                print(f"{filename} -> {newFileName}")
                os.rename(filename, newFileName)

        return


    blocks = BlockList()
    for f in args.input:
        blocks.loadFromFile(f)

    if len(blocks) == 0:
        lastBlock = 0
        print("No blocks loaded")
    else:
        seq = sorted(blocks.keys())
        print(f"{len(blocks)} blocks loaded ({seq[0]:#x} - {seq[len(seq)-1]:#x})")
        missing = []
        cur = seq[0]
        for n in seq[1:]:
            cur += 1
            while n != cur:
                missing.append(cur)
                cur += 1
        if missing:
            print(f"{len(missing)} blocks missing: ", ", ".join(hex(x) for x in missing))

    if args.fetch:
        print(f"FETCH {args.fetch}")
        server, path = args.fetch.split('/', 1)
        if os.path.exists(FILE_NEXTSEQ):
            with open(FILE_NEXTSEQ) as f:
                startBlock = int(f.read(), 0)
            print("startBlock %x" % startBlock)
        else:
            startBlock = 0

        attempt = 0
        while True:
            try:
                conn = http.client.HTTPConnection(server, timeout=10)
                conn.request("GET", f"/{path}?start={startBlock}")
                rsp = conn.getresponse()
                print(rsp.status, rsp.reason)
                data = rsp.read()
                print(f"{len(data)} bytes received")
                break
            except socket.timeout as e:
                attempt += 1
                print(f"{e}, attempt {attempt}")
                if attempt > 3:
                    raise

        startBlock = None
        endBlock = None
        if len(data) != 0:
            newBlockCount = 0
            off = 0
            while off < len(data):
                block = Block.parse(data[off:off+Block.SIZE])
                off += Block.SIZE
                if block is None:
                    continue
                if startBlock is None:
                    startBlock = block
                endBlock = block
                if verbose:
                    print(block)
                blocks.append(block)
                newBlockCount += 1
        if startBlock is None:
            print("No valid blocks received")
        else:
            endSequence = endBlock.sequence
            if endBlock.isFull():
                nextSequence = endSequence + 1
            else:
                nextSequence = endSequence
                endSequence -= 1
            tail = len(data) % Block.SIZE
            off = len(data) - tail
            if endSequence >= startBlock.sequence:
                filename = FILE_DATALOG % (startBlock.sequence, endSequence)
                with open(filename, "wb") as f:
                    f.write(data[:off])
            with open(FILE_TAIL, "wb") as f:
                f.write(data[off:])
            with open(FILE_NEXTSEQ, "w") as f:
                f.write(hex(nextSequence))
            print("tail %u, next %x" % (tail, nextSequence))
            

    if args.dump:
        log = DataLog()
        for b in sorted(blocks):
            log.loadBlock(blocks[b])
        print(f"{len(log.entries)} entries loaded")

        dataCount = 0

        def printData():
            if dataCount != 0:
                print(f"Kind.data x {dataCount}")

        for entry in log.entries:
            print(f"{entry.block.sequence:#x} @ {entry.blockOffset:#x} {entry.kind.name}: {entry}")
            if verbose and entry.kind == Kind.data and entry.table is not None:
                for f in entry.table.fields:
                    print(f"  {f.id:#5} {f.name} = {f.getValue(entry.data)}")

        printData()


    SYSTABLE_PREFIX = '__'
    SYSTABLE_NAME = SYSTABLE_PREFIX + 'datalog'

    if args.export:
        log = DataLog()
        log.loadContext('context.json')
        for b in sorted(blocks):
            log.loadBlock(blocks[b])
        print(f"{len(log.entries)} new entries loaded")

        tables = {}
        sysTables = []
        con = sqlite3.connect('datalog.db')
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table';")
        for r in cur:
            tableName = r[0]
            if tableName.startswith(SYSTABLE_PREFIX):
                sysTables.append(tableName)
            else:
                r = con.execute(f"SELECT max(utc) FROM [{tableName}];").fetchone()
                columns = [c[1] for c in con.execute(f"PRAGMA table_info('{tableName}');")]
                tableInfo = dict(utc=r[0], entry=None, columns=columns)
                tables[tableName] = tableInfo

        if SYSTABLE_NAME not in sysTables:
            con.execute(f"CREATE TABLE [{SYSTABLE_NAME}](utc DATETIME, kind TEXT, comment TEXT)")

        exportCount = 0
        skipCount = 0
        for entry in log.entries:
            if entry.kind == Kind.boot or entry.kind == Kind.exception:
                if entry.time is not None:
                    con.execute(f"INSERT INTO [{SYSTABLE_NAME}](utc, kind, comment) VALUES({entry.time}, ?, ?);", (entry.kind.name, str(entry)))
                continue
            if entry.kind != Kind.data or entry.table is None:
                continue

            tableName = entry.table.name
            fields = entry.table.fields
            tableInfo = tables.get(tableName)
            if tableInfo is None:
                # Create database table
                columnDefs = ",\n".join(f"  [{f.name}] {f.sqltype()}" for f in fields)
                stmt = f"CREATE TABLE [{tableName}] (\n  utc DATETIME PRIMARY KEY NOT NULL,\n{columnDefs});"
                print(stmt)
                con.execute(stmt)
                tableInfo = dict(utc=0, entry=entry.table, columns=[f.name for f in fields])
                tables[tableName] = tableInfo
                con.commit()
            elif entry.table != tableInfo['entry']:
                # Check for new fields and amend database table definition
                columns = tableInfo['columns']
                for f in fields:
                    if f.name in columns:
                        continue
                    stmt = f"ALTER TABLE [{tableName}] ADD COLUMN [{f.name}] {f.sqltype()};"
                    print(stmt)
                    con.execute(stmt)
                    columns.append(f.name)
                tableInfo['entry'] = entry.table

            utc =  entry.getUtc()
            if utc <= tables[tableName]['utc']:
                skipCount += 1
                continue

            columnNames = ", ".join(f"[{f.name}]" for f in fields)
            stmt = f"INSERT INTO [{tableName}](utc, {columnNames}) VALUES({utc}, {', '.join('?' for f in fields)});"
            values = tuple(f.getValue(entry.data) for f in fields)
            if verbose:
                print(stmt, list(values))
            try:
                con.execute(stmt, values)
                tables[tableName]['utc'] = entry.getUtc()
                exportCount += 1
            except (sqlite3.OperationalError) as err:
                print(err)
                print(stmt)
            except sqlite3.IntegrityError:
                pass

        con.commit()
        log.saveContext('context.json')

        print(f"{exportCount} entries exported")
        print(f"{skipCount} existing entries skipped")


if __name__ == "__main__":
    main()
