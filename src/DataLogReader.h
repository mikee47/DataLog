#pragma once

#include "DataLog.h"
#include <Data/Stream/DataSourceStream.h>

/**
 * @brief Class to manage reading a data log.
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
class DataLogReader : public IDataSourceStream
{
public:
	DataLogReader(DataLog& log, unsigned startBlock, unsigned blockCount)
		: log(log), startBlock(startBlock), blockCount(blockCount), size(blockCount * log.getBlockSize())

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
	DataLog& log;
	uint16_t startBlock;
	uint16_t blockCount;
	uint16_t blockOffset{0};
	uint32_t readPos{0};
	uint32_t size;
	bool done{false};
};
