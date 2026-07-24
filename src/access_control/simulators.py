from __future__ import annotations

from dataclasses import dataclass, field

from access_control.models import Event, RelayActivation


@dataclass
class SimulatedClock:
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class InMemoryCardRepository:
    authorized_cards: set[str] = field(default_factory=set[str])

    async def is_authorized(self, card_id: str) -> bool:
        return card_id in self.authorized_cards


@dataclass
class InMemoryEventStore:
    events: list[Event] = field(default_factory=list[Event])

    async def append(self, event: Event) -> None:
        self.events.append(event)


@dataclass
class SimulatedRelay:
    clock: SimulatedClock
    activations: list[RelayActivation] = field(default_factory=list[RelayActivation])
    _safe: bool = True

    @property
    def is_safe(self) -> bool:
        return self._safe

    async def pulse(self, duration: float) -> None:
        if duration <= 0:
            raise ValueError("relay duration must be positive")
        self._safe = False
        self.activations.append(
            RelayActivation(timestamp=self.clock.monotonic(), requested_duration=duration)
        )
        self.clock.advance(duration)
        self._safe = True

    async def make_safe(self) -> None:
        self._safe = True

    def simulate_unsafe_state(self) -> None:
        """Test-only hook representing an unexpectedly energized relay."""
        self._safe = False
