from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from bridgewire.application.status_service import StatusService, StatusSnapshot
from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.controller import ControllerState
from bridgewire.reader import ReaderHealthState


class AuditEventReader(Protocol):
    def list_events(
        self,
        *,
        limit: int,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> tuple[AuditEvent, ...]: ...

    def get_event(self, event_id: str) -> AuditEvent | None: ...


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    controller_state: ControllerState
    reader_health: ReaderHealthState

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
    ) -> None:
        if maximum_event_page_size <= 0:
            raise ValueError("maximum event page size must be positive")
        self._status = status
        self._events = events
        self.maximum_event_page_size = maximum_event_page_size

    def health(self) -> HealthSnapshot:
        snapshot = self._status.snapshot()
        healthy = (
            snapshot.controller_state in {ControllerState.READY, ControllerState.RELEASED}
            and snapshot.reader_health is ReaderHealthState.READY
            and snapshot.authorization_loaded
        )
        return HealthSnapshot(
            status="healthy" if healthy else "degraded",
            controller_state=snapshot.controller_state,
            reader_health=snapshot.reader_health,
        )

    def status(self) -> StatusSnapshot:
        return self._status.snapshot()

    def list_events(
        self,
        *,
        limit: int,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> tuple[AuditEvent, ...]:
        if limit <= 0 or limit > self.maximum_event_page_size:
            raise ValueError(f"limit must be between 1 and {self.maximum_event_page_size}")
        return self._events.list_events(
            limit=limit,
            before=before,
            after=after,
            event_type=event_type,
            severity=severity,
        )

    def get_event(self, event_id: str) -> AuditEvent | None:
        return self._events.get_event(event_id)
