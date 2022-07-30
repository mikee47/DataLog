#undef DEBUG_VERBOSE_LEVEL

#include "DataLogReader.h"
#include <debug_progmem.h>

uint16_t DataLogReader::readMemoryBlock(char* data, int bufSize)
{
	if(bufSize == 0) {
		return 0;
	}

	auto blockSize = log.getBlockSize();
	auto block = startBlock + readPos / blockSize;
	auto offset = readPos % blockSize;

	auto bytesToRead = std::min(uint32_t(bufSize), size - readPos);

	debug_d("[DLR] READ block %u, offset %u, count %u (start %u, end %u, readPos %u)", block, offset, bytesToRead,
			log.getStartBlock(), log.getEndBlock(), readPos);

	int res = log.read(block, offset, data, bytesToRead);
	if(res < 0) {
		done = true;
		return 0;
	}

	return res;
}

int DataLogReader::seekFrom(int offset, SeekOrigin origin)
{
	debug_d("[DLR] SEEK offset %u, origin %u (readPos %u, size %u)", offset, origin, readPos, size);

	size_t newPos;
	switch(origin) {
	case SeekOrigin::Start:
		newPos = offset;
		break;
	case SeekOrigin::Current:
		newPos = readPos + offset;
		break;
	case SeekOrigin::End:
		newPos = size + offset;
		break;
	default:
		return -1;
	}

	if(newPos > size) {
		return -1;
	}

	if(newPos == size) {
		done = true;
	}

	readPos = newPos;
	return readPos;
}
