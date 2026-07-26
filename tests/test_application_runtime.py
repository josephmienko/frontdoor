from __future__ import annotations

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
)
from bridgewire.simulation import SimulatedReaderSession, build_record


@dataclass
class RecordingHealthReporter:
    reports: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def report(self, status: str, **details: object) -> None:
        self.reports.append((status, details))


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
    assert result.accepted
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert result.physical_release is PhysicalReleaseStatus.ASSERTED
    assert result.release_initiated
    assert result.controller_state is ControllerState.RELEASED
    assert result.as_dict() == {
        "accepted": True,
        "authorization": "authorized",
        "controller_state": "released",
        "physical_release": "asserted",
        "release_initiated": True,
        "source": "test",
        "audit_event_id": None,
    }


@pytest.mark.unit
def test_access_service_rejects_unparsed_injected_identifier(
    system: tuple[object, ...],
) -> None:
    controller = system[0]
    assert isinstance(controller, AccessController)
    service = AccessService(controller)
    service.start()
    with pytest.raises(ValueError, match="uppercase hexadecimal"):
        service.submit_credential(
            "not-a-card",
            source=CredentialSource.API_INJECTED_READER_EVENT,
        )


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
    controller, clock, _relay, audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
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
    assert not reader.connected
    runtime.run_once()
    assert reader.connected
    assert controller.state is ControllerState.READY
    assert [status for status, _details in health.reports] == [
        "degraded",
        "ready",
        "degraded",
        "ready",
    ]
    runtime.shutdown()
