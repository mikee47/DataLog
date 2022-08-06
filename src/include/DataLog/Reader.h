/**
 * Reader.h
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

#pragma once

#include "Log.h"
#include <Data/Stream/DataSourceStream.h>

namespace DataLog
{
/**
 * @brief Class to stream raw data log contents
 *
 * Server needs to understand basic block format of logs, to identify sequence numbers.
 * It periodically requests the next block.
 * If it exists, it is returned.
 *
 * Error codes:
 *  - success
 *  - block no longer available
 *  - block not yet available
 * Returned status information gives first and last available blocks.
 *
 * There is a race condition whereby the first block is erased whilst being transferred.
 * This could be mitigated by calculating a hash before sending the block. If the hash
 * doesn't match the received data then the block is discarded.
 *
 * Alternatively, before each read confirm that the range requested isn't in a block which
 * is being actively written. If so, abort the transfer and return an error.
 *
 */
class Reader : public IDataSourceStream
{
public:
	Reader(Log& log, unsigned startBlock) : log(log), startBlock(startBlock)
	{
	}

	uint16_t readMemoryBlock(char* data, int bufSize) override;

	int seekFrom(int offset, SeekOrigin origin) override;

	bool isFinished() override
	{
		return done;
	}

	MimeType getMimeType() const override
	{
		return MimeType::BINARY;
	}

private:
	Log& log;
	uint16_t startBlock;
	uint32_t readPos{0};
	bool done{false};
};

} // namespace DataLog
