# After-hours hardware-service deployment checklist

This checklist records the safe stopping point on 2026-07-25 and the work to
resume only during an approved period when the production door is not needed.

## Current stopping point

- Bridgewire 1.2.1 is committed, tagged, pushed, built, and published.
- The supervised hardware composition root, physical BCM23 adapter, CH340
  serial adapter, SQLite audit repository, health file, and systemd deployment
  mode are implemented.
- Local gates passed: 161 tests, 90.01% branch coverage, Ruff, and strict mypy.
- The standalone bench checks previously proved strict ID-20LA parsing,
  authorization, a three-second BCM23 pulse, relay operation, and reader
  unplug/reconnect.
- Deployment stopped before changing the service because the CH340 was removed.
- At the stopping point, the Pi still ran the simulated service and BCM23 was
  input/LOW. The production reader/relay assembly was returned to door service.

Release artifacts:

- `bridgewire_access_control-1.2.1-py3-none-any.whl`
  - SHA-256: `a383f5658d676846dd9f408270cdf6b1f04bfc9fb587930cf54cf71117a184d8`
- `bridgewire_access_control-1.2.1.tar.gz`
  - SHA-256: `d8ebc3e9f0ab5edd74e85efbf94b2be1720e32a3281a80a4fa2d753b1cb34da3`

## Preconditions

1. Confirm the maintenance window and that the door may be unavailable.
2. Move the reader and relay assembly back to the isolated bench.
3. Keep the X825 external 5 V supply connected.
4. Confirm console/microSD recovery remains available.
5. Confirm COM, NO, BCM23, ground, and relay power wiring before enabling the
   hardware service.
6. Confirm exactly one CH340 `1a86:7523` adapter is connected. Its current
   stable path is `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`; it has no
   device-specific serial number, so zero and multiple matches must be rejected.

## Deployment

1. Verify the Pi is booted from `/dev/sda2`, Trixie is healthy, no throttling
   is current, SMART passes, and BCM23 is LOW.
2. Verify the enrolled authorization CSV remains root-owned, group-readable
   only by `bridgewire`, and passes schema validation.
3. Re-run the complete local quality gate and verify the wheel checksum.
4. Stage the 1.2.1 wheel, bootstrap script, hardware configuration, and schema.
5. Make a root-only temporary copy of the enrolled authorization CSV.
6. Stop the simulated `bridgewire.service`; verify BCM23 remains LOW.
7. Run `bootstrap-rp4-bench.sh --install-hardware-service` with:
   - the 1.2.1 wheel and recorded checksum;
   - `configs/hardware-bench.toml`;
   - the protected authorization copy;
   - the matching schema.
8. Remove the temporary authorization copy after successful installation.

Do not run a standalone reader diagnostic concurrently with the hardware
service.

## Definition-of-done checks

Record evidence for each item:

1. Safe startup: BCM23 is output/LOW before the reader is reported connected.
2. Health: `/run/bridgewire/health.json` reaches `ready`.
3. Full path: an enrolled credential produces controller authorization,
   `relay_asserted`, physical COM-NO closure, and `relay_restored`.
4. Non-blocking behavior: reader and audit events continue during the
   three-second release, and duplicate authorization does not extend its
   deadline.
5. Persistence: SQLite contains authorized, asserted, restored, denied,
   malformed, and reader-state events across restart.
6. Privacy: neither journald nor SQLite contains raw identifiers or names.
7. Reader recovery: unplug/reconnect succeeds through the same path without a
   reboot.
8. Safe stop: `systemctl stop bridgewire` immediately commands LOW before GPIO
   cleanup; COM-NO opens.
9. Restart recovery: restart establishes LOW, reloads authorization, reopens
   SQLite, reconnects the reader, and returns health to `ready`.
10. Reboot recovery: repeat the safe-start and ready checks after reboot.

Only after these pass should the remaining physical failure matrix be run:
unauthorized, malformed, invalid checksum, duplicate during release,
unauthorized during release, unplug during release, SIGTERM during release,
controller exception, restart, and GPIO initialization failure.

## Rollback

If any check fails:

1. Stop `bridgewire.service` and physically confirm BCM23 is 0 V and COM-NO is
   open.
2. Do not restart repeatedly against ambiguous reader identity or uncertain
   relay state.
3. Restore the simulated service with the 1.2.1 wheel and simulation config, or
   repoint both versioned release symlinks to the last known-good release.
4. If the HDD path is suspect, disconnect USB storage and boot the preserved
   microSD recovery system.
5. Return the original production unit to service only under the established
   manual procedure.
