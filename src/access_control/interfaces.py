from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol

from access_control.models import Event


class CardSource(Protocol):
    def cards(self) -> AsyncIterator[str]: ...


class ExitButton(Protocol):
    def presses(self) -> AsyncIterator[None]: ...


class Relay(Protocol):
    @property
    def is_safe(self) -> bool: ...

    async def pulse(self, duration: float) -> None: ...

    async def make_safe(self) -> None: ...


class CardRepository(Protocol):
    async def is_authorized(self, card_id: str) -> bool: ...


class EventStore(Protocol):
    async def append(self, event: Event) -> None: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...


class HealthProbe(Protocol):
    async def check(self) -> bool: ...


EventCallback = Callable[[Event], None]
