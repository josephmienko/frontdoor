from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire.adapters.health.file_reporter import FileHealthReporter
from bridgewire.adapters.reader.posix_serial import (
    UDEVADM_TIMEOUT_SECONDS,
    PosixSerialSession,
    _serial_device_from_by_id,
    enumerate_serial_devices,
)
from bridgewire.audit import AuditEvent, EventType, Severity, SQLiteAuditSink
from bridgewire.cli import main
from bridgewire.gpio import RaspberryPiRelay
from bridgewire.hardware_service import (
    PosixSerialSession as CompatibilityPosixSerialSession,
)
from bridgewire.hardware_service import (
    enumerate_serial_devices as compatibility_enumerate_serial_devices,
)
from bridgewire.hardware_service import run_hardware_service
from bridgewire.reader import SerialDevice


class FakeGpio:
    BCM = 11
    OUT = 1
    LOW = 0
    HIGH = 1

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def setwarnings(self, enabled: bool) -> None:
        self.calls.append(("setwarnings", enabled))

    def setmode(self, mode: int) -> None:
        self.calls.append(("setmode", mode))

    def setup(self, channel: int, mode: int, *, initial: int) -> None:
        self.calls.append(("setup", channel, mode, initial))

    def output(self, channel: int, level: int) -> None:
        self.calls.append(("output", channel, level))

    def cleanup(self, channel: int) -> None:
        self.calls.append(("cleanup", channel))


@pytest.mark.unit
def test_physical_relay_owns_safe_gpio_lifecycle() -> None:
    gpio = FakeGpio()
    relay = RaspberryPiRelay(gpio)
    relay.setup(numbering="BCM", channel=23)
    relay.command(True)
    relay.command(False)
    relay.cleanup()
    relay.cleanup()
    assert gpio.calls == [
        ("setwarnings", False),
        ("setmode", gpio.BCM),
        ("setup", 23, gpio.OUT, gpio.LOW),
        ("output", 23, gpio.HIGH),
        ("output", 23, gpio.LOW),
        ("output", 23, gpio.LOW),
        ("cleanup", 23),
    ]


@pytest.mark.unit
def test_physical_relay_rejects_unapproved_pin() -> None:
    with pytest.raises(ValueError, match="BCM channel 23"):
        RaspberryPiRelay(FakeGpio()).setup(numbering="BCM", channel=24)


@pytest.mark.unit
def test_sqlite_audit_survives_reopen_without_sensitive_context(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    sink = SQLiteAuditSink(path)
    sink.append(
        AuditEvent(
            EventType.RELAY_ASSERTED,
            Severity.INFO,
            datetime.now(UTC),
            event_id="stable-event-id",
        )
    )
    sink.close()
    reopened = SQLiteAuditSink(path)
    assert reopened.count() == 1
    reopened.close()
    assert "credential" not in path.read_bytes().decode("latin1").lower()


@pytest.mark.unit
def test_udev_identity_populates_non_unique_ch340_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = "\n".join(
        [
            "ID_VENDOR_ID=1a86",
            "ID_MODEL_ID=7523",
            "ID_VENDOR=1a86",
            "ID_MODEL=USB_Serial",
        ]
    )
    observed: dict[str, object] = {}

    def check_output(*_args: object, **kwargs: object) -> str:
        observed.update(kwargs)
        return output

    monkeypatch.setattr(
        "bridgewire.adapters.reader.posix_serial.subprocess.check_output", check_output
    )
    device = _serial_device_from_by_id(Path("/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"))
    assert device is not None
    assert (device.vid, device.pid, device.serial_number) == (0x1A86, 0x7523, None)
    assert observed["timeout"] == UDEVADM_TIMEOUT_SECONDS == 0.5


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(1, ["udevadm"]),
        subprocess.TimeoutExpired(["udevadm"], 0.5),
        OSError("udevadm unavailable"),
    ],
)
def test_udev_discovery_failures_are_bounded_and_skipped(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    def fail(*_args: object, **_kwargs: object) -> str:
        raise failure

    monkeypatch.setattr("bridgewire.adapters.reader.posix_serial.subprocess.check_output", fail)
    assert _serial_device_from_by_id(Path("/dev/serial/by-id/example")) is None


@pytest.mark.unit
def test_health_report_contains_utc_freshness_timestamp(tmp_path: Path) -> None:
    instant = datetime(2026, 7, 25, 19, 30, tzinfo=UTC)
    path = tmp_path / "health.json"
    FileHealthReporter(path, now=lambda: instant).report("ready", reader="connected")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "reader": "connected",
        "reported_at": "2026-07-25T19:30:00+00:00",
        "status": "ready",
    }


@pytest.mark.unit
def test_health_atomic_replace_failure_preserves_previous_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "health.json"
    path.write_text('{"status":"old"}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("bridgewire.adapters.health.file_reporter.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        FileHealthReporter(path).report("ready")
    assert path.read_text(encoding="utf-8") == '{"status":"old"}\n'
    assert list(tmp_path.glob(".health-*")) == []


@pytest.mark.unit
def test_hardware_service_compatibility_exports_are_deliberate() -> None:
    import bridgewire.hardware_service as hardware_service

    assert CompatibilityPosixSerialSession is PosixSerialSession
    assert compatibility_enumerate_serial_devices is enumerate_serial_devices
    assert "_serial_device_from_by_id" not in hardware_service.__all__


@pytest.mark.unit
def test_hardware_cli_forwards_explicit_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "bridgewire.hardware_service.run_hardware_service",
        lambda **_kwargs: 7,
    )
    paths = [tmp_path / name for name in ("c", "s", "a", "d", "n", "h")]
    assert (
        main(
            [
                "serve-hardware",
                "--config",
                str(paths[0]),
                "--schema",
                str(paths[1]),
                "--authorization",
                str(paths[2]),
                "--audit",
                str(paths[3]),
                "--notifications",
                str(paths[4]),
                "--health",
                str(paths[5]),
            ]
        )
        == 7
    )


@pytest.mark.integration
def test_hardware_composition_runs_full_path_and_shuts_down_low(
    tmp_path: Path,
    repo_root: Path,
    authorization_fixture_root: Path,
    schema_root: Path,
) -> None:
    import sqlite3
    import threading

    stop = threading.Event()
    stable = Path("/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0")

    class Session:
        calls = 0

        def read(self) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"\x02010203040501\r\n\x03"
            stop.set()
            return b""

        def close(self) -> None:
            pass

    gpio = FakeGpio()
    audit_path = tmp_path / "audit.sqlite3"
    assert (
        run_hardware_service(
            config_path=repo_root / "configs" / "hardware-bench.toml",
            authorization_path=authorization_fixture_root / "valid.csv",
            schema_path=schema_root / "authorization-file" / "schema.json",
            audit_path=audit_path,
            notification_path=tmp_path / "notifications.jsonl",
            health_path=tmp_path / "health.json",
            gpio=gpio,
            enumerate_devices=lambda: [
                SerialDevice(Path("/dev/ttyUSB0"), stable, vid=0x1A86, pid=0x7523)
            ],
            open_reader=lambda _path: Session(),
            install_signals=False,
            stop_event=stop,
        )
        == 0
    )
    connection = sqlite3.connect(audit_path)
    try:
        events = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM audit_events ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        connection.close()
    assert events == [
        "service_started",
        "reader_connecting",
        "reader_connected",
        "reader_record_received",
        "credential_authorized",
        "relay_asserted",
        "service_shutdown",
    ]
    assert ("output", 23, gpio.HIGH) in gpio.calls
    assert gpio.calls[-2:] == [("output", 23, gpio.LOW), ("cleanup", 23)]
    assert "0102030405" not in audit_path.read_bytes().decode("latin1")
    assert '"status": "stopped"' in (tmp_path / "health.json").read_text(encoding="utf-8")
