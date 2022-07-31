#undef DEBUG_VERBOSE_LEVEL

#include "DataLog.h"
#include <Clock.h>
#include <debug_progmem.h>
#include <FlashString/Map.hpp>
#include <esp_system.h>

namespace
{
constexpr uint32_t magic{0xA78BE044};

#ifdef DATALOG_FAST_TESTING
#define PAGES_PER_BLOCK 1
#define MAX_TOTAL_BLOCKS 4
#else
#define PAGES_PER_BLOCK 4
#endif

#define XX(tag, value, ...) DEFINE_FSTR_LOCAL(str_##tag, #tag)
DATALOG_ENTRY_KIND_MAP(XX)
#undef XX

#define XX(tag, value, ...)                                                                                            \
	{                                                                                                                  \
		DataLog::Entry::Kind::tag,                                                                                     \
		&str_##tag,                                                                                                    \
	},
DEFINE_FSTR_MAP(kindTags, DataLog::Entry::Kind, FlashString, DATALOG_ENTRY_KIND_MAP(XX))
#undef XX

}; // namespace

uint32_t DataLog::prevTicks;
uint16_t DataLog::domainCount;

String toString(DataLog::Entry::Kind kind)
{
	return String(kindTags[kind]);
}

bool DataLog::init(Storage::Partition partition)
{
	isReady = false;
	this->partition = partition;
	blockSize = partition.getBlockSize() * PAGES_PER_BLOCK;
	totalBlocks = partition.size() / blockSize;
#ifdef MAX_TOTAL_BLOCKS
	totalBlocks = std::min(totalBlocks, uint16_t(MAX_TOTAL_BLOCKS));
#endif

	if(!partition || blockSize == 0) {
		return false;
	}

	/*
     * Make some assumptions for simplicity. We can improve this later:
     *
     * - The partition was initially fully erased 
     * - The log isn't full
     * - There are no invalid pages
     *
     * Scan sequence:
     * - inspect each block header to determine the oldest and newest block sequence numbers
     * - if there are no blocks marked, the log is empty
     * - block with the oldest number is the startBlock
     * - search last block to determine the write offset
     */
	blockCount = 0;
	writeOffset = 0;
	startBlock = BlockInfo{};
	endBlock = BlockInfo{};
	for(unsigned block = 0; block < totalBlocks; ++block) {
		struct S {
			Entry::Header header;
			Entry::Block block;
		};
		S s;
		partition.read(block * blockSize, &s, sizeof(s));
		if(s.header.kind != Entry::Kind::block) {
			// End of used blocks
			if(s.header.kind == Entry::Kind::erased) {
				break;
			}

			debug_e("[DL] Bad block entry");
			return false;
		}

		if(s.block.magic != magic) {
			debug_e("[DL] Bad block magic");
			return false;
		}

		debug_i("[DL] blk #%u seq %u @ 0x%08x", block, s.block.sequence, block * blockSize);

		++blockCount;
		if(s.block.sequence > endBlock.sequence) {
			endBlock.number = block;
			endBlock.sequence = s.block.sequence;
		}
		if(startBlock.sequence == 0 || s.block.sequence < startBlock.sequence) {
			startBlock.number = block;
			startBlock.sequence = s.block.sequence;
		}
	}

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

	debug_i("[DL] startBlock #%u seq %u", startBlock.number, startBlock.sequence);
	debug_i("[DL] endBlock #%u seq %u", endBlock.number, endBlock.sequence);
	debug_i("[DL] writeOffset = 0x%08x", writeOffset);

	isReady = true;
	Entry::Boot boot{
		.reason = uint8_t(system_get_rst_info()->reason),
	};
	writeEntry(boot);

	// All blocks read
	return true;
}

DataLog::SystemTime DataLog::getSystemTime()
{
	uint64_t ticks = micros();
	if(ticks < prevTicks) {
		ticks += 0xffffffffULL;
	}
	prevTicks = ticks;
	return ticks / 1000;
}

bool DataLog::writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength, const void* data, uint16_t dataLength)
{
	if(!isReady) {
		return false;
	}

	auto entrySize = sizeof(Entry::Header) + infoLength + dataLength;
	auto space = blockSize - (writeOffset % blockSize);
	if(space < entrySize) {
		// No room in page, skip to next one
		Entry::Header header{
			.size = uint16_t(space - sizeof(Entry::Header)),
			.kind = Entry::Kind::pad,
			.flags = 0,
		};
		debug_i("[DL] Pad %u @ 0x%08x", header.size, writeOffset);
		partition.write(writeOffset, &header, sizeof(header));
		writeOffset += space;
	}

	if(writeOffset % blockSize == 0) {
		writeOffset %= blockSize * totalBlocks;
		endBlock.number = writeOffset / blockSize;
		++endBlock.sequence;
		if(endBlock.number == startBlock.number) {
			if(startBlock.sequence == 0) {
				// Empty log
				assert(endBlock.number == 0 && endBlock.sequence == 1);
				startBlock = endBlock;
			} else {
				// Retire this block
				debug_i("[DL] Retire block #%u seq %u", startBlock.number, startBlock.sequence);
				++startBlock.number;
				startBlock.number %= totalBlocks;
				++startBlock.sequence;
				--blockCount;
			}
		}

		// Initialise the block
		debug_i("[DL] Initialise block #%u seq %u @ 0x%08x", endBlock.number, endBlock.sequence, writeOffset);
		partition.erase_range(writeOffset, blockSize);
		struct S {
			Entry::Header header;
			Entry::Block block;
		};
		S s{
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
		++blockCount;
	}

	Entry::Header header{
		.size = uint16_t(infoLength + dataLength),
		.kind = kind,
		.flags = 0xff,
	};
	debug_i("[DL] > %s %u @ 0x%08x", toString(header.kind).c_str(), header.size, writeOffset);
	partition.write(writeOffset, &header, sizeof(header));
	partition.write(writeOffset + sizeof(header), info, infoLength);
	partition.write(writeOffset + sizeof(header) + infoLength, data, dataLength);
	header.flags[Entry::Flag::invalid] = false;
	partition.write(writeOffset, &header, sizeof(header));
	writeOffset += sizeof(header) + infoLength + dataLength;

	// Entries always start on a word boundary
	writeOffset = ALIGNUP4(writeOffset);

	return true;
}

bool DataLog::writeTime()
{
	Entry::Time e{
		.systemTime = getSystemTime(),
		.time = {uint32_t(IFS::fsGetTimeUTC())},
	};
	return writeEntry(e);
}

DataLog::Entry::Domain::ID DataLog::writeDomain(const String& name)
{
	++domainCount;
	Entry::Domain e{
		.id = domainCount,
	};
	writeEntry(e, name);
	return e.id;
}

bool DataLog::writeField(uint16_t id, Entry::Field::Type type, uint8_t size, const String& name)
{
	Entry::Field e{
		.id = id,
		.type = type,
		.size = size,
	};
	return writeEntry(e, name);
}

bool DataLog::writeData(uint16_t domain, const void* data, uint16_t length)
{
	Entry::Data e{
		.systemTime = getSystemTime(),
		.domain = domain,
	};
	return writeEntry(e, data, length);
}

int DataLog::read(uint16_t block, uint16_t offset, void* buffer, uint16_t bufSize)
{
	debug_d("[DL] read: block %u, offset %u, size %u", block, offset, bufSize);

	if(offset >= blockSize || block < startBlock.sequence || block >= endBlock.sequence) {
		return -1;
	}

	uint32_t maxSize = (endBlock.sequence - block) * blockSize - offset;
	auto bytesToRead = std::min(uint32_t(bufSize), maxSize);
	auto bufptr = static_cast<uint8_t*>(buffer);
	uint32_t readOffset = (startBlock.number + block - startBlock.sequence) * blockSize + offset;
	uint32_t totalSize = totalBlocks * blockSize;
	uint32_t bytesRead{0};
	while(bytesRead < bytesToRead) {
		readOffset %= totalSize;
		auto len = std::min(bytesToRead - bytesRead, totalSize - readOffset);
		debug_d("[DL] read %u, %u", readOffset, len);
		partition.read(readOffset, bufptr, len);
		bufptr += len;
		bytesRead += len;
		readOffset += len;
	}

	return bytesRead;
}
