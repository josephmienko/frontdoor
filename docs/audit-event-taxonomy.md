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

The JSONL queue writes locally first. Delivery status can later move from
pending to delivered, but endpoint delivery is outside this increment.
