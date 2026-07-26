from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from bridgewire.authorization import AuthorizationSnapshot
from bridgewire.controller import ControllerSnapshot, ControllerState
from bridgewire.interfaces import Clock
from bridgewire.reader import ReaderHealthState, ReaderSnapshot


class ControllerSnapshotSource(Protocol):
    def snapshot(self) -> ControllerSnapshot: ...


class ReaderSnapshotSource(Protocol):
    def snapshot(self) -> ReaderSnapshot: ...


class AuthorizationSnapshotSource(Protocol):
    def snapshot(self) -> AuthorizationSnapshot: ...


class AuditStatusSource(Protocol):
    def latest_event_time(self) -> datetime | None: ...


class NotificationStatusSource(Protocol):
    def pending_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    controller_state: ControllerState
    reader_connected: bool
    reader_health: ReaderHealthState
    last_successful_credential_processing_at: datetime | None
    last_reader_record_age_seconds: float | None
    release_active: bool
    release_deadline: float | None
    release_remaining_seconds: float | None
    configured_release_seconds: float
    last_relay_command_high: bool | None
    authorization_loaded: bool
    authorization_record_count: int
    authorization_version: str | None
    authorization_modified_at: datetime | None
    last_audit_event_at: datetime | None
    pending_notification_count: int
    application_started_at: datetime
    software_version: str

    def as_dict(self) -> dict[str, object]:
        return {
            key: (
                value.value
                if isinstance(value, StrEnum)
                else value.isoformat()
                if isinstance(value, datetime)
                else value
            )
            for key, value in asdict(self).items()
        }


class StatusService:
    """Framework-independent read model assembled only from public snapshots."""

    def __init__(
        self,
        *,
        controller: ControllerSnapshotSource,
        reader: ReaderSnapshotSource,
        authorization: AuthorizationSnapshotSource,
        audit: AuditStatusSource,
        notifications: NotificationStatusSource,
        clock: Clock,
        software_version: str,
    ) -> None:
        self._controller = controller
        self._reader = reader
        self._authorization = authorization
        self._audit = audit
        self._notifications = notifications
        self._started_at = clock.now()
        self._software_version = software_version

    def snapshot(self) -> StatusSnapshot:
        controller = self._controller.snapshot()
        reader = self._reader.snapshot()
        authorization = self._authorization.snapshot()
        return StatusSnapshot(
            controller_state=controller.state,
            reader_connected=reader.connected,
            reader_health=reader.health_state,
            last_successful_credential_processing_at=controller.last_credential_processed_at,
            last_reader_record_age_seconds=reader.last_record_age_seconds,
            release_active=controller.release_active,
            release_deadline=controller.release_deadline,
            release_remaining_seconds=controller.release_remaining_seconds,
            configured_release_seconds=controller.configured_release_seconds,
            last_relay_command_high=controller.last_relay_command_high,
            authorization_loaded=authorization.loaded,
            authorization_record_count=authorization.record_count,
            authorization_version=authorization.version,
            authorization_modified_at=authorization.modified_at,
            last_audit_event_at=self._audit.latest_event_time(),
            pending_notification_count=self._notifications.pending_count(),
            application_started_at=self._started_at,
            software_version=self._software_version,
        )
