from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridgewire.application.status_service import (
    OperationalSnapshotUnavailableError,
    StatusService,
    StatusSnapshot,
)
from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.controller import ControllerState
from bridgewire.reader import ReaderHealthState


class QueryUnavailableError(RuntimeError):
    pass


class InvalidQueryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventCursor:
    timestamp: datetime
    event_id: str

    def encode(self) -> str:
        payload = json.dumps(
            [self.timestamp.astimezone(UTC).isoformat(), self.event_id],
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> EventCursor:
        try:
            padding = "=" * (-len(value) % 4)
            raw = json.loads(base64.urlsafe_b64decode(value + padding))
            if not isinstance(raw, list) or len(raw) != 2:
                raise ValueError
            timestamp = datetime.fromisoformat(str(raw[0]))
            event_id = str(raw[1])
            if timestamp.tzinfo is None or timestamp.utcoffset() is None or not event_id:
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidQueryError("cursor is malformed") from exc
        return cls(timestamp.astimezone(UTC), event_id)


@dataclass(frozen=True, slots=True)
class EventPage:
    items: tuple[AuditEvent, ...]
    next_cursor: str | None


class AuditEventReader(Protocol):
    def list_events(
        self,
        *,
        limit: int,
        cursor: EventCursor | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> tuple[AuditEvent, ...]: ...

    def get_event(self, event_id: str) -> AuditEvent | None: ...


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    controller_state: ControllerState | None
    reader_health: ReaderHealthState | None
    operational_snapshot_stale: bool

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


class ReadOnlyQueryService:
    """Framework-neutral status, health, and audit query facade."""

    def __init__(
        self,
        *,
        status: StatusService,
        events: AuditEventReader,
        maximum_event_page_size: int = 100,
        operational_snapshot_stale_after_seconds: float = 10.0,
    ) -> None:
        if maximum_event_page_size <= 0:
            raise ValueError("maximum event page size must be positive")
        if operational_snapshot_stale_after_seconds <= 0:
            raise ValueError("snapshot freshness threshold must be positive")
        self._status = status
        self._events = events
        self.maximum_event_page_size = maximum_event_page_size
        self._stale_after = operational_snapshot_stale_after_seconds

    def health(self) -> HealthSnapshot:
        try:
            snapshot = self._status.snapshot()
        except OperationalSnapshotUnavailableError:
            return HealthSnapshot("unavailable", None, None, True)
        stale = snapshot.snapshot_age_seconds >= self._stale_after
        healthy = (
            not stale
            and snapshot.controller_state in {ControllerState.READY, ControllerState.RELEASED}
            and snapshot.reader_health is ReaderHealthState.READY
            and snapshot.authorization_loaded
        )
        return HealthSnapshot(
            status="healthy" if healthy else "degraded",
            controller_state=snapshot.controller_state,
            reader_health=snapshot.reader_health,
            operational_snapshot_stale=stale,
        )

    def status(self) -> StatusSnapshot:
        return self._status.snapshot()

    def list_events(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> EventPage:
        if limit <= 0 or limit > self.maximum_event_page_size:
            raise InvalidQueryError(f"limit must be between 1 and {self.maximum_event_page_size}")
        if cursor is not None and before is not None:
            raise InvalidQueryError("cursor and before cannot be combined")
        before = self._normalize_time(before, "before")
        after = self._normalize_time(after, "after")
        if before is not None and after is not None and after >= before:
            raise InvalidQueryError("after must be earlier than before")
        decoded = EventCursor.decode(cursor) if cursor is not None else None
        events = self._events.list_events(
            limit=limit + 1,
            cursor=decoded,
            before=before,
            after=after,
            event_type=event_type,
            severity=severity,
        )
        items = events[:limit]
        next_cursor = (
            EventCursor(items[-1].timestamp, items[-1].event_id).encode()
            if len(events) > limit and items
            else None
        )
        return EventPage(items, next_cursor)

    def get_event(self, event_id: str) -> AuditEvent | None:
        return self._events.get_event(event_id)

    @staticmethod
    def _normalize_time(value: datetime | None, field: str) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidQueryError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)
