DataLog Test
============

Application to perform integration tests for DataLog library.

Running on Host::

    make flash run

To inspect::

    hexdump -C -s 0xba000 out/Host/debug/firmware/flash.bin

NB. 0xba000 is start of **datalog1** partition as reported via **make map**.

To pull the log out of flash backing store into a separate file::

    dd bs=4096 skip=$((0xba)) count=64 if=out/Host/debug/firmware/flash.bin of=log.bin

To dump the contents of the log::

    python ../tools/datalog.py log.bin --dump

