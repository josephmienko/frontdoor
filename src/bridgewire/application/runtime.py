from __future__ import annotations

import threading
from types import MappingProxyType

from bridgewire.application.access_service import AccessService, CredentialSource
from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.interfaces import AuditSink, Clock, HealthReporter
from bridgewire.reader import (
    ReaderEvent,
    ReaderEventType,
    ReaderRecordStream,
    ReaderSupervisor,
)


class BridgewireRuntime:
    """Cooperative reader/controller lifecycle independent of OS signal handling."""

    def __init__(
        self,
        *,
        access: AccessService,
        reader: ReaderSupervisor,
        health_reporter: HealthReporter,
        audit: AuditSink,
        clock: Clock,
        maximum_record_bytes: int = 16,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._access = access
        self._reader = reader
        self._health = health_reporter
        self._audit = audit
        self._clock = clock
        self._stream = ReaderRecordStream(maximum_record_bytes * 4)
        self._stopped = stop_event or threading.Event()
        self._shutdown_complete = False

    @property
    def shutdown_requested(self) -> bool:
        return self._stopped.is_set()

    def start(self) -> None:
        self._access.start()
        self._health.report("degraded", reason="reader_connecting")

    def run_once(self) -> None:
        self._access.tick()
        if not self._reader.connected:
            if self._reader.connect_until_ready(1):
                self._health.report("ready", reader="connected")
            return
        for record in self._reader.read_records_once(self._stream):
            self._access.submit_record(
                record,
                source=CredentialSource.PHYSICAL_READER,
            )
        self._access.tick()

    def run(self) -> None:
        while not self.shutdown_requested:
            self.run_once()

    def cooperative_wait(self, seconds: float) -> bool:
        deadline = self._clock.monotonic() + seconds
        while self._clock.monotonic() < deadline:
            self._access.tick()
            remaining = max(0.0, deadline - self._clock.monotonic())
            if self._stopped.wait(min(0.05, remaining)):
                return False
        return True

    def request_shutdown(self) -> None:
        self._stopped.set()

    def record_reader_event(self, event: ReaderEvent) -> None:
        if event.event_type in {
            ReaderEventType.READER_NOT_FOUND,
            ReaderEventType.READER_IDENTITY_AMBIGUOUS,
            ReaderEventType.READER_OPEN_FAILED,
            ReaderEventType.READER_READ_FAILED,
            ReaderEventType.READER_DISCONNECTED,
            ReaderEventType.RECONNECT_REPEATEDLY_FAILED,
        }:
            self._health.report("degraded", reason=event.event_type.value)
        self._audit.append(
            AuditEvent(
                event_type=EventType(event.event_type.value),
                severity=(
                    Severity.INFO
                    if event.event_type.value
                    in {"reader_connected", "reader_recovered", "reader_record_received"}
                    else Severity.WARNING
                ),
                timestamp=self._clock.now(),
                correlation=MappingProxyType({"attempt": event.attempt} if event.attempt else {}),
                reader_state=self._reader.health_state.value,
                controller_state=self._access.controller_state.value,
            )
        )

    def report_fault(self) -> None:
        self._health.report("faulted")

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._reader.close()
        self._access.shutdown()
        self._health.report("stopped")
        self._shutdown_complete = True
