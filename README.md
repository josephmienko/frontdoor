# Bridgewire access control

This repository contains the first, entirely simulated vertical slice of the
replacement Bridgewire door controller. It strictly parses the documented
ID-20LA serial format, validates the legacy `KEY,NAME,ALLOW` authorization
shape, drives a non-blocking controller against a simulated BCM23 relay, emits
privacy-safe audit events, escalates suspicious activity, and models reader
discovery and recovery. It contains no Raspberry Pi GPIO adapter, live
credential data, notification endpoint, or remote-unlock feature.

## Quick start

Install Python 3.13 and
[Poetry 2.4](https://python-poetry.org/docs/#installation), then:

```powershell
poetry env use 3.13
poetry install --with dev
poetry run bridgewire version
poetry run bridgewire simulate --config configs/simulation.toml
```

Run the complete local quality gate:

```powershell
poetry run ruff format --check .
poetry run ruff check .
poetry run mypy .
poetry run pytest --cov=bridgewire --cov-branch --cov-report=term-missing
poetry build
```

Docker is optional and runs only the sanitized simulator:

```powershell
docker compose up --build
```

On a new Windows development machine, the optional RP4 SSH setup script can
create `.env`, configure a dedicated SSH key, install its public key on the
RP4, and verify key-based access:

```powershell
.\setup-rp4-ssh.ps1
```

The VS Code interpreter is `.venv/Scripts/python.exe`. If Poetry creates an
environment elsewhere, set `POETRY_VIRTUALENVS_IN_PROJECT=true`, remove only
the project environment, and rerun `poetry install`.

## Design and safety

Bridgewire is a modular monolith: the controller, authorization source, reader,
relay, audit sink, and notification queue communicate in-process through typed
ports. The reusable runtime and application boundary are described in
[architecture refactor through Increment 4](docs/architecture-refactor-increment-4.md).

The core depends on typed interfaces and an injected monotonic clock. Tests
never wait in real time. Startup explicitly commands BCM23 LOW before
credential handling; shutdown attempts LOW before cleanup. HIGH is requested
only for an authorized credential, for an original three-second deadline that
cannot be extended by subsequent credentials.

Reader inactivity is telemetry only. It is not treated as proof of failure.
Raw credential identifiers, names, secrets, and webhook values are excluded
from routine audit data.

See [the increment architecture](docs/increment-1-architecture.md),
[reader contract](docs/reader-contract.md), and
[requirements traceability](docs/requirements-traceability.md).

For a repeatable, simulation-safe Raspberry Pi 4 bench setup after installing
the OS, see [RP4 bench restoration](docs/rp4-bench-bootstrap.md). The bootstrap
validates the OS, architecture, Python version, and released wheel checksum;
it does not configure physical access-control hardware.

The deferred supervised-hardware deployment and validation work is recorded in
the [after-hours hardware-service checklist](docs/after-hours-hardware-service-checklist.md).

## Read-only API

The optional FastAPI adapter exposes `GET /health`, `GET /status`,
`GET /events`, and `GET /events/{event_id}`. It has no relay-control,
credential-injection, or authorization-administration routes. API
configuration is disabled and bound to `127.0.0.1` by default.

The concurrency and deployment boundaries are documented in the
[read-only API design](docs/read-only-api.md). The hardware service starts
Uvicorn only when `[api].enabled = true`; the default remains disabled and
loopback-only. The hardware host retains sole ownership of fail-secure relay
cleanup. API lifecycle failures are logged and reported as degraded while
local credential processing continues. Uvicorn startup and shutdown are
bounded to two and five seconds respectively; its daemon thread is a
last-resort process-exit escape hatch after relay cleanup. Authentication, TLS,
control routes, systemd, and deployment integration remain deferred.

## Contributions and releases

Use Conventional Commits, for example `feat: add simulated reader recovery`.
CI must pass before `main` can release. See [releasing](docs/releasing.md).
