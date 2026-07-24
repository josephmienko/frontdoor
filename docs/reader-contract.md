# Reader identity, records, and health

The parser implements the ID Innovations ID-20LA ASCII output documented by
the manufacturer: `STX`, ten uppercase hexadecimal data characters, two
uppercase hexadecimal checksum characters, `CR`, `LF`, and `ETX`. The checksum
is the XOR of the five data bytes. Serial settings are 9600 baud, 8 data bits,
no parity, and one stop bit. Source:
[ID-3/12/20LA manual, v2.01](https://www.id-innovations.com/ID-3%2612%2620LA%28en%29%20V2.01%2020200526.pdf).

Decoding is strict ASCII. Empty, partial, overlong, badly framed, non-hex,
lowercase, checksum-invalid, and invalid-byte records cannot become
credentials. The stream parser retains a bounded partial record and can
return multiple complete records from one read.

Reader identity is injected. It can constrain a stable `/dev/serial/by-id`
path, VID/PID, optional USB serial number, and optional manufacturer/product
attributes. No placeholder USB identity is treated as authoritative.
Discovery distinguishes no match, one match, and ambiguity; unrelated serial
devices do not create a false identity failure.

The health lifecycle emits connecting, connected, not found, ambiguous, open
failed, read failed, OS disconnect, reconnect scheduled, repeatedly failed,
recovered, and record received events. Reconnect uses bounded exponential
backoff with injectable jitter and an interruptible wait. Silence while a
device remains connected is not a confirmed fault and never forces reconnect.
