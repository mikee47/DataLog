/**
 * Table.h
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

namespace DataLog
{
/**
 * @brief Manage table as a table
 */
class Table
{
public:
	Table(Log& log) : log(log), id(log.allocateTableId())
	{
	}

	/**
	 * @brief Write a table record
	 *
	 * This method is used by the application to periodically refresh the table and field
	 * information.
	 */
	bool writeTable(const String& name)
	{
		Entry::Table e{
			.id = id,
		};
		return log.writeEntry(e, name);
	}

	/**
	 * @brief Write a Field entry describing one column of data
	 */
	bool writeField(uint16_t id, Entry::Field::Type type, uint8_t size, const String& name, bool variable = false)
	{
		Entry::Field e{
			.id = id,
			.type = type,
			.variable = variable,
			.size = size,
		};
		return log.writeEntry(e, name);
	}

	template <typename T>
	typename std::enable_if<!std::is_floating_point<T>::value && std::is_unsigned<T>::value, bool>::type
	writeField(uint16_t id, const String& name)
	{
		return writeField(id, Entry::Field::Type::Unsigned, sizeof(T), name);
	}

	template <typename T>
	typename std::enable_if<!std::is_floating_point<T>::value && std::is_signed<T>::value, bool>::type
	writeField(uint16_t id, const String& name)
	{
		return writeField(id, Entry::Field::Type::Signed, sizeof(T), name);
	}

	template <typename T>
	typename std::enable_if<std::is_floating_point<T>::value, bool>::type writeField(uint16_t id, const String& name)
	{
		return writeField(id, Entry::Field::Type::Float, sizeof(T), name);
	}

	template <typename T>
	typename std::enable_if<std::is_same<T, char[]>::value, bool>::type writeField(uint16_t id, const String& name)
	{
		return writeField(id, Entry::Field::Type::Char, sizeof(char), name, true);
	}

	/**
	 * @brief Write a Data entry record
	 * @param data A complete row for this table
	 * @param length Size in bytes of the data
	 */
	bool writeData(const void* data, uint16_t length)
	{
		Entry::Data e{
			.systemTime = getSystemTime(),
			.table = id,
		};
		return log.writeEntry(e, data, length);
	}

private:
	Log& log;
	Entry::Table::ID id;
};

} // namespace DataLog
