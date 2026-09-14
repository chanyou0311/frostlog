"""frostlog-gateway: the one process that holds the cooler's Bluetooth link.

The cooler (Anker Solix EverFrost 2) accepts a single central at a time and stops
advertising while it is connected, and the session key is negotiated per
connection. Everything that wants to read or write the cooler therefore goes
through this process: it streams what the cooler says and takes allow-listed
commands, over two Unix sockets. It writes nothing to disk -- what is worth
keeping is the collector's business, what is worth deciding is the controller's.
"""

MODEL = "everfrost"
