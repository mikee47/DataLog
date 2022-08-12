DataLog tools
=============

datalog.py
   Log file management and format conversion

Functions required
------------------

- Download blocks via HTTP
   - At or after given start sequence, i.e. 0 or undefined starts at first available block
   - Optionally include final partial block (will be less than block size)

- Parse blocks
   - Discard duplicates
   - Check ordering
   - Report missing blocks
   - Append to archive file
   - Keep status file

- Schema and value unit support
   - Describe domains, fields by name
   - Indicate scale, unit of measurement as per Sunsynk
   - Generate header files instead of using MAP(XX) macros

- Index (sqlite3 database)
   - Access records by date/time, domain, etc.

- Home Assistant (for example)
   - Create plugin to access data
   - Use schema to interpret data
   - Build data records as required, e.g. energy usage from low/high registers converted into kWh figure
   - Don't copy actual data into database unless necessary

- Convert from .csv into blocks
   - e.g. Tigo spreadsheets, Sunsynk logger data
   - More suitable archive format
   - Single source for all related data


Consider
--------

- Reduce block size to one flash sector
   - 12 byte header every 4096 is fine
   - Reduces minimum flash requirement to 2 flash sectors (8K) for low-throughput logs, e.g. critical system log
   - Multiple logs may be appropriate

- Split entries across block boundary
   - Reduce wasted space for large data records
   - Allows for larger data records. 16-bit overall size is still plenty.
   - Don't split info part (first few bytes) only data part
   - Use continuation flag bit. If encountered without preceding entry then discard.

