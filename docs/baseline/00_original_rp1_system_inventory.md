# Original RP1 System Inventory

This document records the behavior and configuration observed in the preserved
RP1 storage image. It is intended to guide behavioral replication, not to
preserve obsolete implementation or security weaknesses.

## Evidence and preservation

The original 7.4 GiB card was copied as a whole-device image on 2026-07-23.
Imaging completed without reported read errors, and the image size exactly
matched the source device.

```text
Image:  rp1-card-20260723.img
Size:   7,948,206,080 bytes
SHA256: ff529985ff0c731a5f62341b5b8a8fae2e97b5c1f6ced262b10bfea66b2a060a
```

The inventory was performed from the image through a read-only loop device.
The ext4 filesystem was mounted with journal recovery disabled. The original
card remained unmounted during inspection.

Secrets, private keys, card identifiers, cardholder names, and Slack endpoint
values were deliberately not copied into this document.

## Platform

| Item | Observed value |
|---|---|
| Operating system | Raspbian GNU/Linux 10 (Buster) |
| Hostname in image | `raspberrypi` |
| Live LAN name | `door.bridgewire.org` |
| Live/static address | `10.164.40.15/22` |
| Default gateway and DNS | `10.164.40.1` |
| Package architecture | Primarily `armhf`; `arm64` is also registered |
| Installed kernel module families | Raspberry Pi 6.6.20 and 6.6.31, v6/v7/v7l/v8 variants |
| Python runtime | Python 3.7 |
| Application package | `hodor_picontroller` 1.1 |

The exact Raspberry Pi board revision has not yet been established from the
image. The installed kernel variants alone are not sufficient evidence of the
physical model.

## Application layout

```text
/home/pi/hodor/
├── bw_cardkey.csv
├── events/
├── log/
├── scripts/
├── .hodor_lastslack
└── .hodor_slacker_config.yml

/home/pi/hodor_picontroller/
├── hodor_controller/watcher.py
├── hodor_slackbot/hodor_slacker.py
├── setup.py
└── unit_test_suite.py
```

The application runs directly from this source tree rather than from an
installed wheel. The launch scripts invoke `python3` with absolute paths.

## Startup and service behavior

### `hodor-watcher.service`

Purpose:

- Reads credentials from the USB serial reader.
- Reloads the authorization CSV for each presented credential.
- Writes local event and log records.
- Pulses the door relay after an authorized credential.

Configuration:

```text
After=network.target
WorkingDirectory=/home/pi/hodor
User=pi
Restart=always
ExecStart=/bin/bash /home/pi/hodor/scripts/run_hodor_watcher.sh
```

Effective application command:

```text
python3 /home/pi/hodor_picontroller/hodor_controller/watcher.py \
  --root /home/pi/hodor \
  --dev /dev/ttyUSB0
```

### `hodor-slacker.service`

Purpose:

- Polls the filesystem event queue.
- Sends each new event message to a configured Slack webhook.
- Records the last event it attempted to send.

Configuration:

```text
After=network.target
WorkingDirectory=/home/pi/hodor
User=pi
Restart=always
ExecStart=/bin/bash /home/pi/hodor/scripts/run_hodor_slacker.sh
```

Effective application command:

```text
python3 /home/pi/hodor_picontroller/hodor_slackbot/hodor_slacker.py \
  --root /home/pi/hodor
```

The process does not sleep while polling an empty event queue. If Slack returns
an error, the event is still recorded as seen and is not retried.

### Event retention

`/etc/cron.daily/hodor-cullevents` deletes `*.event` files older than 90 days.
The snapshot contained 1,432 event files.

### Watchdog

The system watchdog service is enabled, and the boot configuration enables the
hardware watchdog.

```text
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
```

This monitors basic system health and load. It does not directly monitor the
reader, relay, application services, or ability to process credentials.

## Reader interface

The reader is connected through an FTDI USB serial adapter.

| Setting | Observed value |
|---|---|
| Device | `/dev/ttyUSB0` |
| Baud rate | 9600 |
| Read timeout | 1 second |
| Framing | Line-oriented input |
| Character decoding | UTF-8 |
| Normalization | Trim whitespace, uppercase, remove STX and ETX |

The controller ignores empty input and a lone ETX byte. Stored card keys have
hyphens removed before indexing, while the presented key is otherwise compared
after the normalization above.

Kernel logs show repeated FTDI disconnect/reconnect cycles and USB I/O errors.
The controller has no explicit reconnect loop. An unhandled serial exception
can terminate the process, after which systemd restarts the service.

The exact reader make, model, output format, and FTDI USB vendor/product IDs
still need to be recorded from the physical installation.

## Authorization behavior

The authorization database is `/home/pi/hodor/bw_cardkey.csv`.

```text
KEY,NAME,ALLOW
```

The snapshot contained 451 data records:

- 126 with `ALLOW=y`
- 325 with `ALLOW=n`

For each credential:

1. Read one line from the serial port.
2. Normalize the received value.
3. Reload and parse the complete CSV.
4. Locate a matching `KEY`.
5. Grant access only when the corresponding `ALLOW` value, lowercased, is `y`.
6. Log and enqueue events for the recognition and grant/deny decision.
7. Pulse the relay for an allowed credential.

An unknown credential is logged and enqueued as an unrecognized-key event.

## Relay behavior

| Setting | Observed value |
|---|---|
| Pin numbering | Physical board numbering (`GPIO.BOARD`) |
| Output pin | Physical pin 16 |
| BCM equivalent | GPIO 23 |
| Active polarity | High |
| Pulse duration | 3 seconds |

The legacy implementation sets the pin high, sleeps for three seconds, and then
sets it low. It does not use `try/finally`, register shutdown handlers, call
GPIO cleanup, or explicitly recover the safe output state after an exception.
Restart logs contain repeated warnings that the GPIO channel is already in use.

No exit-request button handling exists in the application source. The actual
egress mechanism and whether it bypasses software must be established from the
physical wiring.

## Events, logs, and Slack

Each event is a JSON file with:

```text
message
localtime
utctime
```

Event filenames contain local timestamp, process ID, and a per-process sequence
number. Events and logs contain plaintext card identifiers and cardholder names.
Those same messages are forwarded to Slack.

The Slack sender constructs a `curl` command containing the webhook and prints
the complete command. Historical launcher logs therefore likely contain the
webhook secret. The webhook should be treated as compromised and rotated; its
value must not be imported into the replacement repository.

Observed permissions also need correction:

- The event directory is mode `0777`.
- The authorization CSV is mode `0755` and therefore world-readable.
- The `pi` user has passwordless sudo access.

The replacement must avoid recording raw card identifiers or names in routine
logs, event files, and third-party notifications.

## Observed operational history

- Application activity in the snapshot continues through 2025-12-31.
- Sanitized watcher logs contain 67 recognized-and-granted transactions.
- No deny or unknown-card messages appeared in the retained watcher log set.
- One empty/unreadable event produced a Slack-side read error.
- FTDI USB disconnects, reconnects, and flow-control errors recur in kernel
  logs.
- The ext4 filesystem was clean when imaged.

## Behavior to replicate

- FTDI serial input at 9600 baud.
- STX/ETX removal, trimming, and uppercase normalization.
- Authorization semantics equivalent to `KEY,NAME,ALLOW`.
- Explicit grant only for an affirmative allow value.
- Active-high three-second relay pulse on the equivalent output.
- Local event generation independent of network availability.
- Automatic recovery after reader or application failure.

## Legacy weaknesses not to replicate

- Raw credential and cardholder data in logs or Slack.
- Slack webhook values written into logs.
- World-readable authorization data or world-writable event storage.
- Relay control without exception-safe de-energization.
- Dependence on a volatile `/dev/ttyUSB0` name.
- Lost Slack notifications after transient delivery errors.
- Busy-loop filesystem polling.
- Application health inferred only from process existence.
- Obsolete OS and Python versions.

## Remaining physical and operational questions

- Raspberry Pi board model and revision.
- Reader make, model, transport, and exact byte-level output.
- FTDI vendor ID, product ID, serial number, and stable udev identity.
- Relay board model, input voltage, and electrical isolation.
- Whether physical pin 16 drives the relay directly or through another board.
- Magnetic-lock type: fail-safe or fail-secure.
- Relay contact selection and normal state.
- Lock and relay power supplies.
- Exit-request wiring and whether it operates independently of software.
- Expected behavior during power loss, reader loss, network loss, and process
  restart.
- Whether three seconds remains the desired unlock interval.

No facility lock should be connected to replacement hardware until these items
are verified and relay behavior has been tested with a harmless bench load.

## Recommended replication sequence

1. Preserve a second copy of the image and checksum in a restricted location.
2. Photograph, label, and electrically verify the RP1 wiring and peripherals.
3. Add characterization tests for serial framing and authorization behavior.
4. Implement a reconnecting FTDI serial adapter using a stable device identity.
5. Implement a relay adapter that defaults low and de-energizes in `finally`
   blocks, shutdown handlers, startup recovery, and error paths.
6. Add application-aware health checks without making network access a
   prerequisite for local door operation.
7. Replace raw identifiers in logs with irreversible, scoped tokens.
8. Add durable notification retries without blocking access decisions.
9. Bench-test all failure modes before any production connection.
