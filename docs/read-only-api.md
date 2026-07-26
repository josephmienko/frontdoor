# Read-only API increment

Bridgewire remains a modular monolith. FastAPI is an optional inbound adapter
over framework-neutral query services; the controller, authorization store,
reader, audit sink, and relay continue communicating directly in-process.

## Concurrency boundary

The runtime publishes controller and reader state together as one immutable
`OperationalSnapshot` through a lock-protected store. HTTP reads never inspect
live controller or reader fields. Audit queries use a new SQLite read-only
connection per operation, avoiding the writer connection's thread affinity.
The durable notification queue serializes reads and mutations with a reentrant
lock.

The API application factory receives an explicit `ApplicationContainer` and
constructs no GPIO, serial, filesystem, or production database dependencies at
module import time.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health` | `200` only when the controller is operational, the reader is ready, and authorization data is loaded; otherwise `503`. |
| `GET /status` | Returns the immutable application status contract. |
| `GET /events` | Returns newest-first audit events with bounded `limit`, `before`, `after`, `event_type`, and `severity` filters. |
| `GET /events/{event_id}` | Returns one event or `404`. |

There are deliberately no unlock, lock, credential-injection, simulation, or
authorization-administration routes.

## Binding and deployment

API configuration defaults to disabled, `127.0.0.1:8080`, and a maximum event
page size of 100. A non-loopback host must be an explicit configuration change
and requires deployment-layer access control because HTTP authentication and
TLS termination are outside this increment.

This increment provides the application factory and query boundary. It does
not yet add a production API process or combine Uvicorn with the hardware
runtime; coordinated host lifecycle belongs to the next host-integration
increment. Tests instantiate the factory with simulated adapters using
FastAPI's test client.
