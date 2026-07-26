from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import uvicorn
from fastapi import FastAPI

DEFAULT_STARTUP_TIMEOUT_SECONDS = 2.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)


class ApiServerState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    START_FAILED = "start_failed"
    START_TIMED_OUT = "start_timed_out"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"
    STOP_TIMED_OUT = "stop_timed_out"


@dataclass(frozen=True, slots=True)
class ApiServerSnapshot:
    state: ApiServerState
    thread_alive: bool
    failures: tuple[BaseException, ...]


class ApiServerError(RuntimeError):
    pass


class ApiServerLifecycleError(ApiServerError):
    pass


class ApiServerStartupError(ApiServerError):
    pass


class ApiServerStartupTimeout(ApiServerStartupError):
    pass


class ApiServerShutdownTimeout(ApiServerError):
    pass


class ApiServer(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def snapshot(self) -> ApiServerSnapshot: ...


class UvicornThreadServer:
    """Single-use Uvicorn lifecycle subordinate to hardware safety."""

    def __init__(
        self,
        app: FastAPI,
        *,
        host: str,
        port: int,
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        if startup_timeout_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise ValueError("API lifecycle timeouts must be positive")
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                access_log=False,
            )
        )
        self._lock = threading.Lock()
        self._state = ApiServerState.NEW
        self._failures: list[BaseException] = []
        self._thread = threading.Thread(
            target=self._thread_main,
            name="bridgewire-read-only-api",
            daemon=True,
        )
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds

    def snapshot(self) -> ApiServerSnapshot:
        with self._lock:
            return ApiServerSnapshot(
                self._state,
                self._thread.is_alive(),
                tuple(self._failures),
            )

    def start(self) -> None:
        with self._lock:
            if self._state is not ApiServerState.NEW:
                raise ApiServerLifecycleError("server instances cannot be restarted")
            self._state = ApiServerState.STARTING
        try:
            self._thread.start()
        except Exception as exc:
            with self._lock:
                self._failures.append(exc)
                self._state = ApiServerState.START_FAILED
            raise ApiServerStartupError("API thread could not start") from exc
        deadline = time.monotonic() + self._startup_timeout_seconds
        while self._thread.is_alive() and not self._server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        with self._lock:
            state = self._state
            failure = self._failures[-1] if self._failures else None
            if self._server.started and state is ApiServerState.STARTING:
                self._state = ApiServerState.RUNNING
                return
        if not self._thread.is_alive():
            raise ApiServerStartupError("API server exited before startup") from failure
        timeout = ApiServerStartupTimeout("API server startup timed out")
        with self._lock:
            self._failures.append(timeout)
            self._state = ApiServerState.START_TIMED_OUT
        self._server.should_exit = True
        self._thread.join(self._shutdown_timeout_seconds)
        raise timeout

    def stop(self) -> None:
        with self._lock:
            state = self._state
            alive = self._thread.is_alive()
            if state is ApiServerState.NEW:
                self._state = ApiServerState.STOPPED
                return
            if not alive and state in {
                ApiServerState.START_FAILED,
                ApiServerState.FAILED,
                ApiServerState.STOPPED,
            }:
                return
            if not alive:
                self._state = ApiServerState.STOPPED
                return
            self._state = ApiServerState.STOPPING
            self._server.should_exit = True
        self._thread.join(self._shutdown_timeout_seconds)
        with self._lock:
            if self._thread.is_alive():
                timeout = ApiServerShutdownTimeout("API server shutdown timed out")
                self._failures.append(timeout)
                self._state = ApiServerState.STOP_TIMED_OUT
                raise timeout
            self._state = ApiServerState.STOPPED

    def _thread_main(self) -> None:
        try:
            self._server.run()
        except BaseException as exc:
            with self._lock:
                self._failures.append(exc)
                self._state = (
                    ApiServerState.START_FAILED
                    if self._state is ApiServerState.STARTING
                    else ApiServerState.FAILED
                )
            logger.error(
                "read-only API thread failed",
                extra={"api_state": self._state.value},
                exc_info=True,
            )
            return
        with self._lock:
            if self._state is ApiServerState.STARTING:
                startup_failure = ApiServerStartupError("API server exited before startup")
                self._failures.append(startup_failure)
                self._state = ApiServerState.START_FAILED
            elif self._state is ApiServerState.RUNNING:
                runtime_failure = ApiServerError("API server exited unexpectedly")
                self._failures.append(runtime_failure)
                self._state = ApiServerState.FAILED
                logger.error(
                    "read-only API server exited unexpectedly",
                    extra={"api_state": self._state.value},
                )
            elif self._state is ApiServerState.STOPPING:
                self._state = ApiServerState.STOPPED
