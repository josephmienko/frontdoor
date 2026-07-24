# Bridgewire access control

A simulator-first Python service for modernizing the Bridgewire door controller.
The domain code depends only on injected protocols; GPIO and reader hardware are
intentionally outside this first milestone.

## Quick start

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```console
uv sync --all-groups
uv run access-control version
uv run access-control check-config --config configs/local.yaml
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
docker compose up --build
```

On a new Windows development machine, the optional RP4 SSH setup script can
create the local `.env` file, configure a dedicated SSH key, install its public
key on the RP4, and verify key-based access:

```powershell
.\setup-rp4-ssh.ps1
```

The Compose service runs only configuration validation and cannot energize a relay.

## Safety boundary

Only simulated adapters exist in this revision. No GPIO, USB, serial, production
configuration, credentials, VPN material, or card database is included. The exit
request is handled independently of card-reader health and repository access.

## Hardware questions that must be answered before hardware adapters are added

- Reader transport and exact output (serial, HID keyboard, Wiegand, or other)
- Relay input voltage, contact behavior, and active polarity
- Whether the magnetic lock is fail-safe or fail-secure
- Whether egress is directly wired, software driven, or both
- Legacy OS/runtime, VPN setup, credential storage, and failure mechanism
- Lock and relay power supplies, and the sensitivity classification of card IDs

Unidentified boards must be inspected and measured before connection. A harmless
bench load must be used before any facility lock is connected.
