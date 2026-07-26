from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire.adapters.health.file_reporter import FileHealthReporter
from bridgewire.adapters.http.uvicorn_server import (
    ApiServerLifecycleError,
    ApiServerShutdownTimeout,
    ApiServerSnapshot,
    ApiServerStartupError,
    ApiServerStartupTimeout,
    ApiServerState,
    UvicornThreadServer,
)
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
def test_uvicorn_lifecycle_is_explicit_idempotent_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Server:
        started = False
        should_exit = False

        def __init__(self, config: object) -> None:
            observed["config"] = config

        def run(self) -> None:
            self.started = True
            while not self.should_exit:
                import time

                time.sleep(0.001)

    def config(_app: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return object()

    monkeypatch.setattr("bridgewire.adapters.http.uvicorn_server.uvicorn.Config", config)
    monkeypatch.setattr("bridgewire.adapters.http.uvicorn_server.uvicorn.Server", Server)
    server = UvicornThreadServer(
        object(),  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8080,
        startup_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )
    server.start()
    assert server.snapshot().state is ApiServerState.RUNNING
    with pytest.raises(ApiServerLifecycleError):
        server.start()
    server.stop()
    server.stop()
    assert server.snapshot().state is ApiServerState.STOPPED
    with pytest.raises(ApiServerLifecycleError):
        server.start()
    assert observed["host"] == "127.0.0.1"
    assert observed["port"] == 8080


@pytest.mark.unit
def test_uvicorn_retains_thread_failure_before_and_after_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_failure = OSError("before startup")

    class BeforeServer:
        started = False
        should_exit = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            raise before_failure

    monkeypatch.setattr(
        "bridgewire.adapters.http.uvicorn_server.uvicorn.Config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("bridgewire.adapters.http.uvicorn_server.uvicorn.Server", BeforeServer)
    before = UvicornThreadServer(
        object(),  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8080,
        startup_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )
    with pytest.raises(ApiServerStartupError):
        before.start()
    snapshot = before.snapshot()
    assert snapshot.state is ApiServerState.START_FAILED
    assert before_failure in snapshot.failures
    with pytest.raises(ApiServerLifecycleError):
        before.start()

    after_failure = OSError("after startup")
    release = threading.Event()

    class AfterServer:
        started = False
        should_exit = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            self.started = True
            release.wait(1)
            raise after_failure

    monkeypatch.setattr("bridgewire.adapters.http.uvicorn_server.uvicorn.Server", AfterServer)
    after = UvicornThreadServer(
        object(),  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8080,
        startup_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )
    after.start()
    release.set()
    deadline = time.monotonic() + 1
    while after.snapshot().state is ApiServerState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.001)
    snapshot = after.snapshot()
    assert snapshot.state is ApiServerState.FAILED
    assert after_failure in snapshot.failures


@pytest.mark.unit
def test_uvicorn_timeout_retains_lingering_thread_for_later_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    class Server:
        started = False
        should_exit = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            release.wait(1)

    monkeypatch.setattr(
        "bridgewire.adapters.http.uvicorn_server.uvicorn.Config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr("bridgewire.adapters.http.uvicorn_server.uvicorn.Server", Server)
    server = UvicornThreadServer(
        object(),  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8080,
        startup_timeout_seconds=0.01,
        shutdown_timeout_seconds=0.01,
    )
    with pytest.raises(ApiServerStartupTimeout):
        server.start()
    assert server.snapshot().state is ApiServerState.START_TIMED_OUT
    assert server.snapshot().thread_alive
    with pytest.raises(ApiServerShutdownTimeout):
        server.stop()
    assert server.snapshot().state is ApiServerState.STOP_TIMED_OUT
    release.set()
    server.stop()
    assert server.snapshot().state is ApiServerState.STOPPED


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
            api_server_factory=lambda *_args: pytest.fail(
                "disabled API must not construct a server"
            ),
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


@pytest.mark.integration
@pytest.mark.parametrize(("fail_start", "fail_stop"), [(True, False), (False, True)])
def test_optional_api_failure_cannot_skip_processing_or_relay_cleanup(
    tmp_path: Path,
    repo_root: Path,
    authorization_fixture_root: Path,
    schema_root: Path,
    fail_start: bool,
    fail_stop: bool,
) -> None:
    import threading

    stop = threading.Event()
    source = (repo_root / "configs" / "hardware-bench.toml").read_text(encoding="utf-8")
    config_path = tmp_path / "hardware-api.toml"
    config_path.write_text(
        source.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
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

    lifecycle: list[str] = []

    class OrderedGpio(FakeGpio):
        def output(self, channel: int, level: int) -> None:
            if level == self.LOW and ("output", channel, self.HIGH) in self.calls:
                lifecycle.append("relay_low")
            super().output(channel, level)

        def cleanup(self, channel: int) -> None:
            lifecycle.append("relay_cleanup")
            super().cleanup(channel)

    gpio = OrderedGpio()

    class ApiServer:
        error: OSError | None = None

        def start(self) -> None:
            lifecycle.append("api_start")
            if fail_start:
                self.error = OSError("API startup failed")
                raise self.error

        def stop(self) -> None:
            lifecycle.append("api_stop")
            assert ("output", 23, gpio.LOW) in gpio.calls
            if fail_stop:
                self.error = OSError("API shutdown failed")
                raise self.error

        def snapshot(self) -> ApiServerSnapshot:
            return ApiServerSnapshot(
                (
                    ApiServerState.STOP_TIMED_OUT
                    if fail_stop and self.error is not None
                    else ApiServerState.START_FAILED
                    if fail_start and self.error is not None
                    else ApiServerState.RUNNING
                ),
                False,
                (self.error,) if self.error is not None else (),
            )

    def factory(_app: object, host: str, port: int) -> ApiServer:
        assert (host, port) == ("127.0.0.1", 8080)
        return ApiServer()

    with pytest.raises(BaseException, match="API"):
        run_hardware_service(
            config_path=config_path,
            authorization_path=authorization_fixture_root / "valid.csv",
            schema_path=schema_root / "authorization-file" / "schema.json",
            audit_path=tmp_path / "audit.sqlite3",
            notification_path=tmp_path / "notifications.jsonl",
            health_path=tmp_path / "health.json",
            gpio=gpio,
            enumerate_devices=lambda: [
                SerialDevice(Path("/dev/ttyUSB0"), stable, vid=0x1A86, pid=0x7523)
            ],
            open_reader=lambda _path: Session(),
            install_signals=False,
            stop_event=stop,
            api_server_factory=factory,
        )
    assert ("output", 23, gpio.HIGH) in gpio.calls
    assert gpio.calls[-2:] == [("output", 23, gpio.LOW), ("cleanup", 23)]
    assert lifecycle[0] == "api_start"
    assert lifecycle.index("relay_low") < lifecycle.index("relay_cleanup")
    assert lifecycle.index("relay_cleanup") < lifecycle.index("api_stop")


@pytest.mark.integration
@pytest.mark.parametrize(
    "target",
    [
        "SQLiteAuditReader",
        "StatusService",
        "ReadOnlyQueryService",
        "create_app",
        "UvicornThreadServer",
    ],
)
def test_complete_api_construction_is_isolated_from_hardware_processing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_root: Path,
    authorization_fixture_root: Path,
    schema_root: Path,
    target: str,
) -> None:
    stop = threading.Event()
    source = (repo_root / "configs" / "hardware-bench.toml").read_text(encoding="utf-8")
    config_path = tmp_path / "hardware-api.toml"
    config_path.write_text(
        source.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
    failure = OSError(f"{target} failed")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(f"bridgewire.hardware_service.{target}", fail)
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
    with pytest.raises(BaseException, match=target):
        run_hardware_service(
            config_path=config_path,
            authorization_path=authorization_fixture_root / "valid.csv",
            schema_path=schema_root / "authorization-file" / "schema.json",
            audit_path=tmp_path / "audit.sqlite3",
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
    assert ("output", 23, gpio.HIGH) in gpio.calls
    assert gpio.calls[-2:] == [("output", 23, gpio.LOW), ("cleanup", 23)]


@pytest.mark.integration
def test_api_runtime_failure_is_observed_while_hardware_continues(
    tmp_path: Path,
    repo_root: Path,
    authorization_fixture_root: Path,
    schema_root: Path,
) -> None:
    stop = threading.Event()
    source = (repo_root / "configs" / "hardware-bench.toml").read_text(encoding="utf-8")
    config_path = tmp_path / "hardware-api.toml"
    config_path.write_text(
        source.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
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

    api_failure = OSError("sensitive bind detail")

    class ApiServer:
        observations = 0

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def snapshot(self) -> ApiServerSnapshot:
            self.observations += 1
            if self.observations >= 2:
                return ApiServerSnapshot(ApiServerState.FAILED, False, (api_failure,))
            return ApiServerSnapshot(ApiServerState.RUNNING, True, ())

    gpio = FakeGpio()
    health_path = tmp_path / "health.json"
    with pytest.raises(BaseException, match="sensitive bind detail"):
        run_hardware_service(
            config_path=config_path,
            authorization_path=authorization_fixture_root / "valid.csv",
            schema_path=schema_root / "authorization-file" / "schema.json",
            audit_path=tmp_path / "audit.sqlite3",
            notification_path=tmp_path / "notifications.jsonl",
            health_path=health_path,
            gpio=gpio,
            enumerate_devices=lambda: [
                SerialDevice(Path("/dev/ttyUSB0"), stable, vid=0x1A86, pid=0x7523)
            ],
            open_reader=lambda _path: Session(),
            install_signals=False,
            stop_event=stop,
            api_server_factory=lambda _app, _host, _port: ApiServer(),
        )
    assert ("output", 23, gpio.HIGH) in gpio.calls
    assert gpio.calls[-2:] == [("output", 23, gpio.LOW), ("cleanup", 23)]
    assert "sensitive bind detail" not in health_path.read_text(encoding="utf-8")
