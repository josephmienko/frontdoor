from __future__ import annotations

import threading
from collections.abc import Callable
from types import MappingProxyType

from bridgewire.application.access_service import AccessService, CredentialSource
from bridgewire.application.status_service import OperationalSnapshotStore
from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.interfaces import AuditSink, Clock, HealthReporter, Waiter
from bridgewire.reader import (
    ReaderEvent,
    ReaderEventType,
    ReaderRecordStream,
    ReaderSupervisor,
)


class EventWaiter:
    """Production waiter that keeps ticking and responds promptly to shutdown."""

    def __init__(
        self,
        clock: Clock,
        stop_event: threading.Event,
        *,
        interval_seconds: float = 0.05,
    ) -> None:
        self._clock = clock
        self._stop_event = stop_event
        self._interval_seconds = interval_seconds

    def wait(self, seconds: float, on_interval: Callable[[], None]) -> bool:
        deadline = self._clock.monotonic() + seconds
        while self._clock.monotonic() < deadline:
            on_interval()
            remaining = max(0.0, deadline - self._clock.monotonic())
            if self._stop_event.wait(min(self._interval_seconds, remaining)):
                return False
        return True


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
        waiter: Waiter | None = None,
        operational_snapshots: OperationalSnapshotStore | None = None,
    ) -> None:
        self._access = access
        self._reader = reader
        self._health = health_reporter
        self._audit = audit
        self._clock = clock
        self._stream = ReaderRecordStream(maximum_record_bytes * 4)
        self._stopped = stop_event or threading.Event()
        self._waiter = waiter or EventWaiter(clock, self._stopped)
        self._shutdown_complete = False
        self._operational_snapshots = operational_snapshots

    def _publish_snapshot(self) -> None:
        if self._operational_snapshots is not None:
            self._operational_snapshots.publish(self._access.snapshot(), self._reader.snapshot())

    @property
    def shutdown_requested(self) -> bool:
        return self._stopped.is_set()

    def start(self) -> None:
        self._access.start()
        self._health.report("degraded", reason="reader_connecting")
        self._publish_snapshot()

    def run_once(self) -> bool:
        self._access.tick()
        try:
            if not self._reader.connected:
                if self._reader.connect_until_ready(1):
                    self._health.report("ready", reader="connected")
                    return True
                return False
            for record in self._reader.read_records_once(self._stream):
                self._access.submit_record(
                    record,
                    source=CredentialSource.PHYSICAL_READER,
                )
            self._access.tick()
            return False
        finally:
            self._publish_snapshot()

    def run(self, *, on_ready: Callable[[], None] | None = None) -> None:
        while not self.shutdown_requested:
            if self.run_once() and on_ready is not None:
                on_ready()

    def cooperative_wait(self, seconds: float) -> bool:
        return self._waiter.wait(seconds, self._access.tick)

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

    def handle_failure(self) -> None:
        self._access._recoverable_failure()

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        failures: list[BaseException] = []
        # Secure relay state takes precedence over reader and health cleanup.
        for cleanup in (
            self._access.shutdown,
            self._reader.close,
            lambda: self._health.report("stopped"),
        ):
            try:
                cleanup()
            except BaseException as exc:
                failures.append(exc)
        if failures:
            self._publish_snapshot()
            raise BaseExceptionGroup("runtime shutdown failed", failures)
        self._shutdown_complete = True
        self._publish_snapshot()
