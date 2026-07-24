# Existing Software Architecture

**System:** Original RP1 Hodor access-control application  
**Version:** 2.0  
**Updated:** 2026-07-24  
**Runtime:** Python 3.7 on Raspberry Pi Linux  
**Authoritative baseline:** Preserved RP1 backup

## Software architecture diagram

```mermaid
flowchart TB
    Reader["ID-20LA reader contract<br/>USB serial<br/>/dev/ttyUSB0<br/>9600 baud, 8N1"]

    subgraph WatcherProcess["hodor-watcher process"]
        Serial["Serial adapter<br/>open once; readline(100)"]
        Normalize["Credential normalization<br/>UTF-8, trim, uppercase, remove STX/ETX"]
        ReloadCSV["CSV loader<br/>reopen full file for each credential"]
        Decision["Authorization decision<br/>KEY match and ALLOW=y"]
        EventWriter["Plaintext event writer"]
        WatcherLog["Watcher logging"]
        GPIO["RPi.GPIO<br/>BOARD pin 16 / BCM23"]
        Pulse["Blocking HIGH → sleep(3) → LOW"]
    end

    Auth["/home/pi/hodor/bw_cardkey.csv"]
    EventDir["/home/pi/hodor/events"]
    WatcherLogs["hodor_watcher.log<br/>hodor_run_watcher.log"]

    subgraph SlackerProcess["hodor-slacker process"]
        Poller["Busy-poll event directory"]
        Parser["Parse event records"]
        Curl["Invoke curl<br/>no connection or total timeout"]
        Pointer["Advance last-seen pointer"]
        SlackerLog["Slack logging"]
    end

    SlackConfig[".hodor_slacker_config.yml"]
    SlackerLogs["hodor_slacklog.log<br/>hodor_run_slacker.log"]
    Slack["Slack webhook"]

    Systemd["systemd"]
    Logrotate["logrotate<br/>daily; 9 rotations;<br/>restarts services"]
    EventCull["daily 90-day event cleanup"]
    Watchdog["Hardware watchdog<br/>not application-aware"]
    Relay["Custom relay board"]
    ExitHardware["Independent hardware exit path"]

    Systemd --> WatcherProcess
    Systemd --> SlackerProcess

    Reader --> Serial --> Normalize --> ReloadCSV --> Decision
    Auth --> ReloadCSV
    Decision --> EventWriter --> EventDir
    Decision --> WatcherLog --> WatcherLogs
    Decision --> GPIO --> Pulse --> Relay

    EventDir --> Poller --> Parser --> Curl --> Slack
    SlackConfig --> Curl
    Parser --> Pointer
    SlackerLog --> SlackerLogs

    Logrotate --> WatcherLogs
    Logrotate --> SlackerLogs
    Logrotate -->|"service restart"| Systemd
    EventCull --> EventDir
    Watchdog -.-> WatcherProcess

    ExitHardware -.->|"not represented in software"| Relay
```

## Process responsibilities

### `hodor-watcher`

The watcher:

- configures `RPi.GPIO` in BOARD mode;
- configures physical pin 16 as an output;
- opens `/dev/ttyUSB0` at 9600 baud with a one-second timeout;
- reads records with `readline(100)`;
- normalizes credential text;
- reloads the authorization CSV for each credential;
- writes recognized, granted, denied, and unknown events;
- executes a blocking three-second active-high GPIO pulse.

It has no in-process reconnect loop, reader rediscovery, health threshold, retry policy, backoff, signal handler, or exception-safe GPIO restoration.

### `hodor-slacker`

The Slack process polls the event directory and sends new events through `curl`. Local access decisions do not depend on Slack or network availability.

## Relationship between software and observed hardware failures

| Physical failure | Software behavior | Physical result |
|---|---|---|
| Pi loses power | All software stops | Exit remains usable; RFID entry unavailable |
| Reader disconnected | No usable credentials reach watcher; watcher may fail or remain ineffective depending on device behavior | Exit remains usable; RFID entry unavailable |
| GPIO conductor open | Reader and watcher can continue reading, authorizing, and logging | Authorized credentials have no effect on the locks |
| 12 V supply lost | Pi, reader, and watcher may continue operating | Electronic locks are de-energized; software commands have no physical effect |
| Facility AC lost | Software continues while UPS reserve remains | Normal operation for approximately 5–10 minutes |

## Key limitations

- Reader identity is tied to hard-coded `/dev/ttyUSB0`.
- Process existence is treated as a proxy for reader health.
- Empty reads are ignored indefinitely.
- No reader authentication or cryptographic credential validation exists.
- No checksum or fixed-length validation is performed.
- Blocking `sleep(3)` pauses credential processing.
- Abnormal termination does not guarantee GPIO LOW.
- Sensitive names, identifiers, and endpoint information may be exposed through logs and permissive filesystem modes.

## Baseline assumption

Because the live Pi cannot be inspected logically, the preserved backup is the accepted software starting point. Physical observations are used to validate externally visible behavior, but undocumented live-file differences are treated as unknowable rather than left as an open migration dependency.
