from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire.audit import (
    AuditEvent,
    DurableNotificationQueue,
    EventType,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
    Severity,
)
from bridgewire.cli import main
from bridgewire.clock import ManualClock
from bridgewire.configuration import ConfigurationError, load_configuration
from bridgewire.controller import AccessController, ControllerState
from bridgewire.gpio import SimulatedRelay
from bridgewire.reader import (
    BackoffPolicy,
    ReaderEvent,
    ReaderEventType,
    ReaderIdentity,
    ReaderSupervisor,
    SerialDevice,
)
from bridgewire.simulation import SimulatedReaderSession


@pytest.mark.unit
def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.2.0"


@pytest.mark.integration
def test_cli_simulation_is_json_lines_and_private(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["simulate"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert "0102030405" not in "\n".join(lines)


@pytest.mark.unit
def test_durable_notification_is_retained_until_marked_delivered(tmp_path: Path) -> None:
    queue_path = tmp_path / "notifications.jsonl"
    queue = DurableNotificationQueue(queue_path)
    event = AuditEvent(EventType.ESCALATION_CRITICAL, Severity.CRITICAL, datetime.now(UTC))
    queue.enqueue(event)
    recovered = DurableNotificationQueue(queue_path)
    assert [item["event_id"] for item in recovered.pending()] == [event.event_id]
    recovered.mark_delivered(event.event_id)
    assert recovered.pending() == []


@pytest.mark.unit
def test_configuration_reports_missing_table(tmp_path: Path) -> None:
    path = tmp_path / "empty.toml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="reader"):
        load_configuration(path)


@pytest.mark.unit
def test_clock_rejects_backwards_time() -> None:
    with pytest.raises(ValueError, match="backwards"):
        ManualClock().advance(-1)


@pytest.mark.unit
def test_backoff_jitter_is_deterministic() -> None:
    jittered = BackoffPolicy(minimum=10, maximum=20, jitter=0.2)
    assert jittered.delay(1, random_unit=0) == 8
    assert jittered.delay(1, random_unit=1) == 12


@pytest.mark.unit
def test_invalid_reader_identity_and_backoff_are_rejected() -> None:
    with pytest.raises(ValueError, match="identity"):
        ReaderIdentity()
    with pytest.raises(ValueError, match="bounds"):
        BackoffPolicy(minimum=0, maximum=1)
    with pytest.raises(ValueError, match="jitter"):
        BackoffPolicy(jitter=2)


@pytest.mark.failure_mode
def test_os_disconnect_and_explicit_close_are_safe() -> None:
    events: list[ReaderEvent] = []
    session = SimulatedReaderSession([])
    stable_path = Path("/dev/serial/by-id/simulated")
    supervisor = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=stable_path),
        enumerate_devices=lambda: [SerialDevice(Path("/dev/ttyUSB0"), stable_path)],
        open_reader=lambda _path: session,
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(1)
    supervisor.os_disconnected()
    assert events[-1].event_type is ReaderEventType.READER_DISCONNECTED
    assert session.closed
    supervisor.close()


@pytest.mark.failure_mode
def test_controller_rejects_credentials_before_safe_start(
    system: tuple[
        AccessController,
        ManualClock,
        SimulatedRelay,
        InMemoryAuditSink,
        InMemoryNotificationQueue,
    ],
) -> None:
    controller = system[0]
    with pytest.raises(RuntimeError, match="not accepting"):
        from bridgewire.reader import ParsedRecord

        controller.process(ParsedRecord("0102030405"))
    assert controller.state is ControllerState.INITIALIZING
