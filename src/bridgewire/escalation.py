from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum


class EscalationLevel(IntEnum):
    NONE = 0
    WARNING = 1
    CRITICAL = 2


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    warning_count: int = 3
    warning_window: float = 60.0
    critical_count: int = 5
    critical_window: float = 300.0
    reset_after: float = 900.0

    def __post_init__(self) -> None:
        if self.warning_count <= 0 or self.critical_count <= self.warning_count:
            raise ValueError("escalation thresholds must be positive and increasing")
        if (
            self.warning_window <= 0
            or self.critical_window < self.warning_window
            or self.reset_after <= 0
        ):
            raise ValueError("escalation windows must be positive and ordered")


class EscalationTracker:
    def __init__(self, policy: EscalationPolicy | None = None) -> None:
        self._policy = policy or EscalationPolicy()
        self._events: deque[float] = deque()
        self._level = EscalationLevel.NONE
        self._last_event: float | None = None

    @property
    def level(self) -> EscalationLevel:
        return self._level

    def record(self, timestamp: float) -> EscalationLevel:
        if (
            self._last_event is not None
            and timestamp - self._last_event >= self._policy.reset_after
        ):
            self._events.clear()
            self._level = EscalationLevel.NONE
        self._last_event = timestamp
        self._events.append(timestamp)
        oldest_relevant = timestamp - self._policy.critical_window
        while self._events and self._events[0] < oldest_relevant:
            self._events.popleft()
        critical_count = sum(
            event >= timestamp - self._policy.critical_window for event in self._events
        )
        warning_count = sum(
            event >= timestamp - self._policy.warning_window for event in self._events
        )
        new_level = self._level
        if critical_count >= self._policy.critical_count:
            new_level = EscalationLevel.CRITICAL
        elif warning_count >= self._policy.warning_count:
            new_level = max(new_level, EscalationLevel.WARNING)
        changed = new_level if new_level > self._level else EscalationLevel.NONE
        self._level = new_level
        return changed
