/**
 * Log.h
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

#include <Storage.h>
#include "Entry.h"

namespace DataLog
{
/**
 * @brief Circular flash data logging
 *
 * 
 * 
 * Elements written out are kept small. Order as follows:
 *
 * - 'table' identifies a data source
 * - 'field' entries identify table fields (columns) and their types
 * - 'data' records contain actual data
 *
 * The 'table' and 'field' records must appear together and in that order.
 * The application should write these on every system restart.
 * This accommodates updates to amend table structures if required.
 * Major changes would probably require a new table name, perhaps with version number.
 *
 * Entries are not permitted to straddle blocks. If an entry won't fit in the available
 * space then it's marked as 'padding' and a new block is started.
 *
 * Block size is fixed at 16K (4 flash pages) to reduce the impact of this padding.
 *
 * For long-term data storage the log must be replicated to a server. See `DataLogReader`.
 *
 * Endurance
 * ---------
 *
 * SPI flash (e.g. Winbond w25q32) is rated at > 100,000 cycles.
 *
 */
class Log
{
public:
	/**
     * @brief Initialise the log ready for writing
     *
     * Entire partition is treated as a FIFO.
     * When a block becomes full, the next is erased.
     * Requires entire partition to be initially blank.
     */
	bool init(Storage::Partition partition);

	bool isReady() const
	{
		return state == State::ready;
	}

	explicit operator bool() const
	{
		return isReady();
	}

	bool writeTime();

	/**
	 * @brief Write an Entry of any kind.
	 */
	bool writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength, const void* data, uint16_t dataLength);

	bool writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength)
	{
		return writeEntry(kind, info, infoLength, nullptr, 0);
	}

	bool writeEntry(Entry::Kind kind, const void* info, uint16_t infoLength, const String& data)
	{
		return writeEntry(kind, info, infoLength, data.c_str(), data.length());
	}

	template <typename Info, typename... Args>
	typename std::enable_if<std::is_class<Info>::value, bool>::type writeEntry(const Info& info, Args... args)
	{
		return writeEntry(Info::kind, &info, sizeof(info), args...);
	}

	bool writeBoot();

	int read(uint16_t block, uint16_t offset, void* buffer, uint16_t bufSize);

	uint16_t getBlockSize() const
	{
		return blockSize;
	}

	uint16_t getStartBlock() const
	{
		return startBlock.sequence;
	}

	uint16_t getEndBlock() const
	{
		return endBlock.sequence;
	}

	uint16_t getFullBlockCount() const
	{
		return endBlock.sequence - startBlock.sequence;
	}

	Entry::Table::ID allocateTableId()
	{
		++tableCount;
		return tableCount;
	}

private:
	enum class State {
		uninitialised,
		ready,
		busy,
	};

	struct BlockInfo {
		unsigned number;
		uint32_t sequence;
	};

	static uint16_t tableCount; ///< Used to assign table IDs
	Storage::Partition partition;
	BlockInfo startBlock{};  ///< Oldest block in the log (one with lowest sequence number)
	BlockInfo endBlock{};	///< Current write block
	uint32_t writeOffset{0}; ///< Write offset from start of log
	uint16_t blockSize{0};
	uint16_t totalBlocks{0}; ///< Total number of blocks in partition
	State state{State::uninitialised};
};

} // namespace DataLog

String toString(DataLog::Entry::Kind kind);
