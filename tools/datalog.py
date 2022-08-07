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

import os, json, sys, struct, time, array
import argparse
from enum import IntEnum
import http.client, socket
import sqlite3

verbose = False

FILE_DATALOG = "logs/datalog-%08x-%08x.bin"
FILE_NEXTSEQ = "logs/next.seq"
FILE_TAIL = "logs/tail.bin"


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
                # print(f"{str(Kind(kind))}, {entrySize}, {flags:#x}")
                try:
                    entry = map[kind](content, ctx)
                except (UnicodeDecodeError, IndexError, struct.error, ValueError) as err:
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


class Table(Entry):
    def __init__(self, content, ctx):
        self.kind = Kind.table
        (self.id,) = struct.unpack("<H", content[:2])
        self.name = content[2:].decode()
        self.fields = []
        self.dataEntryCount = 0
        self.fieldDataSize = 0
        ctx.tables[self.id] = self
        ctx.table = self

    def __str__(self):
        return "id %u, name '%s'" % (self.id, self.name)


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
        (self.id, type, self.size) = struct.unpack("<HBB", content[:4])
        if self.size == 0:
            raise ValueError('Bad field')
        self.type = type & 0x7f
        self.isVariable = (type & 0x80) != 0
        self.name = content[4:].decode()
        if self.table is not None:
            self.offset = self.table.fieldDataSize
            self.table.fieldDataSize += 2 if self.isVariable else self.size
            self.table.fields.append(self)

    def typestr(self):
        t = Field.typemap.get((self.type, self.size))
        return t[0] if t else f"{str(Field.Type(self.type))}{self.size*8}"

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
            (value,) = struct.unpack(f"<{fmt}", data[self.offset:self.offset+self.size])
            return value
        len = 0
        off = self.table.fieldDataSize
        value = None
        for f in self.table.fields:
            if not f.isVariable:
                continue
            (len,) = struct.unpack("<H", data[f.offset:f.offset+2])
            len *= f.size
            if f is self:
                if fmt == 's':
                    value = data[off:off+len].decode()
                else:
                    value = array.array(fmt, data[off:off+len])
                break
            off += len
        return value

    def __str__(self):
        s = "?" if self.table is None else self.table.name
        s += f", id {self.id}, {self.typestr()}, name '{self.name}'"
        return s


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
            print("WARNING! File size is not a multiple of block size")

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
            

    if args.dump or args.export:
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
            if verbose and entry.kind == Kind.data and entry.table is not None:
                for f in entry.table.fields:
                    print(f"  {f.id:#5} {f.name} = {f.getValue(entry.data)}")

        printData()


    if args.export:
        def getSqlName(s):
            return s.replace('/', '_')
        def getSqlFieldName(s):
            return f"field_{s}" if s[0].isnumeric() else s
        tables = set()
        con = sqlite3.connect('datalog.db')
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        while True:
            r = cur.fetchone()
            if r is None:
                break
            print(r)
            tables.add(r[0])

        for entry in log.entries:
            if entry.kind != Kind.data:
                continue
            if entry.table is None:
                continue

            tableName = getSqlName(entry.table.name)
            if tableName not in tables:
                fields = ", ".join(f"{getSqlFieldName(f.name)} {f.sqltype()}" for f in entry.table.fields)
                stmt = f"CREATE TABLE {tableName}(utc DATETIME PRIMARYKEY, {fields});"
                print(stmt)
                cur.execute(stmt)
                tables.add(tableName)
                con.commit()

            fieldNames = ", ".join(f"{getSqlFieldName(f.name)}" for f in entry.table.fields)
            stmt = f"INSERT INTO {getSqlName(entry.table.name)}(utc, {fieldNames}) VALUES({entry.getUtc()}, {', '.join('?' for f in entry.table.fields)});"
            values = tuple(f.getValue(entry.data) for f in entry.table.fields)
            if verbose:
                print(stmt, list(values))
            try:
                cur.execute(stmt, values)
            except sqlite3.OperationalError as err:
                print(err)

        con.commit()

if __name__ == "__main__":
    main()
