# Read-only API increment

Bridgewire remains a modular monolith. FastAPI is an optional inbound adapter
over framework-neutral query services; the controller, authorization store,
reader, audit sink, and relay continue communicating directly in-process.

## Concurrency boundary

The runtime publishes controller and reader state together as one immutable
`OperationalSnapshot` through a lock-protected store. HTTP reads never inspect
live controller or reader fields. Audit queries use a new SQLite read-only
connection per operation, avoiding the writer connection's thread affinity.
Each publication includes a UTC `snapshot_published_at`. Health considers a
snapshot stale at an age of 10 seconds by default; API responsiveness alone is
therefore not evidence that the access controller is healthy.

The durable notification queue serializes reads and mutations with a reentrant
lock. This is thread-safe within one process only; concurrent multi-process
mutation is unsupported. Filesystem reads occur while holding the lock, which
prevents intermediate in-process states but means a slow filesystem can
temporarily delay other queue access.

The API application factory receives an explicit `ApplicationContainer` and
constructs no GPIO, serial, filesystem, or production database dependencies at
module import time.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health` | `200` only when the controller is operational, the reader is ready, and authorization data is loaded; otherwise `503`. |
| `GET /status` | Returns the immutable application status contract. |
| `GET /events` | Returns newest-first audit events with bounded `limit`, opaque composite `cursor`, `before`, `after`, `event_type`, and `severity` filters. |
| `GET /events/{event_id}` | Returns one event or `404`. |

There are deliberately no unlock, lock, credential-injection, simulation, or
authorization-administration routes.

## Binding and deployment

API configuration defaults to disabled, `127.0.0.1:8080`, and a maximum event
page size of 100. Unknown API keys follow the existing configuration policy and
are ignored; a misspelled `enabled` key therefore leaves the API disabled.
A non-loopback host must be an explicit configuration change
and requires deployment-layer access control because HTTP authentication and
TLS termination are outside this increment.

This increment provides the application factory and query boundary. It does
not yet add a production API process or combine Uvicorn with the hardware
runtime; coordinated host lifecycle belongs to the next host-integration
increment. Tests instantiate the factory with simulated adapters using
FastAPI's test client.

Audit reads use a one-second SQLite busy timeout. Expected database
availability failures become a sanitized `503`; SQL, exception text, and paths
are not returned. Pagination orders by `(timestamp DESC, event_id DESC)` and
the opaque continuation cursor encodes both fields, so equal timestamps are
not skipped. Callers must retain the same filters while following a cursor.
`before` and `after` are UTC-normalized filters, not continuation tokens.

The test suite currently emits a deprecation warning from FastAPI/Starlette's
TestClient integration with the evolving HTTPX client API. Application code
does not call deprecated HTTPX internals, and runtime behavior is unaffected.
Revisit compatibility during the next dependency-refresh cycle.
