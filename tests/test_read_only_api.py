from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridgewire.adapters.http.api import ApplicationContainer, create_app
from bridgewire.adapters.persistence.sqlite_audit_reader import SQLiteAuditReader
from bridgewire.application.query_service import ReadOnlyQueryService
from bridgewire.application.status_service import (
    OperationalSnapshot,
    OperationalSnapshotStore,
    StatusService,
)
from bridgewire.audit import (
    AuditEvent,
    EventType,
    InMemoryNotificationQueue,
    Severity,
    SQLiteAuditSink,
)
from bridgewire.authorization import AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController
from bridgewire.reader import (
    ParsedRecord,
    ReaderHealthState,
    ReaderIdentity,
    ReaderSnapshot,
    ReaderSupervisor,
    SerialDevice,
)
from bridgewire.simulation import SimulatedReaderSession


def _client(
    *,
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
    maximum_page_size: int = 2,
) -> tuple[TestClient, AccessController, OperationalSnapshotStore, SQLiteAuditSink]:
    controller, clock, _relay, _memory_audit, _memory_notifications = system
    assert isinstance(controller, AccessController)
    assert isinstance(clock, ManualClock)
    stable = Path("/dev/serial/by-id/api-reader")
    reader = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable)],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _seconds: True,
        emit=lambda _event: None,
        monotonic=clock.monotonic,
    )
    audit_path = tmp_path / "audit.sqlite3"
    audit_sink = SQLiteAuditSink(audit_path)
    audit_reader = SQLiteAuditReader(audit_path)
    operational = OperationalSnapshotStore(
        OperationalSnapshot(controller.snapshot(), reader.snapshot())
    )
    status = StatusService(
        operational=operational,
        authorization=authorization,
        audit=audit_reader,
        notifications=InMemoryNotificationQueue(),
        clock=clock,
        software_version="1.3.0",
        application_started_at=clock.now(),
    )
    queries = ReadOnlyQueryService(
        status=status,
        events=audit_reader,
        maximum_event_page_size=maximum_page_size,
    )
    return (
        TestClient(create_app(ApplicationContainer(queries))),
        controller,
        operational,
        audit_sink,
    )


@pytest.mark.integration
def test_api_health_status_events_and_absent_control_routes(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, controller, operational, audit = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
    )
    audit.append(
        AuditEvent(EventType.SERVICE_STARTED, Severity.INFO, datetime(2026, 1, 1, tzinfo=UTC))
    )
    audit.append(
        AuditEvent(
            EventType.READER_CONNECTED,
            Severity.INFO,
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
    )
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    operational.publish(
        controller.snapshot(),
        operational.snapshot().reader,
    )

    assert client.get("/health").status_code == 503  # reader is not yet ready
    status = client.get("/status")
    assert status.status_code == 200
    assert status.json()["controller_state"] == "released"
    assert status.json()["last_relay_command"] == "released"
    events = client.get("/events", params={"limit": 2})
    assert events.status_code == 200
    assert len(events.json()["events"]) == 2
    event_id = events.json()["events"][0]["event_id"]
    assert client.get(f"/events/{event_id}").status_code == 200
    filtered = client.get(
        "/events",
        params={"limit": 2, "event_type": "reader_connected", "severity": "info"},
    )
    assert [item["event_type"] for item in filtered.json()["events"]] == ["reader_connected"]
    before = client.get(
        "/events",
        params={"limit": 2, "before": "2026-01-01T00:00:01Z"},
    )
    assert [item["event_type"] for item in before.json()["events"]] == ["service_started"]
    assert client.get("/events/not-present").status_code == 404
    assert client.get("/events", params={"limit": 3}).status_code == 422
    for route in ("/unlock", "/lock", "/simulation/credentials"):
        assert client.post(route).status_code == 404
    audit.close()


@pytest.mark.integration
def test_api_reports_healthy_only_from_operational_state(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, controller, operational, audit = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
    )
    controller.start()
    operational.publish(
        controller.snapshot(),
        ReaderSnapshot(
            connected=True,
            health_state=ReaderHealthState.READY,
            last_record_age_seconds=None,
        ),
    )
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    audit.close()
