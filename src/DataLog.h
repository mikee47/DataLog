#pragma once

#include <Storage.h>
#include <IFS/TimeStamp.h>

/**
 * @brief Circular flash data logging
 *
 * Elements written out are kept small. Order as follows:
 *
 * - First entry in a block is 'start'
 * - 'domain' identifies a data source, should appear before any data16 records
 * - 'field' records are optional and identify fields in the domain's dataset
 * - 'data16' records contain actual data
 *
 * The 'start', 'domain' and 'field' records can be relatively large.
 * A 512K partition contains 128 flash pages, so perhaps 8 flash  pages per block
 * would be appropriate.
 *
 * For long-term data storage the log must be replicated to a server.
 * Perhaps the simplest way to do that is to include a callback which is invoked
 * after a full block has been written.
 * In the event of network issues, etc. the log can be read in full and block sequence
 * numbers used to resume replication. See DataLogReader.
 *
 * Endurance
 * ---------
 *
 * SPI flash (e.g. Winbond w25q32) is rated at > 100,000 cycles.
 *
 */
class DataLog
{
public:
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
	XX(domain, 4, "Qualifies following fields (e.g. name of device)")                                                  \
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
         * @brief A domain identifies a data set
         */
		struct Domain {
			static constexpr Kind kind{Kind::domain};
			using ID = uint16_t;
			ID id;		 ///< Identifier
			char name[]; ///< e.g. name of device, no NUL
		};
		static_assert(sizeof(Domain) == 2);

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
			Domain::ID domain; ///< Identifies which domain this data is for
			uint16_t reserved;
			uint8_t data[]; ///< Data follows in same order and size as fields
		};
		static_assert(sizeof(Data) == 8);
	};

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
		return blockSize != 0;
	}

	explicit operator bool() const
	{
		return isReady();
	}

	bool writeTime();
	/**
	 * @brief Write a domain record and return the allocated ID
	 *
	 * Use the domain ID in subsequent `writeField` calls.
	 */
	Entry::Domain::ID writeDomain(const String& name);

	/**
	 * @brief Write a Field entry describing one column of data
	 */
	bool writeField(uint16_t id, Entry::Field::Type type, uint8_t size, const String& name, bool variable = false);

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
	 *
	 * This stores a complete set of data for a given domain.
	 */
	bool writeData(uint16_t domain, const void* data, uint16_t length);

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

	/**
	 * @brief Get time in milliseconds, accounting for wrapping
	 */
	static SystemTime getSystemTime();

private:
	struct BlockInfo {
		unsigned number;
		uint32_t sequence;
	};

	Storage::Partition partition;
	BlockInfo startBlock{};  ///< Oldest block in the log (one with lowest sequence number)
	BlockInfo endBlock{};	///< Current write block
	uint32_t writeOffset{0}; ///< Write offset from start of log
	uint16_t blockSize{0};
	uint16_t totalBlocks{0};	 ///< Total number of blocks in partition
	static uint32_t prevTicks;   ///< Used by `getSystemTime` to identify wrapping
	static uint32_t highTicks;   ///< Microseconds overflow
	static uint16_t domainCount; ///< Used to assign domain IDs
};

String toString(DataLog::Entry::Kind kind);
