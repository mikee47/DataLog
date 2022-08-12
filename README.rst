DataLog
=======

Flexible data logging system for Sming.

This is a very basic example of a `Time series Database (TSDB) <https://en.wikipedia.org/wiki/Time_series_database>`.
It is intended to be write-only on the embedded system, with a more powerful computer reading the data back
and performing processing, etc.
It is therefore designed as a circular log for tagged binary data.

It implements a block-based circular log directly on a partition,
thus avoiding any filing system overhead.

Data is logged into 16K blocks which are tagged with a 32-bit sequence number.
The first block will have sequence #1.
Sequence numbers increment and are never re-used (unless the partition is erased).
When the last block in the partition has been filled, logging wraps back around
to the first block which is erased.

Typically a remote server (e.g. Raspberry Pi) will periodically retrieve the data
for archival, analysis, etc.
