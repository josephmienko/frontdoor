from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bridgewire.audit import AuditEvent
    from bridgewire.authorization import AuthorizationOutcome


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...


class Relay(Protocol):
    def setup(self, *, numbering: str, channel: int) -> None: ...

    def command(self, high: bool) -> None: ...

    def cleanup(self) -> None: ...


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class NotificationQueue(Protocol):
    def enqueue(self, event: AuditEvent) -> None: ...


class HealthReporter(Protocol):
    def report(self, status: str, **details: object) -> None: ...


class AuthorizationSource(Protocol):
    def classify(self, credential: str) -> AuthorizationOutcome: ...


class InterruptibleWaiter(Protocol):
    def wait(self, seconds: float) -> bool:
        """Return true after waiting or false when shutdown interrupts the wait."""


class DeviceEnumerator(Protocol):
    def list_devices(self) -> Sequence[object]: ...


class ReaderOpener(Protocol):
    def open(self, path: Path) -> object: ...


JitterSource = Callable[[float], float]
Correlation = Mapping[str, str | int | float | bool | None]
