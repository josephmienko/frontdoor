from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest

from bridgewire.application.status_service import (
    OperationalSnapshot,
    OperationalSnapshotStore,
    StatusService,
)
from bridgewire.audit import (
    AuditEvent,
    DurableNotificationQueue,
    EventType,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
    Severity,
    SQLiteAuditSink,
)
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController, ControllerState, RelayCommand
from bridgewire.gpio import SimulatedRelay
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
    operational = OperationalSnapshotStore(
        OperationalSnapshot(controller.snapshot(), reader.snapshot())
    )
    service = StatusService(
        operational=operational,
        authorization=authorization,
        audit=audit,
        notifications=notifications,
        clock=clock,
        software_version="9.8.7",
        application_started_at=clock.now(),
    )
    started_at = clock.now()
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    processed_at = clock.now()
    clock.advance(1.25)
    operational.publish(controller.snapshot(), reader.snapshot())

    snapshot = service.snapshot()

    assert snapshot.controller_state is ControllerState.RELEASED
    assert snapshot.reader_health is ReaderHealthState.DEGRADED
    assert snapshot.release_active
    assert snapshot.release_remaining_seconds == pytest.approx(1.75)
    assert snapshot.configured_release_seconds == 3
    assert snapshot.last_relay_command is RelayCommand.RELEASED
    assert snapshot.last_credential_processed_at == processed_at
    assert snapshot.release_deadline_at == clock.now() + timedelta(seconds=1.75)
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
    assert snapshot.release_deadline_monotonic is None
    assert snapshot.release_remaining_seconds is None
    assert snapshot.last_relay_command is RelayCommand.SECURED


@pytest.mark.unit
def test_status_before_start_serializes_unavailable_values_and_injected_start_time(
    system: tuple[object, ...],
    schema: dict[str, object],
) -> None:
    controller, clock, _relay, audit, notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    assert isinstance(notifications, InMemoryNotificationQueue)
    authorization = AuthorizationStore(AuthorizationFile(schema))
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/missing")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
        monotonic=clock.monotonic,
    )
    application_started_at = clock.now() - timedelta(seconds=30)
    snapshot = StatusService(
        operational=OperationalSnapshotStore(
            OperationalSnapshot(controller.snapshot(), reader.snapshot())
        ),
        authorization=authorization,
        audit=audit,
        notifications=notifications,
        clock=clock,
        software_version="1.2.1",
        application_started_at=application_started_at,
    ).snapshot()

    assert snapshot.controller_state is ControllerState.INITIALIZING
    assert not snapshot.authorization_loaded
    assert snapshot.authorization_record_count == 0
    assert snapshot.authorization_version is None
    assert snapshot.authorization_modified_at is None
    assert snapshot.last_audit_event_at is None
    assert snapshot.last_credential_processed_at is None
    assert snapshot.release_deadline_at is None
    assert snapshot.last_relay_command is None
    assert snapshot.application_started_at == application_started_at
    payload = snapshot.as_dict()
    assert all(
        payload[field] is None
        for field in (
            "authorization_version",
            "authorization_modified_at",
            "last_audit_event_at",
            "last_credential_processed_at",
            "release_deadline_at",
            "last_relay_command",
        )
    )


@pytest.mark.unit
def test_expired_release_reports_zero_remaining_until_controller_ticks(
    system: tuple[object, ...],
) -> None:
    controller, clock, _relay, _audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    clock.advance(4)

    snapshot = controller.snapshot()

    assert snapshot.state is ControllerState.RELEASED
    assert snapshot.release_remaining_seconds == 0


@pytest.mark.unit
def test_concrete_persistence_status_sources(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    audit = SQLiteAuditSink(tmp_path / "audit.sqlite3")
    queue = DurableNotificationQueue(tmp_path / "notifications.jsonl")
    event = AuditEvent(
        EventType.ESCALATION_CRITICAL,
        Severity.CRITICAL,
        clock.now(),
        MappingProxyType({"event_count": 3}),
    )
    audit.append(event)
    queue.enqueue(event)

    assert audit.latest_event_time() == clock.now()
    assert queue.pending_count() == 1
    audit.close()


@pytest.mark.unit
def test_status_source_failure_propagates_predictably(
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    controller, clock, _relay, _audit, notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(notifications, InMemoryNotificationQueue)

    class FailingAudit:
        def latest_event_time(self) -> None:
            raise OSError("audit unavailable")

    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/missing")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
        monotonic=clock.monotonic,
    )
    service = StatusService(
        operational=OperationalSnapshotStore(
            OperationalSnapshot(controller.snapshot(), reader.snapshot())
        ),
        authorization=authorization,
        audit=FailingAudit(),
        notifications=notifications,
        clock=clock,
        software_version="1.2.1",
        application_started_at=clock.now(),
    )

    with pytest.raises(OSError, match="audit unavailable"):
        service.snapshot()


@pytest.mark.unit
def test_status_service_rejects_ambiguous_application_start_timestamp(
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    controller, clock, _relay, audit, notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    assert isinstance(audit, InMemoryAuditSink)
    assert isinstance(notifications, InMemoryNotificationQueue)
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/missing")),
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
        monotonic=clock.monotonic,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        StatusService(
            operational=OperationalSnapshotStore(
                OperationalSnapshot(controller.snapshot(), reader.snapshot())
            ),
            authorization=authorization,
            audit=audit,
            notifications=notifications,
            clock=clock,
            software_version="1.2.1",
            application_started_at=datetime(2026, 1, 1),
        )


@pytest.mark.unit
def test_controller_snapshot_reports_stopped_and_faulted_states(
    system: tuple[object, ...],
) -> None:
    controller, _clock, relay, _audit, _notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(relay, SimulatedRelay)
    controller.start()
    relay.fail_next_high = True
    controller.process(ParsedRecord("0102030405"))
    assert controller.snapshot().state is ControllerState.FAULTED
    controller.shutdown()
    assert controller.snapshot().state is ControllerState.STOPPED
