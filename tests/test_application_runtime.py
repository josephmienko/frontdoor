from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bridgewire.application.access_service import AccessService, CredentialSource
from bridgewire.application.runtime import BridgewireRuntime
from bridgewire.audit import InMemoryAuditSink
from bridgewire.authorization import AuthorizationOutcome
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController, ControllerState, PhysicalReleaseStatus
from bridgewire.gpio import RelayActionType, SimulatedRelay
from bridgewire.reader import (
    ReaderDisconnectedError,
    ReaderEvent,
    ReaderEventType,
    ReaderIdentity,
    ReaderSupervisor,
    SerialDevice,
    parse_credential_identifier,
)
from bridgewire.simulation import SimulatedReaderSession, build_record


@dataclass
class RecordingHealthReporter:
    reports: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def report(self, status: str, **details: object) -> None:
        self.reports.append((status, details))


@dataclass
class AdvancingWaiter:
    clock: ManualClock
    calls: list[float] = field(default_factory=list)
    interrupt: bool = False

    def wait(self, seconds: float, on_interval: Callable[[], None]) -> bool:
        self.calls.append(seconds)
        remaining = seconds
        while remaining > 0:
            step = min(0.25, remaining)
            self.clock.advance(step)
            on_interval()
            remaining -= step
            if self.interrupt:
                return False
        return True


@dataclass
class FailingHealthReporter(RecordingHealthReporter):
    fail_statuses: set[str] = field(default_factory=set)

    def report(self, status: str, **details: object) -> None:
        super().report(status, **details)
        if status in self.fail_statuses:
            raise OSError(f"{status} health failed")


@pytest.mark.unit
def test_access_service_maps_explicit_source_and_serializable_result(
    system: tuple[object, ...],
) -> None:
    controller = system[0]
    assert isinstance(controller, AccessController)
    service = AccessService(controller)
    service.start()
    result = service.submit_credential(
        "0102030405",
        source=CredentialSource.TEST,
    )
    assert result.authorized
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert not result.malformed
    assert result.physical_release is PhysicalReleaseStatus.ASSERTED
    assert result.relay_actuation_requested
    assert result.relay_actuation_succeeded
    assert result.controller_state is ControllerState.RELEASED
    assert result.as_dict() == {
        "authorized": True,
        "authorization": "authorized",
        "malformed": False,
        "controller_state": "released",
        "physical_release": "asserted",
        "relay_actuation_requested": True,
        "relay_actuation_succeeded": True,
        "source": "test",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "credential",
    [
        "a1b2c3d4e5",
        "010203040",
        "01020304050",
        "010203040Z",
        " 0102030405",
        "0102030405 ",
    ],
)
def test_access_service_routes_invalid_identifier_through_malformed_path(
    credential: str,
    system: tuple[object, ...],
) -> None:
    controller, _clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    service = AccessService(controller)
    service.start()
    result = service.submit_credential(
        credential,
        source=CredentialSource.API_INJECTED_READER_EVENT,
    )
    assert not result.authorized
    assert result.authorization is None
    assert result.malformed
    assert result.physical_release is PhysicalReleaseStatus.NOT_REQUESTED
    assert not result.relay_actuation_requested
    assert not relay.is_high
    assert audit.events[-1].event_type.value == "malformed_record"


@pytest.mark.unit
def test_access_service_origin_is_mandatory(system: tuple[object, ...]) -> None:
    controller = system[0]
    assert isinstance(controller, AccessController)
    service = AccessService(controller)
    service.start()
    with pytest.raises(TypeError):
        service.submit_parsed_record(  # type: ignore[call-arg]
            parse_credential_identifier("0102030405")
        )


@pytest.mark.unit
def test_access_service_denied_result_is_unambiguous(
    system: tuple[object, ...],
) -> None:
    controller = system[0]
    assert isinstance(controller, AccessController)
    service = AccessService(controller)
    service.start()
    result = service.submit_credential(
        "1112131415",
        source=CredentialSource.SIMULATOR,
    )
    assert not result.authorized
    assert result.authorization is AuthorizationOutcome.DENIED
    assert not result.malformed
    assert result.physical_release is PhysicalReleaseStatus.NOT_REQUESTED
    assert not result.relay_actuation_requested
    assert not result.relay_actuation_succeeded
    assert result.as_dict()["source"] == "simulator"


@pytest.mark.failure_mode
def test_access_service_separates_authorization_from_actuation_failure(
    system: tuple[object, ...],
) -> None:
    controller, _clock, relay, _audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(relay, SimulatedRelay)
    service = AccessService(controller)
    service.start()
    relay.fail_next_high = True
    result = service.submit_credential(
        "0102030405",
        source=CredentialSource.TEST,
    )
    assert result.authorized
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert result.physical_release is PhysicalReleaseStatus.ACTUATION_FAILED
    assert result.relay_actuation_requested
    assert not result.relay_actuation_succeeded
    assert result.controller_state is ControllerState.FAULTED
    assert "audit_event_id" not in result.as_dict()


@pytest.mark.integration
def test_runtime_ticks_relocks_and_cleans_up_during_reader_inactivity(
    system: tuple[object, ...],
) -> None:
    controller, clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    stable = Path("/dev/serial/by-id/runtime-reader")
    session = SimulatedReaderSession([build_record("0102030405")])
    runtime: BridgewireRuntime | None = None

    def emit(event: ReaderEvent) -> None:
        assert runtime is not None
        runtime.record_reader_event(event)

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: session,
        wait=lambda _seconds: True,
        emit=emit,
        monotonic=clock.monotonic,
    )
    health = RecordingHealthReporter()
    runtime = BridgewireRuntime(
        access=AccessService(controller),
        reader=reader,
        health_reporter=health,
        audit=audit,
        clock=clock,
    )
    runtime.start()
    runtime.run_once()
    runtime.run_once()
    assert controller.state is ControllerState.RELEASED
    assert relay.is_high
    clock.advance(3)
    runtime.run_once()
    assert controller.release_deadline is None
    assert not relay.is_high
    runtime.request_shutdown()
    assert runtime.shutdown_requested
    runtime.record_reader_event(ReaderEvent(ReaderEventType.READER_DISCONNECTED))
    runtime.shutdown()
    runtime.shutdown()
    assert health.reports == [
        ("degraded", {"reason": "reader_connecting"}),
        ("ready", {"reader": "connected"}),
        ("degraded", {"reason": "reader_disconnected"}),
        ("stopped", {}),
    ]
    assert [action.action for action in relay.actions][-2:] == [
        RelayActionType.LOW,
        RelayActionType.CLEANUP,
    ]


@pytest.mark.integration
def test_runtime_recovers_reader_without_restarting_controller(
    system: tuple[object, ...],
) -> None:
    controller, clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    stable = Path("/dev/serial/by-id/runtime-reader")
    sessions = [
        SimulatedReaderSession([ReaderDisconnectedError("removed")]),
        SimulatedReaderSession([]),
    ]
    runtime: BridgewireRuntime | None = None

    def emit(event: ReaderEvent) -> None:
        assert runtime is not None
        runtime.record_reader_event(event)

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: sessions.pop(0),
        wait=lambda _seconds: True,
        emit=emit,
        monotonic=clock.monotonic,
    )
    health = RecordingHealthReporter()
    access = AccessService(controller)
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=health,
        audit=audit,
        clock=clock,
    )
    runtime.start()
    runtime.run_once()
    result = access.submit_credential("0102030405", source=CredentialSource.TEST)
    assert result.relay_actuation_succeeded
    assert relay.is_high
    runtime.run_once()
    assert not reader.connected
    assert relay.is_high
    clock.advance(3)
    runtime.run_once()
    assert reader.connected
    assert controller.state is ControllerState.READY
    assert not relay.is_high
    assert [status for status, _details in health.reports] == [
        "degraded",
        "ready",
        "degraded",
        "ready",
    ]
    runtime.shutdown()


@pytest.mark.integration
def test_reconnect_wait_advances_manual_clock_and_keeps_relock_ticking(
    system: tuple[object, ...],
) -> None:
    controller, clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    runtime: BridgewireRuntime | None = None

    def emit(event: ReaderEvent) -> None:
        assert runtime is not None
        runtime.record_reader_event(event)

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/missing")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda seconds: runtime.cooperative_wait(seconds) if runtime else False,
        emit=emit,
        monotonic=clock.monotonic,
    )
    waiter = AdvancingWaiter(clock)
    access = AccessService(controller)
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=RecordingHealthReporter(),
        audit=audit,
        clock=clock,
        waiter=waiter,
    )
    runtime.start()
    access.submit_credential("0102030405", source=CredentialSource.TEST)
    assert relay.is_high
    runtime.run_once()
    runtime.run_once()
    assert waiter.calls == [1.0, 2.0]
    assert controller.release_deadline is None
    assert not relay.is_high
    runtime.shutdown()


@pytest.mark.unit
def test_shutdown_interrupts_reconnect_wait_without_advancing_manual_clock(
    system: tuple[object, ...],
) -> None:
    controller, clock, _relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/missing")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: False,
        emit=lambda _event: None,
    )
    runtime = BridgewireRuntime(
        access=AccessService(controller),
        reader=reader,
        health_reporter=RecordingHealthReporter(),
        audit=audit,
        clock=clock,
    )
    runtime.start()
    runtime.request_shutdown()
    assert not runtime.cooperative_wait(30)
    assert clock.monotonic() == 0
    runtime.shutdown()


@pytest.mark.unit
def test_runtime_readiness_callback_occurs_once_per_connection(
    system: tuple[object, ...],
) -> None:
    import threading

    controller, clock, _relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    stop = threading.Event()
    stable = Path("/dev/serial/by-id/runtime-reader")

    class StopSession:
        def read(self) -> bytes:
            stop.set()
            return b""

        def close(self) -> None:
            pass

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: StopSession(),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
    )
    runtime = BridgewireRuntime(
        access=AccessService(controller),
        reader=reader,
        health_reporter=RecordingHealthReporter(),
        audit=audit,
        clock=clock,
        stop_event=stop,
    )
    callbacks: list[str] = []
    runtime.start()
    runtime.run(on_ready=lambda: callbacks.append("ready"))
    assert callbacks == ["ready"]
    runtime.shutdown()


@pytest.mark.failure_mode
def test_reader_close_failure_cannot_skip_secure_shutdown(
    system: tuple[object, ...],
) -> None:
    controller, clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    stable = Path("/dev/serial/by-id/runtime-reader")

    class FailingCloseSession:
        def read(self) -> bytes:
            return b""

        def close(self) -> None:
            raise OSError("reader close failed")

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: FailingCloseSession(),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
    )
    access = AccessService(controller)
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=RecordingHealthReporter(),
        audit=audit,
        clock=clock,
    )
    runtime.start()
    runtime.run_once()
    access.submit_credential("0102030405", source=CredentialSource.TEST)
    assert relay.is_high
    with pytest.raises(BaseExceptionGroup, match="runtime shutdown failed"):
        runtime.shutdown()
    assert not relay.is_high
    assert controller.state is ControllerState.STOPPED
    with pytest.raises(BaseExceptionGroup):
        runtime.shutdown()
    assert not relay.is_high


@pytest.mark.failure_mode
def test_controller_and_reader_shutdown_failures_are_both_retained(
    system: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, clock, _relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    stable = Path("/dev/serial/by-id/runtime-reader")

    class FailingCloseSession:
        def read(self) -> bytes:
            return b""

        def close(self) -> None:
            raise OSError("reader close failed")

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: FailingCloseSession(),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
    )
    access = AccessService(controller)
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=RecordingHealthReporter(),
        audit=audit,
        clock=clock,
    )
    runtime.start()
    runtime.run_once()

    def fail_shutdown() -> None:
        raise RuntimeError("controller shutdown failed")

    monkeypatch.setattr(access, "shutdown", fail_shutdown)
    with pytest.raises(BaseExceptionGroup) as captured:
        runtime.shutdown()
    messages = [str(error) for error in captured.value.exceptions]
    assert messages == ["controller shutdown failed", "reader close failed"]


@pytest.mark.failure_mode
def test_shutdown_health_failure_occurs_after_relay_is_secured(
    system: tuple[object, ...],
) -> None:
    controller, clock, relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(relay, SimulatedRelay)
    assert isinstance(audit, InMemoryAuditSink)
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/reader")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: False,
        emit=lambda _event: None,
    )
    access = AccessService(controller)
    health = FailingHealthReporter(fail_statuses={"stopped"})
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=health,
        audit=audit,
        clock=clock,
    )
    runtime.start()
    access.submit_credential("0102030405", source=CredentialSource.TEST)
    with pytest.raises(BaseExceptionGroup):
        runtime.shutdown()
    assert not relay.is_high
    assert controller.state is ControllerState.STOPPED
