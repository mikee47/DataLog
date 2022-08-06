/**
 * Entry.h
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

#include <IFS/TimeStamp.h>
#include <Data/BitSet.h>

namespace DataLog
{
using TimeStamp = IFS::TimeStamp;

/**
 * @brief Milliseconds since last boot
 */
using SystemTime = uint32_t;

/**
 * @brief Variable-length data 
 */
using Size = uint16_t;

#define DATALOG_ENTRY_KIND_MAP(XX)                                                                                     \
	XX(pad, 0, "Unused padding")                                                                                       \
	XX(block, 1, "Identifies start of block")                                                                          \
	XX(boot, 2, "System boot")                                                                                         \
	XX(time, 3, "Contains RTC value and corresponding system time")                                                    \
	XX(table, 4, "Qualifies following fields (e.g. name of device)")                                                   \
	XX(field, 5, "Field identification record")                                                                        \
	XX(data, 6, "Data record")                                                                                         \
	XX(exception, 7, "Exception information")                                                                          \
	XX(map, 8, "Map of block sequence numbers")                                                                        \
	XX(erased, 0xff, "Erased")

/**
 * @brief Log entries are 32-bit word-aligned
 */
struct Entry {
	enum class Kind : uint8_t {
#define XX(tag, value, ...) tag = value,
		DATALOG_ENTRY_KIND_MAP(XX)
#undef XX
	};

	enum class Flag {
		invalid, ///< Cleared as final step of writing record
	};
	using Flags = BitSet<uint8_t, Flag>;

	/**
	 * @brief Header is exactly one word in size so it can be written atomically
	 */
	struct Header {
		Size size; ///< Size of content, excluding this header
		Kind kind;
		Flags flags;
	};

	/**
	 * @brief Written as the first entry in a flash block
	 *
	 * During initialisation the partition is scanned to determine the read start
	 * position, which is the block containing the lowest sequence number.
	 *
	 * The current write position is determined by finding the next block with the highest
	 * sequence number. The block might be full, in which case a new block is started.
	 *
	 */
	struct Block {
		static constexpr Kind kind{Kind::block};

		uint32_t magic;
		uint32_t sequence; ///< Always increments
	};

	/**
	 * @brief Map of blocks
	 */
	struct Map {
		static constexpr Kind kind{Kind::map};

		// uint32_t seq[]; ///< Sequence numbers of blocks
	};

	/**
	 * @brief System boot information
	 */
	struct Boot {
		static constexpr Kind kind{Kind::boot};

		uint8_t reason; ///< rst_reason
	};

	/**
	 * @brief Exception information
	 */
	struct Exception {
		static constexpr Kind kind{Kind::exception};

		uint32_t cause;
		uint32_t epc1;
		uint32_t epc2;
		uint32_t epc3;
		uint32_t excvaddr;
		uint32_t depc;
		uint8_t stack[];
	};

	/**
	 * @brief Written on restart, at midnight and when RTC clock is updated.
	 */
	struct Time {
		static constexpr Kind kind{Kind::time};

		SystemTime systemTime;
		TimeStamp time;
	};

	/**
	 * @brief A table identifies a data set
	 */
	struct Table {
		static constexpr Kind kind{Kind::table};
		using ID = uint16_t;
		ID id;		 ///< Identifier
		char name[]; ///< e.g. name of device, no NUL
	};
	static_assert(sizeof(Table) == 2);

	/**
	 * @brief A field descriptor
	 */
	struct Field {
		static constexpr Kind kind{Kind::field};

		using ID = uint16_t;
		enum class Type : uint8_t {
			Unsigned,
			Signed,
			Float,
			Char,
		};

		/**
		 * @brief Application-specific Identifier
		 *
		 * For example, modbus register number.
		 */
		ID id;

		/**
		 * @brief Base type of field
		 */
		Type type : 7;

		/**
		 * @brief Variable-length field flag
		 *
		 * Allows storage of array-type or variable-length values.
		 *
		 * When set, field contains actual length of data in bytes (as uint16_t).
		 * Data from all variable fields is stored sequentially after fixed portion.
		 */
		bool variable : 1;

		/**
		 * @brief Size of field in bytes
		 *
		 * For example, uint8_t stored as Type::Unsigned with size=1.
		 *
		 * With variable-length fields, this gives the size of each element in the array.
		 * For example, 
		 */
		uint8_t size;

		/**
		 * @brief Field name, no NUL
		 */
		char name[];
	};
	static_assert(sizeof(Field) == 4);

	/**
	 * @brief A set of data entries
	 */
	struct Data {
		static constexpr Kind kind{Kind::data};

		SystemTime systemTime;
		Table::ID table; ///< Identifies which table this data is for
		uint16_t reserved;
		uint8_t data[]; ///< Data follows in same order and size as fields
	};
	static_assert(sizeof(Data) == 8);
};

/**
 * @brief Get time in milliseconds, accounting for wrapping
 */
SystemTime getSystemTime();

} // namespace DataLog

String toString(DataLog::Entry::Kind kind);
