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

When `[api].enabled = true`, the existing hardware host constructs one shared
application container and starts Uvicorn in an isolated daemon thread. The
hardware runtime remains on the main thread and remains the sole owner of
signals, relay state, and GPIO cleanup. API startup failure is retained but
does not prevent the local reader/runtime loop from running.

The API adapter is single-use and reports these explicit lifecycle states:
`new`, `starting`, `running`, `start_failed`, `start_timed_out`, `failed`,
`stopping`, `stopped`, and `stop_timed_out`. Construction, startup, runtime,
and shutdown failures are logged immediately, retained for final aggregation,
and written to file health as API degradation without including exception
text. The main loop performs a non-blocking state observation once per existing
runtime iteration; it never joins or waits for HTTP work while processing
reader/controller events.

Coordinated shutdown is deliberately safety-first:

1. the hardware runtime shuts down and commands the relay LOW;
2. the hardware host independently retries final relay cleanup;
3. Uvicorn receives its bounded shutdown request;
4. durable audit persistence closes.

An API shutdown timeout is reported only after the relay-secure attempts.
Uvicorn never owns or invokes GPIO cleanup. No separate API command, background
control worker, or systemd unit is introduced.

Startup waits at most two seconds for Uvicorn readiness. If readiness fails,
the initial cleanup join can take up to five additional seconds. The
worst-case bounded delay before reader processing is therefore approximately
seven seconds. The relay has already been secured before this optional API
attempt, and an API failure cannot terminate access control. Shutdown joins
for at most five seconds. Server adapter instances cannot be restarted; a
process-level restart constructs a new container and adapter.

The API thread is daemonized only as a last-resort bounded-exit escape hatch.
Graceful shutdown and a bounded join are always attempted. If a thread remains
alive, that state and failure are retained; interpreter exit may terminate
in-flight HTTP responses abruptly, but relay LOW and the host's direct GPIO
cleanup have already been attempted. Audit closure remains independent.

Authentication and TLS are not implemented. Loopback remains the default,
control routes remain unsupported, and systemd/deployment integration is
explicitly deferred.

File-health output gained additive `api` and `access_control` fields. Existing
JSON consumers that tolerate unknown fields remain compatible; consumers that
reject unknown fields may require adjustment. The file-health schema is not
currently formally versioned.

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
