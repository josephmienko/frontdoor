from __future__ import annotations

import json
import signal
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from bridgewire.adapters.health.file_reporter import FileHealthReporter
from bridgewire.adapters.reader.posix_serial import (
    PosixSerialSession,
    _serial_device_from_by_id,
    enumerate_serial_devices,
)
from bridgewire.application.access_service import AccessService
from bridgewire.application.runtime import BridgewireRuntime
from bridgewire.audit import DurableNotificationQueue, SQLiteAuditSink
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import SystemClock
from bridgewire.configuration import load_configuration
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationTracker
from bridgewire.gpio import RaspberryPiRelay
from bridgewire.reader import ReaderEvent, ReaderSession, ReaderSupervisor, SerialDevice

__all__ = [
    "PosixSerialSession",
    "_serial_device_from_by_id",
    "enumerate_serial_devices",
    "run_hardware_service",
]


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
    """Production composition root; OS concerns remain at this host boundary."""

    config = load_configuration(config_path)
    if config.relay.backend != "raspberry_pi":
        raise ValueError("hardware service requires relay.backend = raspberry_pi")
    clock = SystemClock()
    audit = SQLiteAuditSink(audit_path)
    authorization = AuthorizationStore(
        AuthorizationFile(json.loads(schema_path.read_text(encoding="utf-8")))
    )
    authorization.reload(authorization_path)
    controller = AccessController(
        authorization=authorization,
        relay=RaspberryPiRelay(gpio),
        audit=audit,
        notifications=DurableNotificationQueue(notification_path),
        clock=clock,
        escalation=EscalationTracker(config.escalation),
        release_seconds=config.gpio.release_seconds,
        gpio_channel=config.gpio.channel,
    )
    access = AccessService(controller)
    runtime: BridgewireRuntime | None = None

    def wait(seconds: float) -> bool:
        assert runtime is not None
        return runtime.cooperative_wait(seconds)

    def emit(event: ReaderEvent) -> None:
        assert runtime is not None
        runtime.record_reader_event(event)

    opener = open_reader or (lambda path: PosixSerialSession(path, config.serial.baud_rate))
    reader = ReaderSupervisor(
        identity=config.reader_identity,
        enumerate_devices=enumerate_devices,
        open_reader=opener,
        wait=wait,
        emit=emit,
        backoff=config.backoff,
        monotonic=clock.monotonic,
    )
    runtime = BridgewireRuntime(
        access=access,
        reader=reader,
        health_reporter=FileHealthReporter(health_path),
        audit=audit,
        clock=clock,
        maximum_record_bytes=config.serial.maximum_record_bytes,
        stop_event=stop_event,
    )
    if install_signals:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: runtime.request_shutdown())
    try:
        runtime.start()
        while not runtime.shutdown_requested:
            was_connected = reader.connected
            runtime.run_once()
            if not was_connected and reader.connected:
                print(
                    json.dumps({"event": "service_ready", "reader": "connected"}),
                    flush=True,
                )
        return 0
    except BaseException:
        runtime.report_fault()
        if access.controller_state.value not in {"initializing", "stopped"}:
            access.recoverable_failure()
        raise
    finally:
        runtime.shutdown()
        audit.close()
