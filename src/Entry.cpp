/**
 * Entry.cpp
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

#include "include/DataLog/Entry.h"
#include <FlashString/Map.hpp>
#include <Clock.h>

namespace
{
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

String toString(DataLog::Entry::Kind kind)
{
	return String(kindTags[kind]);
}

namespace DataLog
{
SystemTime getSystemTime()
{
	static uint32_t prevTicks;
	static uint32_t highTicks;

	uint32_t ticks = micros();
	if(ticks < uint32_t(prevTicks)) {
		++highTicks;
	}
	prevTicks = ticks;

	return ((uint64_t(highTicks) << 32) + ticks) / 1000;
}

} // namespace DataLog
