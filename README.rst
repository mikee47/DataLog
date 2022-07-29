DataLog
=======

Flexible data logging system for Sming.

This library is designed to log any kind of data efficiently.
It implements a block-based circular log directly on a partition,
thus avoiding any filing system overhead.

Data is logged into 16K blocks which are tagged with a 32-bit sequence number.
The first block will have sequence #1.
Sequence numbers increment and are never re-used.
When the last block in the partition has been filled, logging wraps back around
to the first block which is erased.

Typically a remote server (e.g. Raspberry Pi) will periodically retrieve the data
for archival, analysis, etc.
