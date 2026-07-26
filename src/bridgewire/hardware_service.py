from __future__ import annotations

import json
import logging
import signal
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from fastapi import FastAPI

from bridgewire import __version__
from bridgewire.adapters.health.file_reporter import FileHealthReporter
from bridgewire.adapters.http.api import ApplicationContainer, create_app
from bridgewire.adapters.http.uvicorn_server import (
    ApiServer,
    ApiServerState,
    UvicornThreadServer,
)
from bridgewire.adapters.persistence.sqlite_audit_reader import SQLiteAuditReader
from bridgewire.adapters.reader.posix_serial import (
    PosixSerialSession,
    enumerate_serial_devices,
)
from bridgewire.application.access_service import AccessService
from bridgewire.application.query_service import ReadOnlyQueryService
from bridgewire.application.runtime import BridgewireRuntime
from bridgewire.application.status_service import OperationalSnapshotStore, StatusService
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
    "enumerate_serial_devices",
    "run_hardware_service",
]
logger = logging.getLogger(__name__)


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
    api_server_factory: Callable[[FastAPI, str, int], ApiServer] | None = None,
) -> int:
    """Production composition root; OS concerns remain at this host boundary."""

    config = load_configuration(config_path)
    if config.relay.backend != "raspberry_pi":
        raise ValueError("hardware service requires relay.backend = raspberry_pi")
    clock = SystemClock()
    application_started_at = clock.now()
    audit: SQLiteAuditSink | None = None
    relay: RaspberryPiRelay | None = None
    access: AccessService | None = None
    runtime: BridgewireRuntime | None = None
    api_server: ApiServer | None = None
    failures: list[BaseException] = []
    observed_api_failures: set[int] = set()
    api_state = ApiServerState.STOPPED if not config.api.enabled else ApiServerState.NEW
    health = FileHealthReporter(health_path)

    def report_api_state(state: ApiServerState, *, failure: bool = False) -> None:
        nonlocal api_state
        api_state = state
        try:
            status = (
                "degraded"
                if failure
                else "ready"
                if state is ApiServerState.RUNNING
                else "stopped"
                if state in {ApiServerState.STOPPING, ApiServerState.STOPPED}
                else "degraded"
            )
            health.report(
                status,
                access_control=(
                    "stopped"
                    if state in {ApiServerState.STOPPING, ApiServerState.STOPPED}
                    else "operational"
                ),
                api={"enabled": config.api.enabled, "state": state.value},
            )
        except BaseException as exc:
            if id(exc) not in observed_api_failures:
                observed_api_failures.add(id(exc))
                failures.append(exc)

    def retain_api_failure(exc: BaseException, state: ApiServerState) -> None:
        if id(exc) in observed_api_failures:
            return
        observed_api_failures.add(id(exc))
        failures.append(exc)
        logger.error(
            "read-only API lifecycle failure",
            extra={"api_state": state.value},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        report_api_state(state, failure=True)

    def observe_api() -> None:
        nonlocal api_state
        if api_server is None:
            return
        snapshot = api_server.snapshot()
        failure_state = snapshot.state in {
            ApiServerState.START_FAILED,
            ApiServerState.START_TIMED_OUT,
            ApiServerState.FAILED,
            ApiServerState.STOP_TIMED_OUT,
        }
        if snapshot.state is not api_state and (not failure_state or not snapshot.failures):
            report_api_state(snapshot.state, failure=failure_state)
        for api_failure in snapshot.failures:
            retain_api_failure(api_failure, snapshot.state)

    def wait(seconds: float) -> bool:
        assert runtime is not None
        return runtime.cooperative_wait(seconds)

    def emit(event: ReaderEvent) -> None:
        assert runtime is not None
        runtime.record_reader_event(event)

    try:
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
        access = AccessService(controller)
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
        operational_snapshots = OperationalSnapshotStore()
        runtime = BridgewireRuntime(
            access=access,
            reader=reader,
            health_reporter=health,
            audit=audit,
            clock=clock,
            maximum_record_bytes=config.serial.maximum_record_bytes,
            stop_event=stop_event,
            operational_snapshots=operational_snapshots,
        )
        if install_signals:
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(
                    signum,
                    lambda _signum, _frame: runtime.request_shutdown(),
                )
        runtime.start()
        if config.api.enabled:
            try:
                audit_reader = SQLiteAuditReader(audit_path)
                status_service = StatusService(
                    operational=operational_snapshots,
                    authorization=authorization,
                    audit=audit_reader,
                    notifications=notifications,
                    clock=clock,
                    software_version=__version__,
                    application_started_at=application_started_at,
                )
                query_service = ReadOnlyQueryService(
                    status=status_service,
                    events=audit_reader,
                    maximum_event_page_size=config.api.max_event_page_size,
                    operational_snapshot_stale_after_seconds=(
                        config.api.operational_snapshot_stale_after_seconds
                    ),
                )
                app = create_app(ApplicationContainer(query_service))
                factory = api_server_factory or (
                    lambda api_app, host, port: UvicornThreadServer(api_app, host=host, port=port)
                )
                api_server = factory(app, config.api.host, config.api.port)
                report_api_state(ApiServerState.STARTING)
                api_server.start()
                observe_api()
            except Exception as exc:
                state = (
                    api_server.snapshot().state
                    if api_server is not None
                    else ApiServerState.START_FAILED
                )
                retain_api_failure(exc, state)
        else:
            health.report(
                "degraded",
                reason="reader_connecting",
                api={"enabled": False, "state": "disabled"},
            )

        def ready() -> None:
            print(
                json.dumps({"event": "service_ready", "reader": "connected"}),
                flush=True,
            )
            if config.api.enabled:
                report_api_state(api_state)
            else:
                health.report(
                    "ready",
                    reader="connected",
                    api={"enabled": False, "state": "disabled"},
                )

        runtime.run(
            on_ready=ready,
            on_iteration=observe_api if config.api.enabled else None,
        )
    except BaseException as exc:
        failures.append(exc)
        if runtime is not None:
            for action in (runtime.report_fault, runtime.handle_failure):
                try:
                    action()
                except BaseException as cleanup_exc:
                    failures.append(cleanup_exc)
    finally:
        # The hardware runtime secures and cleans the relay before optional
        # HTTP shutdown is attempted. Uvicorn never owns GPIO lifecycle.
        if runtime is not None:
            try:
                runtime.shutdown()
            except BaseException as exc:
                failures.append(exc)
        # Final hardware-owned fallback: RaspberryPiRelay.cleanup commands LOW
        # before cleanup and is idempotent after successful controller shutdown.
        if relay is not None:
            try:
                relay.cleanup()
            except BaseException as exc:
                failures.append(exc)
        if api_server is not None:
            try:
                api_server.stop()
            except BaseException as exc:
                retain_api_failure(exc, api_server.snapshot().state)
            observe_api()
        if audit is not None:
            try:
                audit.close()
            except BaseException as exc:
                failures.append(exc)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("hardware service failed", failures)
    return 0
