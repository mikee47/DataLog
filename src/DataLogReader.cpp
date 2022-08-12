/**
 * DataLogReader.cpp
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

	debug_d("[DLR] READ block %u, offset %u, count %u (start %u, end %u, readPos %u)", block, offset, bufSize,
			log.getStartBlock(), log.getEndBlock(), readPos);

	int res = log.read(block, offset, data, bufSize);
	if(res <= 0) {
		done = true;
		res = 0;
	}

	return res;
}

int DataLogReader::seekFrom(int offset, SeekOrigin origin)
{
	debug_d("[DLR] SEEK offset %u, origin %u (readPos %u, size %u)", offset, origin, readPos, size);

	if(origin != SeekOrigin::Current) {
		return -1;
	}

	readPos += offset;
	return readPos;
}
