DataLog Test
============

Application to perform integration tests for DataLog library.

Host
----

Running on Host::

    make flash run

To inspect::

    hexdump -C -s 0xba000 out/Host/debug/firmware/flash.bin

NB. 0xba000 is start of **datalog1** partition as reported via **make map**.


All architectures
-----------------

*except rp2040* - flash reading requires debug connection.

To pull the log out of flash into a separate file::

    make readpart PART=datalog1

To dump the contents of the log::

    python ../tools/datalog.py --dump out/Host/debug/datalog1.read.bin

substitute **Host** for **Esp8266**, etc.

To export data into sqlite3 database **datalog.db**:

    python ../tools/datalog.py --export out/Host/debug/datalog1.read.bin
