from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridgewire.audit import (
    AuditEvent,
    DurableNotificationQueue,
    EventType,
    Severity,
    SQLiteAuditSink,
)
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import SystemClock
from bridgewire.configuration import load_configuration
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationTracker
from bridgewire.gpio import RaspberryPiRelay
from bridgewire.reader import (
    ReaderDisconnectedError,
    ReaderEvent,
    ReaderIdentity,
    ReaderRecordStream,
    ReaderSession,
    ReaderSupervisor,
    SerialDevice,
)


class PosixSerialSession(ReaderSession):
    def __init__(self, path: Path, baud_rate: int) -> None:
        termios: Any = import_module("termios")
        tty: Any = import_module("tty")

        if baud_rate != 9600:
            raise ValueError("hardware reader requires 9600 baud")
        self._fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            tty.setraw(self._fd)
            attributes = termios.tcgetattr(self._fd)
            attributes[4] = termios.B9600
            attributes[5] = termios.B9600
            attributes[2] = (
                (attributes[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB))
                | termios.CS8
                | termios.CLOCAL
                | termios.CREAD
            )
            termios.tcsetattr(self._fd, termios.TCSANOW, attributes)
            termios.tcflush(self._fd, termios.TCIFLUSH)
        except Exception:
            os.close(self._fd)
            raise

    def read(self) -> bytes:
        import select

        if self._fd < 0:
            raise ReaderDisconnectedError("reader is closed")
        try:
            if not select.select([self._fd], [], [], 0.05)[0]:
                return b""
            return os.read(self._fd, 64)
        except OSError as exc:
            raise ReaderDisconnectedError("reader disconnected") from exc

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


def enumerate_serial_devices() -> Sequence[SerialDevice]:
    root = Path("/dev/serial/by-id")
    if not root.is_dir():
        return []
    devices: list[SerialDevice] = []
    for path in sorted(root.iterdir()):
        if not path.is_symlink():
            continue
        devices.append(_serial_device_from_by_id(path))
    return devices


def _serial_device_from_by_id(path: Path) -> SerialDevice:
    properties: dict[str, str] = {}
    try:
        output = subprocess.check_output(
            ["udevadm", "info", "--query=property", f"--name={path}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        properties = dict(
            line.split("=", 1) for line in output.splitlines() if "=" in line
        )
    except (OSError, subprocess.CalledProcessError):
        pass
    return SerialDevice(
        path=path.resolve(),
        by_id_path=path,
        vid=(
            int(properties["ID_VENDOR_ID"], 16)
            if "ID_VENDOR_ID" in properties
            else None
        ),
        pid=(
            int(properties["ID_MODEL_ID"], 16)
            if "ID_MODEL_ID" in properties
            else None
        ),
        serial_number=properties.get("ID_SERIAL_SHORT"),
        manufacturer=properties.get("ID_VENDOR"),
        product=properties.get("ID_MODEL"),
    )


def _write_health(path: Path, status: str, **details: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, **details}
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".health-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run_hardware_service(
    *,
    config_path: Path,
    authorization_path: Path,
    schema_path: Path,
    audit_path: Path,
    notification_path: Path,
    health_path: Path,
    gpio: object | None = None,
    enumerate_devices: Callable[[], Sequence[SerialDevice]] = enumerate_serial_devices,
    open_reader: Callable[[Path], ReaderSession] | None = None,
    install_signals: bool = True,
    stop_event: threading.Event | None = None,
) -> int:
    config = load_configuration(config_path)
    if config.relay.backend != "raspberry_pi":
        raise ValueError("hardware service requires relay.backend = raspberry_pi")
    clock = SystemClock()
    audit = SQLiteAuditSink(audit_path)
    authorization = AuthorizationStore(
        AuthorizationFile(json.loads(schema_path.read_text(encoding="utf-8")))
    )
    authorization.reload(authorization_path)
    relay = RaspberryPiRelay(gpio)
    notifications = DurableNotificationQueue(notification_path)
    controller = AccessController(
        authorization=authorization,
        relay=relay,
        audit=audit,
        notifications=notifications,
        clock=clock,
        escalation=EscalationTracker(config.escalation),
        release_seconds=config.gpio.release_seconds,
        gpio_channel=config.gpio.channel,
    )
    stopped = stop_event or threading.Event()
    if install_signals:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: stopped.set())
    stream = ReaderRecordStream(config.serial.maximum_record_bytes * 4)

    def emit_reader(event: ReaderEvent) -> None:
        audit.append(
            AuditEvent(
                event_type=EventType(event.event_type.value),
                severity=(
                    Severity.INFO
                    if event.event_type.value
                    in {"reader_connected", "reader_recovered", "reader_record_received"}
                    else Severity.WARNING
                ),
                timestamp=clock.now(),
                correlation=MappingProxyType({"attempt": event.attempt} if event.attempt else {}),
                reader_state=supervisor.health_state.value,
                controller_state=controller.state.value,
            )
        )

    def service_wait(seconds: float) -> bool:
        deadline = clock.monotonic() + seconds
        while clock.monotonic() < deadline:
            controller.tick()
            if stopped.wait(min(0.05, max(0.0, deadline - clock.monotonic()))):
                return False
        return True

    opener = open_reader or (lambda path: PosixSerialSession(path, config.serial.baud_rate))
    supervisor = ReaderSupervisor(
        identity=ReaderIdentity(
            by_id_path=config.reader_identity.by_id_path,
            vid=config.reader_identity.vid,
            pid=config.reader_identity.pid,
            serial_number=config.reader_identity.serial_number,
            manufacturer=config.reader_identity.manufacturer,
            product=config.reader_identity.product,
        ),
        enumerate_devices=enumerate_devices,
        open_reader=opener,
        wait=service_wait,
        emit=emit_reader,
        backoff=config.backoff,
        monotonic=clock.monotonic,
    )
    try:
        controller.start()
        _write_health(health_path, "degraded", reason="reader_connecting")
        while not stopped.is_set():
            if not supervisor.connected:
                if supervisor.connect_until_ready(1):
                    _write_health(health_path, "ready", reader="connected")
                    print(json.dumps({"event": "service_ready", "reader": "connected"}), flush=True)
                continue
            for record in supervisor.read_records_once(stream):
                controller.process(record)
            controller.tick()
        return 0
    except BaseException:
        _write_health(health_path, "faulted")
        if controller.state.value not in {"initializing", "stopped"}:
            controller.recoverable_failure()
        raise
    finally:
        supervisor.close()
        controller.shutdown()
        _write_health(health_path, "stopped")
        audit.close()
