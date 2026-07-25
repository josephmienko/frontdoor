# Increment 1 requirements traceability

The normative source is
`docs/redux/bridgewire_replacement_system_requirements_v1.0.md`. The mapping
below records executable evidence for all minimum acceptance criteria.

| AC | Evidence |
|---:|---|
| 1–2 | `test_controller.py`: safe startup and three-second release |
| 3–5 | `test_controller.py`: disabled, unknown, malformed outcomes |
| 6 | `test_reader_parser.py`: strict invalid-byte rejection |
| 7–9 | `test_controller.py`: processing during release and fixed deadline |
| 10–12 | `test_escalation.py`: warning, critical, quiet reset |
| 13–15 | `test_reader_health.py`: not found, ambiguity, unrelated devices |
| 16–17 | `test_reader_health.py`: disconnect/recovery and interruptible wait |
| 18 | `test_cli_and_failures.py`, `test_audit_and_config.py`: access remains local and queue retains pending work |
| 19–20 | `test_authorization.py`: failed reload retention and atomic replace |
| 21 | `test_controller.py`: shutdown LOW and cleanup |
| 22 | `test_audit_and_config.py`, `test_vertical_slice.py`: privacy assertions |
| 23 | CI and the documented local quality-gate commands |

Additional requirement coverage:

| Contract area | Evidence |
|---|---|
| ID-20LA framing/checksum/partial/multiple records | `test_reader_parser.py` |
| Injectable reader identity and stable identity across paths | `test_reader_health.py` |
| Open/read/repeated reconnect events and bounded backoff | `test_reader_health.py` |
| BCM23 LOW/HIGH contract and injected GPIO failures | `test_controller.py`, `test_cli_and_failures.py` |
| Exact `KEY,NAME,ALLOW` schema and sanitized fixtures | `test_authorization.py`, `schemas/authorization-file/schema.json` |
| Typed structured events and nonblocking notification queue | `test_audit_and_config.py`, `test_escalation.py` |
| Full sanitized lifecycle | `test_vertical_slice.py`; `poetry run bridgewire simulate --config configs/simulation.toml` |

Hardware deployment, live notification delivery, real endpoints, and real
credentials are excluded by the increment contract and have no implementation
claim here.

## Hardening adjustment

The adjustment suite adds independent literal reader frames; normalized-key
and empty-file policy tests; deterministic atomic-install failure injection;
GPIO startup, restoration, cleanup, and idempotency failures; notifier retry
retention; real-flow privacy assertions; semantic-version consistency;
inclusive and adjacent escalation boundaries; detailed reader identity,
telemetry, and reconnect cases; working-directory-independent resource tests;
and exact vertical-slice audit/GPIO/reader ordering assertions.
