from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class ManualClock:
    elapsed: float = 0.0
    wall_time: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def now(self) -> datetime:
        return self.wall_time

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot move backwards")
        self.elapsed += seconds
        self.wall_time += timedelta(seconds=seconds)
