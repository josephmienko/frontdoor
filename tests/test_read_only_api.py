from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridgewire.adapters.http.api import ApplicationContainer, create_app
from bridgewire.adapters.persistence.sqlite_audit_reader import SQLiteAuditReader
from bridgewire.application.query_service import QueryUnavailableError, ReadOnlyQueryService
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
    initial_snapshot: bool = True,
) -> tuple[
    TestClient,
    AccessController,
    OperationalSnapshotStore,
    SQLiteAuditSink,
    ManualClock,
]:
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
        OperationalSnapshot(controller.snapshot(), reader.snapshot(), clock.now(), None)
        if initial_snapshot
        else None
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
        clock,
    )


@pytest.mark.integration
def test_api_health_status_events_and_absent_control_routes(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, controller, operational, audit, clock = _client(
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
    current = operational.snapshot()
    assert current is not None
    operational.publish(controller.snapshot(), current.reader, clock.now())

    assert client.get("/health").status_code == 503  # reader is not yet ready
    status = client.get("/status")
    assert status.status_code == 200
    assert status.json()["controller_state"] == "released"
    assert status.json()["last_relay_command"] == "released"
    events = client.get("/events", params={"limit": 2})
    assert events.status_code == 200
    assert len(events.json()["items"]) == 2
    event_id = events.json()["items"][0]["event_id"]
    assert client.get(f"/events/{event_id}").status_code == 200
    filtered = client.get(
        "/events",
        params={"limit": 2, "event_type": "reader_connected", "severity": "info"},
    )
    assert [item["event_type"] for item in filtered.json()["items"]] == ["reader_connected"]
    before = client.get(
        "/events",
        params={"limit": 2, "before": "2026-01-01T00:00:01Z"},
    )
    assert [item["event_type"] for item in before.json()["items"]] == ["service_started"]
    assert client.get("/events/not-present").status_code == 422
    assert client.get("/events", params={"limit": 3}).status_code == 422
    for route in ("/unlock", "/lock", "/simulation/credentials"):
        assert client.post(route).status_code == 404
    assert set(client.get("/openapi.json").json()["paths"]) == {
        "/health",
        "/status",
        "/events",
        "/events/{event_id}",
    }
    audit.close()


@pytest.mark.unit
def test_api_module_import_has_no_production_side_effects() -> None:
    import bridgewire.adapters.http.api as api

    assert not hasattr(api, "runtime")
    assert not hasattr(api, "gpio")
    assert not hasattr(api, "server")


@pytest.mark.integration
def test_api_reports_healthy_only_from_operational_state(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, controller, operational, audit, clock = _client(
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
        clock.now(),
    )
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    audit.close()


@pytest.mark.integration
def test_health_detects_missing_stale_faulted_and_stopped_snapshots(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, controller, operational, audit, clock = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
        initial_snapshot=False,
    )
    missing = client.get("/health")
    assert missing.status_code == 503
    assert missing.json() == {
        "status": "unavailable",
        "controller_state": None,
        "reader_health": None,
        "operational_snapshot_stale": True,
    }
    controller.start()
    ready_reader = ReaderSnapshot(True, ReaderHealthState.READY, None)
    operational.publish(controller.snapshot(), ready_reader, clock.now())
    assert client.get("/health").status_code == 200
    clock.advance(10)
    boundary = client.get("/health")
    assert boundary.status_code == 503
    assert boundary.json()["operational_snapshot_stale"] is True
    operational.publish(controller.snapshot(), ready_reader, clock.now())
    controller.recoverable_failure()
    operational.publish(controller.snapshot(), ready_reader, clock.now())
    assert client.get("/health").json()["controller_state"] == "faulted"
    controller.shutdown()
    operational.publish(controller.snapshot(), ready_reader, clock.now())
    assert client.get("/health").json()["controller_state"] == "stopped"
    audit.close()


@pytest.mark.integration
def test_cursor_pagination_preserves_identical_timestamps_and_filters(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, _controller, _operational, audit, _clock = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
    )
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    ids = [
        "00000000-0000-0000-0000-000000000004",
        "00000000-0000-0000-0000-000000000003",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    ]
    for event_id in ids:
        audit.append(
            AuditEvent(
                EventType.READER_CONNECTED,
                Severity.INFO,
                timestamp,
                event_id=event_id,
            )
        )
    first = client.get(
        "/events",
        params={"limit": 2, "event_type": "reader_connected"},
    ).json()
    assert [item["event_id"] for item in first["items"]] == ids[:2]
    assert first["next_cursor"]
    audit.append(
        AuditEvent(
            EventType.READER_CONNECTED,
            Severity.INFO,
            timestamp.replace(day=3),
        )
    )
    second = client.get(
        "/events",
        params={
            "limit": 2,
            "cursor": first["next_cursor"],
            "event_type": "reader_connected",
        },
    ).json()
    assert [item["event_id"] for item in second["items"]] == ids[2:]
    assert second["next_cursor"] is None
    assert client.get("/events", params={"cursor": "not-a-cursor"}).status_code == 422
    assert (
        client.get(
            "/events",
            params={"cursor": first["next_cursor"], "before": timestamp.isoformat()},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/events",
            params={
                "limit": 2,
                "after": "2026-01-01T00:00:00Z",
                "before": "2026-01-01T00:00:00Z",
            },
        ).status_code
        == 422
    )
    audit.close()


@pytest.mark.integration
def test_event_time_and_identifier_validation(
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, _controller, _operational, audit, _clock = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
    )
    assert (
        client.get("/events", params={"limit": 2, "before": "2026-01-01T12:00:00"}).status_code
        == 422
    )
    assert (
        client.get(
            "/events",
            params={
                "limit": 2,
                "after": "2026-01-02T00:00:00Z",
                "before": "2026-01-01T00:00:00Z",
            },
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/events",
            params={
                "limit": 2,
                "after": "2026-01-01T00:00:00+02:00",
                "before": "2026-01-01T03:00:00Z",
            },
        ).status_code
        == 200
    )
    assert client.get("/events/not-a-uuid").status_code == 422
    assert client.get("/events/00000000-0000-0000-0000-000000000099").status_code == 404
    audit.close()


@pytest.mark.unit
def test_sqlite_reader_applies_timeout_and_translates_availability_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.sqlite3"
    sink = SQLiteAuditSink(path)
    sink.close()
    observed: dict[str, object] = {}
    original_connect = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        observed.update(kwargs)
        return original_connect(  # type: ignore[call-overload,no-any-return]
            *args, **kwargs
        )

    monkeypatch.setattr(
        "bridgewire.adapters.persistence.sqlite_audit_reader.sqlite3.connect",
        connect,
    )
    reader = SQLiteAuditReader(path, timeout_seconds=0.25)
    assert reader.list_events(limit=1) == ()
    assert observed["timeout"] == 0.25

    def locked() -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(reader, "_connect", locked)
    with pytest.raises(QueryUnavailableError):
        reader.list_events(limit=1)
    with pytest.raises(QueryUnavailableError):
        reader.get_event("00000000-0000-0000-0000-000000000001")


@pytest.mark.integration
def test_http_sanitizes_list_and_detail_query_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    system: tuple[object, ...],
    authorization: AuthorizationStore,
) -> None:
    client, _controller, _operational, audit, _clock = _client(
        tmp_path=tmp_path,
        system=system,
        authorization=authorization,
    )

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise QueryUnavailableError("C:/secret/audit.sqlite3 database is locked")

    monkeypatch.setattr(SQLiteAuditReader, "list_events", unavailable)
    response = client.get("/events", params={"limit": 2})
    assert response.status_code == 503
    assert response.json() == {"detail": "query service unavailable"}
    assert "secret" not in response.text
    monkeypatch.setattr(SQLiteAuditReader, "get_event", unavailable)
    detail = client.get("/events/00000000-0000-0000-0000-000000000001")
    assert detail.status_code == 503
    assert detail.json() == {"detail": "query service unavailable"}
    audit.close()
