from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire import __version__
from bridgewire.audit import (
    AuditEvent,
    DurableNotificationQueue,
    EventType,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
    NotificationWorker,
    Severity,
)
from bridgewire.authorization import AuthorizationOutcome, AuthorizationStore
from bridgewire.cli import main
from bridgewire.clock import ManualClock
from bridgewire.configuration import ConfigurationError, load_configuration
from bridgewire.controller import AccessController, ControllerState
from bridgewire.escalation import EscalationTracker
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
    assert capsys.readouterr().out.strip() == __version__
    assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", __version__)


@pytest.mark.unit
def test_project_and_runtime_versions_match(repo_root: Path) -> None:
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__


@pytest.mark.integration
def test_resource_tests_run_outside_repository_working_directory(
    tmp_path: Path, repo_root: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(
                repo_root / "tests" / "test_authorization.py::test_existing_csv_shape_and_outcomes"
            ),
            "-q",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


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
def test_simulated_service_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class StopAfterFirstWait(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            self.set()
            return True

    monkeypatch.setattr("bridgewire.cli.threading.Event", StopAfterFirstWait)
    monkeypatch.setattr("bridgewire.cli.signal.signal", lambda *_args: None)
    assert main(["serve-simulated", "--interval", "0.01"]) == 0
    assert capsys.readouterr().out


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


@pytest.mark.failure_mode
def test_external_delivery_failures_retain_pending_event_for_every_retry(
    tmp_path: Path,
) -> None:
    class FailingEndpoint:
        def __init__(self) -> None:
            self.attempts = 0

        def deliver(self, _event: dict[str, object]) -> None:
            self.attempts += 1
            raise OSError("simulated endpoint unavailable")

    queue = DurableNotificationQueue(tmp_path / "notifications.jsonl")
    event = AuditEvent(EventType.ESCALATION_CRITICAL, Severity.CRITICAL, datetime.now(UTC))
    queue.enqueue(event)
    endpoint = FailingEndpoint()
    worker = NotificationWorker(queue, endpoint)
    for expected_attempts in (1, 2):
        with pytest.raises(OSError, match="unavailable"):
            worker.deliver_one()
        assert endpoint.attempts == expected_attempts
        assert [item["event_id"] for item in queue.pending()] == [event.event_id]


@pytest.mark.failure_mode
def test_local_queue_failure_does_not_stop_controller(
    authorization: AuthorizationStore,
) -> None:
    class FailingQueue:
        def enqueue(self, _event: AuditEvent) -> None:
            raise OSError("simulated local queue failure")

    clock = ManualClock()
    relay = SimulatedRelay(clock)
    audit = InMemoryAuditSink()
    controller = AccessController(
        authorization=authorization,
        relay=relay,
        audit=audit,
        notifications=FailingQueue(),
        clock=clock,
        escalation=EscalationTracker(),
    )
    from bridgewire.reader import ParsedRecord

    controller.start()
    for credential in ("FFFFFFFFFF",) * 5:
        controller.process(ParsedRecord(credential))
    result = controller.process(ParsedRecord("0102030405"))
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert relay.is_high
    assert EventType.NOTIFICATION_DELIVERY_FAILED in [event.event_type for event in audit.events]


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
