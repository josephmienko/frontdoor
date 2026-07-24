from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bridgewire.interfaces import Clock


class RelayActionType(StrEnum):
    SETUP = "setup"
    LOW = "low"
    HIGH = "high"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class RelayAction:
    action: RelayActionType
    timestamp: float
    channel: int


class SimulatedRelay:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.actions: list[RelayAction] = []
        self.is_high = False
        self.channel: int | None = None
        self.numbering: str | None = None
        self.fail_next_high = False
        self.fail_next_low = False

    def setup(self, *, numbering: str, channel: int) -> None:
        self.numbering = numbering
        self.channel = channel
        self.actions.append(RelayAction(RelayActionType.SETUP, self._clock.monotonic(), channel))

    def command(self, high: bool) -> None:
        if self.channel is None:
            raise RuntimeError("relay has not been set up")
        if high and self.fail_next_high:
            self.fail_next_high = False
            raise RuntimeError("injected HIGH failure")
        if not high and self.fail_next_low:
            self.fail_next_low = False
            raise RuntimeError("injected LOW failure")
        self.is_high = high
        action = RelayActionType.HIGH if high else RelayActionType.LOW
        self.actions.append(RelayAction(action, self._clock.monotonic(), self.channel))

    def cleanup(self) -> None:
        if self.channel is None:
            return
        self.actions.append(
            RelayAction(RelayActionType.CLEANUP, self._clock.monotonic(), self.channel)
        )
