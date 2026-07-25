# RP4 bench restoration

The post-OS bootstrap script restores a standalone Raspberry Pi 4 bench after
a clean operating-system installation. It does not touch disks, GPIO, serial
devices, SSH access, or production credentials.

## Preconditions

- Raspberry Pi OS/Debian Trixie, arm64
- Python 3.13 and its `venv` module
- a locally staged released Bridgewire wheel and its independently recorded SHA-256
- sanitized simulation configuration, authorization CSV, and JSON schema
- console or SSH access with `sudo`

Preview the operation on the Pi:

```bash
sudo bash scripts/bootstrap-rp4-bench.sh \
  --dry-run \
  --hostname frontdoor-bench \
  --wheel /tmp/bridgewire_access_control-<version>-py3-none-any.whl \
  --wheel-sha256 '<64-hex-character-checksum>' \
  --config ./configs/simulation.toml \
  --authorization ./configs/simulation-authorization.csv \
  --schema ./schemas/authorization-file/schema.json
```

Remove `--dry-run` to apply it. Add `--maintenance-upgrade` only for an
operator-approved maintenance upgrade. Add `--install-service` to install and
start the safe simulated systemd service. Use `--install-hardware-service`
only with an explicitly validated `relay.backend = "raspberry_pi"` bench
configuration. The two modes use the same unit name and cannot run together.

## Result and rollback

```text
/opt/bridgewire/
|-- current -> releases/<version>
|-- current-venv -> venvs/<version>
|-- releases/<version>/
|   `-- bridgewire_access_control-<version>-py3-none-any.whl
|-- venvs/<version>/
`-- shared/
    |-- authorization.csv
    |-- config.toml
    `-- schema.json
/var/lib/bridgewire/
```

The `bridgewire` system user has no login shell. Release files are root-owned,
and only the service group can read sanitized inputs. Re-running the command
repairs modes, refreshes the versioned environment, verifies the artifact, and
atomically refreshes both `current` links. Previous versions remain available
for rollback by atomically repointing both links.

The `serve-simulated` process handles SIGTERM/SIGINT and uses only the
simulated relay. Its unit prevents privilege escalation and makes the host
filesystem read-only except for Bridgewire state. It does not open GPIO or
serial hardware.

Run one finite simulation manually:

```bash
sudo -u bridgewire \
  /opt/bridgewire/current-venv/bin/bridgewire simulate \
  --config /opt/bridgewire/shared/config.toml \
  --authorization /opt/bridgewire/shared/authorization.csv \
  --schema /opt/bridgewire/shared/schema.json
```

OS recovery remains separate: set SD-first boot or remove/disconnect the USB
drive, boot the preserved microSD, and repair or re-image the HDD.
