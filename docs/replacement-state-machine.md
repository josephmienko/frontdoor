# Replacement access state machine

`INITIALIZING` commands the relay LOW. Only success transitions to `SECURED`;
failure enters `FAULTED` and credential processing remains unavailable.

In `SECURED`, an authorized lookup requests HIGH and records the logical
authorization separately from confirmed actuation. Successful HIGH enters
`RELEASED` with a monotonic deadline three seconds later. Disabled, unknown,
and malformed inputs remain secured and are individually audited.

In `RELEASED`, every input is still classified and audited. Further authorized
inputs do not request HIGH again and do not change the original deadline.
Denied, unknown, and malformed inputs contribute to escalation. `tick()` at
the deadline commands LOW and returns to `SECURED`; there is no blocking sleep.

Shutdown moves through `SHUTTING_DOWN`, attempts LOW regardless of the prior
state, then cleans up and becomes `STOPPED`. A recoverable controller failure
also attempts to restore LOW. A failed GPIO command is audited as an actuation
failure and is never described as a confirmed physical release.
