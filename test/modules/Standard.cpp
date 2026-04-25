/*
 * Standard.cpp
 */

#include <SmingTest.h>
#include <DataLog.h>
#include <Services/Profiling/MinMaxTimes.h>

class StandardTest : public TestGroup
{
public:
	struct __attribute__((packed)) Blob {
		int32_t a;
		uint8_t b;
	};

	StandardTest() : TestGroup(_F("Standard")), table(log), times(F("Log Entry"))
	{
		auto part = Storage::findPartition(F("datalog1"));
		REQUIRE(part);
		log.init(part);
	}

	void execute() override
	{
		const int rounds = 256;

		/*
		 * Create table header.
		 * Applications should only need to do this *once* for each table at startup.
		 */
		times.start();
		table.writeTable("Test");
		table.writeField<char[]>(0, "Startup");
		table.writeField<float>(1, "float1");
		table.writeField<double>(2, "double2");
		table.writeField<char[]>(3, "MoreInfo");
		table.writeField<Blob>(4, "Blob");
		table.writeField<Blob[]>(5, "BlobArray");
		log.writeTime();
		times.update();

		/*
		 * Now write some entries 
		 */
		timer.initializeMs<10>([this] {
			times.start();
			logEntry(table);
			times.update();

			++round;
			if(round < rounds) {
				return;
			}

			timer.stop();
			Serial << times << endl;
			complete();
		});
		timer.start();
		pending();
	}

	void __noinline logEntry(DataLog::Table& table)
	{
		struct __attribute__((packed)) Data {
			DataLog::Size var0_length;
			float float1;
			double double2;
			DataLog::Size var3_length;
			Blob blob;
			DataLog::Size blob_array_length;
			char extra[256];
		};

		DEFINE_FSTR_LOCAL(testString, "This is a variable-length char[] field for testing");
		DEFINE_FSTR_LOCAL(var3, "A second string");
		Data data{
			.var0_length = DataLog::Size(testString.length()),
			.float1 = 3.14159,
			.double2 = -10000,
			.var3_length = DataLog::Size(var3.length()),
			.blob = {1, 2},
			.blob_array_length = 2,
		};
		size_t off = 0;
		off += testString.read(0, &data.extra[off], data.var0_length * sizeof(char));
		off += var3.read(0, &data.extra[off], data.var3_length * sizeof(char));
		Blob arr[2] {
			{3, 4},
			{5, 6},
		};
		memcpy(&data.extra[off], arr, sizeof(arr));
		off += sizeof(arr);
		table.writeData(&data, offsetof(Data, extra) + off);
	}

private:
	DataLog::Log log;
	Profiling::MicroTimes times;
	DataLog::Table table;
	Timer timer;
	unsigned round{0};
};

void REGISTER_TEST(Standard)
{
	registerGroup<StandardTest>();
}
