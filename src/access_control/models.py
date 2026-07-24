from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AccessDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    timestamp: float
    fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class RelayActivation:
    timestamp: float
    requested_duration: float
