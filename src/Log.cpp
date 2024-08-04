/**
 * Log.cpp
 *
 * Copyright 2022 mikee47 <mike@sillyhouse.net>
 *
 * This file is part of the DataLog Library
 *
 * This library is free software: you can redistribute it and/or modify it under the terms of the
 * GNU General Public License as published by the Free Software Foundation, version 3 or later.
 *
 * This library is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
 * without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with this library.
 * If not, see <https://www.gnu.org/licenses/>.
 *
 ****/

// #undef DEBUG_VERBOSE_LEVEL

#include "include/DataLog/Log.h"
#include <debug_progmem.h>
#include <esp_system.h>

namespace DataLog
{
namespace
{
constexpr uint32_t magic{0xA78BE044};

#ifdef DATALOG_FAST_TESTING
#define PAGES_PER_BLOCK 1
#define MAX_TOTAL_BLOCKS 4
#else
#define PAGES_PER_BLOCK 4
#endif

struct BlockStart {
	Entry::Header header;
	Entry::Block block;

	bool isValid() const
	{
		return header.size == sizeof(block) && header.kind == Entry::Kind::block && block.magic == magic;
	}
};

}; // namespace

uint16_t Log::tableCount;

bool Log::init(Storage::Partition partition)
{
	if(!partition) {
		return false;
	}

	this->partition = partition;
	blockSize = partition.getBlockSize() * PAGES_PER_BLOCK;
	if(blockSize == 0) {
		return false;
	}

	totalBlocks = partition.size() / blockSize;
#ifdef MAX_TOTAL_BLOCKS
	totalBlocks = std::min(totalBlocks, uint16_t(MAX_TOTAL_BLOCKS));
#endif

	// Read all block sequence numbers
	uint32_t sequences[totalBlocks]{};
	for(unsigned block = 0; block < totalBlocks; ++block) {
		BlockStart s{};
		partition.read(block * blockSize, &s, sizeof(s));
		debug_d("[DL] 0x%08x blk #%u seq %08x", block * blockSize, block, s.block.sequence);
		if(s.isValid()) {
			sequences[block] = s.block.sequence;
		}
	}

	// Find maximum block sequence
	endBlock = BlockInfo{};
	for(unsigned block = 0; block < totalBlocks; ++block) {
		auto seq = sequences[block];
		if(seq > endBlock.sequence) {
			endBlock = BlockInfo{block, seq};
		}
	}

	if(endBlock.sequence == 0) {
		// log is empty
		startBlock = endBlock;
		writeOffset = 0;
	} else {
		// Scan backwards to find start point
		auto block = endBlock;
		do {
			startBlock = block;
			if(block.sequence == 1) {
				break;
			}
			if(block.number == 0) {
				block.number = totalBlocks - 1;
			} else {
				--block.number;
			}
			--block.sequence;
		} while(block.sequence == sequences[block.number]);

		// Scan end block for write position
		writeOffset = endBlock.number * blockSize;
		auto endOffset = writeOffset + blockSize;
		do {
			Entry::Header header{};
			partition.read(writeOffset, &header, sizeof(header));
			if(header.kind == Entry::Kind::erased) {
				break;
			}
#if DEBUG_VERBOSE_LEVEL >= DBG
			m_printf(_F("0x%04x %s %u\r\n"), writeOffset, toString(header.kind).c_str(), header.size);
#endif
			writeOffset += ALIGNUP4(sizeof(header) + header.size);
		} while(writeOffset < endOffset);

		if(writeOffset > endOffset) {
			debug_w("[DL] End block %08x scan overflowed", endBlock.sequence);
			writeOffset = endOffset;
		}
	}

	debug_d("[DL] startBlock #%u seq %08x", startBlock.number, startBlock.sequence);
	debug_d("[DL] endBlock #%u seq %08x", endBlock.number, endBlock.sequence);
	debug_d("[DL] writeOffset = 0x%08x", writeOffset);

	state = State::ready;
	return true;
}

bool Log::writeBoot()
{
	Entry::Boot boot{
		.reason = uint8_t(system_get_rst_info()->reason),
	};
	return writeEntry(boot);
}

bool Log::writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength, const void* data, uint16_t dataLength)
{
	if(state == State::uninitialised) {
		return false;
	}

	if(state == State::busy) {
		// Assume we're being called from a crash handler which may fire in middle of flash write operation
		if(writeOffset % blockSize == 0) {
			// Start of new block so will get erased anyway
		} else {
			// writeOffset only gets updated after flash write operation
			// Skip block if the flash write was interrupted
			writeOffset %= blockSize * totalBlocks;
			Entry::Header header{};
			partition.read(writeOffset, &header, sizeof(header));
			if(header.kind != Entry::Kind::erased) {
				writeOffset += sizeof(header) + ALIGNUP4(header.size);
			}
		}
	}

	state = State::busy;
	auto entrySize = sizeof(Entry::Header) + infoLength + dataLength;
	auto space = blockSize - (writeOffset % blockSize);
	if(space < entrySize) {
		// No room in page, skip to next one
		Entry::Header header{
			.size = uint16_t(space - sizeof(Entry::Header)),
			.kind = Entry::Kind::pad,
			.flags = 0,
		};
		debug_d("[DL] Pad %u @ 0x%08x", header.size, writeOffset);
		partition.write(writeOffset, &header, sizeof(header));
		writeOffset += space;
	}

	if(writeOffset % blockSize == 0) {
		writeOffset %= blockSize * totalBlocks;
		endBlock.number = writeOffset / blockSize;
		++endBlock.sequence;
		if(endBlock.number == startBlock.number && startBlock.sequence != 0) {
			// Retire this block
			debug_d("[DL] Retire block #%u seq %08x", startBlock.number, startBlock.sequence);
			++startBlock.number;
			startBlock.number %= totalBlocks;
			++startBlock.sequence;
		}

		// Initialise the block
		debug_d("[DL] Initialise block #%u seq %08x @ 0x%08x", endBlock.number, endBlock.sequence, writeOffset);
		partition.erase_range(writeOffset, blockSize);
		BlockStart s{
			.header =
				{
					.size = uint16_t(sizeof(Entry::Block)),
					.kind = Entry::Kind::block,
					.flags = 0xff,
				},
			.block =
				{
					.magic = magic,
					.sequence = endBlock.sequence,
				},
		};
		partition.write(writeOffset, &s, sizeof(s));
		writeOffset += sizeof(s);
	}

	Entry::Header header{
		.size = uint16_t(infoLength + dataLength),
		.kind = kind,
		.flags = 0xff,
	};
	debug_d("[DL] > %s %u @ 0x%08x", toString(header.kind).c_str(), header.size, writeOffset);
	partition.write(writeOffset, &header, sizeof(header));
	partition.write(writeOffset + sizeof(header), info, infoLength);
	if(dataLength != 0) {
		partition.write(writeOffset + sizeof(header) + infoLength, data, dataLength);
	}
	header.flags[Entry::Flag::invalid] = false;
	partition.write(writeOffset, &header, sizeof(header));

	// Entries always start on a word boundary
	writeOffset += sizeof(header) + ALIGNUP4(header.size);

	state = State::ready;
	return true;
}

bool Log::writeTime()
{
	Entry::Time e{
		.systemTime = getSystemTime(),
		.time = {uint32_t(IFS::fsGetTimeUTC())},
	};
	return writeEntry(e);
}

int Log::read(uint16_t block, uint16_t offset, void* buffer, uint16_t bufSize)
{
	if(!isReady()) {
		return -1;
	}

	debug_d("[DL] read: block %u, offset %u, size %u", block, offset, bufSize);

	if(block > endBlock.sequence) {
		return -1;
	}

	uint32_t totalSize = totalBlocks * blockSize;
	uint32_t readOffset = (startBlock.number + block - startBlock.sequence) * blockSize + offset;
	if(readOffset >= totalSize) {
		readOffset -= totalSize;
	}

	uint32_t bytesRead{0};
	if(readOffset > writeOffset) {
		auto len = std::min(uint32_t(bufSize), totalSize - readOffset);
		debug_d("[DL] read 0x%08x, %u", readOffset, len);
		partition.read(readOffset, static_cast<uint8_t*>(buffer), len);
		bytesRead += len;
		readOffset = 0;
	}
	auto len = std::min(bufSize - bytesRead, writeOffset - readOffset);
	if(len != 0) {
		debug_d("[DL] read 0x%08x, %u", readOffset, len);
		partition.read(readOffset, static_cast<uint8_t*>(buffer) + bytesRead, len);
		bytesRead += len;
	}

	return bytesRead;
}

} // namespace DataLog
