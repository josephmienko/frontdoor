from __future__ import annotations

from pathlib import Path

import pytest

from bridgewire.application.status_service import StatusService
from bridgewire.audit import InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.authorization import AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController, ControllerState
from bridgewire.reader import (
    ParsedRecord,
    ReaderEvent,
    ReaderHealthState,
    ReaderIdentity,
    ReaderSupervisor,
    SerialDevice,
)
from bridgewire.simulation import SimulatedReaderSession


@pytest.mark.unit
def test_status_snapshot_is_stable_serializable_and_uses_public_sources(
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    controller, clock, _relay, audit, notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    assert isinstance(notifications, InMemoryNotificationQueue)
    stable = Path("/dev/serial/by-id/status-reader")
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
        monotonic=clock.monotonic,
    )
    service = StatusService(
        controller=controller,
        reader=reader,
        authorization=authorization,
        audit=audit,
        notifications=notifications,
        clock=clock,
        software_version="9.8.7",
    )
    started_at = clock.now()
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    processed_at = clock.now()
    clock.advance(1.25)

    snapshot = service.snapshot()

    assert snapshot.controller_state is ControllerState.RELEASED
    assert snapshot.reader_health is ReaderHealthState.DEGRADED
    assert snapshot.release_active
    assert snapshot.release_remaining_seconds == pytest.approx(1.75)
    assert snapshot.configured_release_seconds == 3
    assert snapshot.last_relay_command_high is True
    assert snapshot.last_successful_credential_processing_at == processed_at
    assert snapshot.authorization_loaded
    assert snapshot.authorization_record_count == authorization.record_count
    assert snapshot.authorization_version is not None
    assert snapshot.authorization_modified_at is not None
    assert snapshot.last_audit_event_at == audit.events[-1].timestamp
    assert snapshot.pending_notification_count == 0
    assert snapshot.software_version == "9.8.7"
    payload = snapshot.as_dict()
    assert payload["controller_state"] == "released"
    assert payload["reader_health"] == "degraded"
    assert payload["application_started_at"] == started_at.isoformat()


@pytest.mark.unit
def test_reader_snapshot_reports_connection_and_last_record_age() -> None:
    clock = ManualClock()
    stable = Path("/dev/serial/by-id/status-reader")
    session = SimulatedReaderSession([b"record"])
    events: list[ReaderEvent] = []
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: session,
        wait=lambda _seconds: True,
        emit=events.append,
        monotonic=clock.monotonic,
    )
    assert reader.connect_until_ready(1)
    reader.read_once()
    clock.advance(2)

    snapshot = reader.snapshot()

    assert snapshot.connected
    assert snapshot.health_state is ReaderHealthState.READY
    assert snapshot.last_record_age_seconds == 2


@pytest.mark.unit
def test_controller_snapshot_relocks_without_exposing_private_state(
    system: tuple[object, ...],
) -> None:
    controller, clock, _relay, _audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    clock.advance(3)
    controller.tick()

    snapshot = controller.snapshot()

    assert snapshot.state is ControllerState.READY
    assert not snapshot.release_active
    assert snapshot.release_deadline is None
    assert snapshot.release_remaining_seconds is None
    assert snapshot.last_relay_command_high is False
