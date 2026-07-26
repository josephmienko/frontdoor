from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
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


class ApiServerStartupCancelled(ApiServerStartupError):
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
        thread_factory: Callable[..., threading.Thread] | None = None,
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
        make_thread = thread_factory or threading.Thread
        self._thread = make_thread(
            target=self._thread_main,
            name="bridgewire-read-only-api",
            daemon=True,
        )
        self._stop_complete = threading.Event()
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
        startup_error: ApiServerStartupError | None = None
        failure: BaseException | None = None
        with self._lock:
            state = self._state
            failure = self._failures[-1] if self._failures else None
            if state is ApiServerState.START_FAILED:
                startup_error = ApiServerStartupError("API server failed during startup")
            elif state is ApiServerState.FAILED:
                startup_error = ApiServerStartupError("API server failed immediately after startup")
            elif state in {ApiServerState.STOPPING, ApiServerState.STOPPED}:
                startup_error = ApiServerStartupCancelled("API server startup was cancelled")
            elif self._server.started and state is ApiServerState.STARTING:
                self._state = ApiServerState.RUNNING
                return
            elif not self._thread.is_alive():
                startup_error = ApiServerStartupError("API server exited before startup")
            else:
                timeout = ApiServerStartupTimeout("API server startup timed out")
                self._failures.append(timeout)
                self._state = ApiServerState.START_TIMED_OUT
                startup_error = timeout
        if not isinstance(startup_error, ApiServerStartupTimeout):
            assert startup_error is not None
            raise startup_error from failure
        self._server.should_exit = True
        self._thread.join(self._shutdown_timeout_seconds)
        raise startup_error

    def stop(self) -> None:
        wait_for_existing_stop = False
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
            if state is ApiServerState.STOPPING:
                wait_for_existing_stop = True
            else:
                self._state = ApiServerState.STOPPING
                self._stop_complete.clear()
                self._server.should_exit = True
        if wait_for_existing_stop:
            self._stop_complete.wait(self._shutdown_timeout_seconds)
            with self._lock:
                if self._state is ApiServerState.STOP_TIMED_OUT:
                    failure = self._failures[-1]
                    assert isinstance(failure, ApiServerShutdownTimeout)
                    raise failure
                return
        self._thread.join(self._shutdown_timeout_seconds)
        with self._lock:
            if self._thread.is_alive():
                timeout = ApiServerShutdownTimeout("API server shutdown timed out")
                self._failures.append(timeout)
                self._state = ApiServerState.STOP_TIMED_OUT
                self._stop_complete.set()
                raise timeout
            self._state = ApiServerState.STOPPED
            self._stop_complete.set()

    def _thread_main(self) -> None:
        log_message: str | None = None
        try:
            self._server.run()
        except BaseException as exc:
            with self._lock:
                self._failures.append(exc)
                self._state = (
                    ApiServerState.START_FAILED
                    if self._state is ApiServerState.STARTING and not self._server.started
                    else ApiServerState.FAILED
                )
                state = self._state
            logger.error(
                "read-only API thread failed",
                extra={"api_state": state.value},
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
                log_message = "read-only API server exited unexpectedly"
            elif self._state is ApiServerState.STOPPING:
                self._state = ApiServerState.STOPPED
        if log_message is not None:
            logger.error(
                log_message,
                extra={"api_state": ApiServerState.FAILED.value},
            )
