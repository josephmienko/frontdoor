# Audit event taxonomy

Every audit event has a generated event ID, UTC timestamp, typed event name,
severity, and optional controller state, reader state, delivery status, and
non-sensitive correlation fields.

Access outcomes distinguish authorized, disabled, unknown, malformed,
release-commanded, release-restored, and actuation-failed. Controller lifecycle
events cover startup, shutdown, and faults. Escalation events distinguish
warning and critical; critical events enqueue a separate durable notification
record without network activity in the controller path.

Allowed correlation data describes behavior, such as an outcome category or
event count. Keys capable of carrying credentials, card IDs, names, passwords,
tokens, secrets, or webhook values are rejected. Production code never places
raw record bytes or decoded identifiers in audit values. Reader parser errors
report a reason category only.

The access controller writes only to a local queue. A separate notification
worker attempts endpoint delivery and removes an item only after success.
Endpoint exceptions and repeated failures leave the original event pending;
network delay or failure therefore never runs on the access-control path.
