/*
 * Standard.cpp
 */

#include <SmingTest.h>
#include <DataLog.h>

class StandardTest : public TestGroup
{
public:
	StandardTest() : TestGroup(_F("Standard"))
	{
		auto part = Storage::findPartition(F("datalog1"));
		REQUIRE(part);
		log.init(part);
	}

	void execute() override
	{
		log.writeTime();

		int domain = log.writeDomain("Test");
		log.writeField<char[]>(0, "Startup");
		log.writeField<float>(1, "float1");
		log.writeField<double>(2, "double2");
		log.writeField<char[]>(3, "MoreInfo");

		struct __attribute__((packed)) Data {
			DataLog::Size var0;
			float float1;
			double double2;
			DataLog::Size var3;
			char extra[256];
		};

		DEFINE_FSTR_LOCAL(testString, "This is a variable-length char[] field for testing");
		DEFINE_FSTR_LOCAL(var3, "A second string");
		Data data{
			.var0 = DataLog::Size(testString.length()),
			.float1 = 3.14159,
			.double2 = -10000,
			.var3 = DataLog::Size(var3.length()),
		};
		size_t off = 0;
		off += testString.read(0, &data.extra[off], data.var0);
		off += var3.read(0, &data.extra[off], data.var3);
		log.writeData(domain, &data, offsetof(Data, extra) + off);
	}

private:
	DataLog log;
};

void REGISTER_TEST(Standard)
{
	registerGroup<StandardTest>();
}