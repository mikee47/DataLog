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

struct BlockStart {
	DataLog::Entry::Header header;
	DataLog::Entry::Block block;

	bool isValid() const
	{
		return header.size == sizeof(block) && header.kind == DataLog::Entry::Kind::block && block.magic == magic;
	}
};

}; // namespace

uint32_t DataLog::prevTicks;
uint32_t DataLog::highTicks;
uint16_t DataLog::domainCount;

String toString(DataLog::Entry::Kind kind)
{
	return String(kindTags[kind]);
}

bool DataLog::init(Storage::Partition partition)
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
		debug_i("[DL] 0x%08x blk #%u seq %08x", block * blockSize, block, s.block.sequence);
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

	debug_i("[DL] startBlock #%u seq %08x", startBlock.number, startBlock.sequence);
	debug_i("[DL] endBlock #%u seq %08x", endBlock.number, endBlock.sequence);
	debug_i("[DL] writeOffset = 0x%08x", writeOffset);

	writeEntry(Entry::Kind::map, sequences, totalBlocks * sizeof(sequences[0]));

	Entry::Boot boot{
		.reason = uint8_t(system_get_rst_info()->reason),
	};
	writeEntry(boot);

	return true;
}

DataLog::SystemTime DataLog::getSystemTime()
{
	uint32_t ticks = micros();
	if(ticks < uint32_t(prevTicks)) {
		++highTicks;
	}
	prevTicks = ticks;

	return ((uint64_t(highTicks) << 32) + ticks) / 1000;
}

bool DataLog::writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength, const void* data, uint16_t dataLength)
{
	if(!isReady()) {
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
		if(endBlock.number == startBlock.number && startBlock.sequence != 0) {
			// Retire this block
			debug_i("[DL] Retire block #%u seq %08x", startBlock.number, startBlock.sequence);
			++startBlock.number;
			startBlock.number %= totalBlocks;
			++startBlock.sequence;
		}

		// Initialise the block
		debug_i("[DL] Initialise block #%u seq %08x @ 0x%08x", endBlock.number, endBlock.sequence, writeOffset);
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
	if(!isReady()) {
		return -1;
	}

	debug_d("[DL] read: block %u, offset %u, size %u", block, offset, bufSize);

	uint32_t totalSize = totalBlocks * blockSize;
	uint32_t readOffset = (startBlock.number + block - startBlock.sequence) * blockSize + offset;
	if(readOffset >= totalSize) {
		readOffset -= totalSize;
	}

	uint32_t bytesRead{0};
	if(readOffset > writeOffset) {
		auto len = std::min(uint32_t(bufSize), totalSize - readOffset);
		debug_i("[DL] read %u, %u", readOffset, len);
		partition.read(readOffset, static_cast<uint8_t*>(buffer), len);
		bytesRead += len;
		readOffset = 0;
	}
	auto len = std::min(bufSize - bytesRead, writeOffset - readOffset);
	if(len != 0) {
		debug_i("[DL] read %u, %u", readOffset, len);
		partition.read(readOffset, static_cast<uint8_t*>(buffer) + bytesRead, len);
		bytesRead += len;
	}

	return bytesRead;
}
