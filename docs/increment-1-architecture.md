# Increment 1 architecture

This increment is a single importable `bridgewire` package split by domain
boundary rather than empty deployment scaffolding.

| Module | Responsibility |
|---|---|
| `reader` | strict framing, discovery, identity matching, health events, bounded reconnect |
| `authorization` | exact CSV/schema validation, immutable lookup, atomic replacement |
| `controller` | non-blocking access state machine and actuation-result distinction |
| `gpio` | narrow relay protocol and timestamped simulator |
| `audit` | typed privacy-safe events and durable notification queue |
| `escalation` | clock-driven warning/critical windows and quiet reset |
| `configuration` | strict TOML configuration contract |
| `simulation` | sanitized end-to-end scenario |

Dependencies point inward through protocols in `interfaces.py`. The controller
has no serial, OS device, network, or Raspberry Pi import. A `ManualClock`
makes deadlines and reconnect waits deterministic. Reader events and access
audit events are separate typed streams because they describe different
lifecycles.

The approved physical contract is represented but not energized: BCM
numbering, channel 23, normal LOW, release HIGH, three seconds. Independent
hardwired egress described in the baseline is not modified or simulated.

Deferred to the Pi 4 bench increment: real serial enumeration/opening, a GPIO
adapter, systemd packaging, hardware-in-the-loop tests, watchdog integration,
and delivery of queued notifications. These are intentionally outside the
simulated increment.
