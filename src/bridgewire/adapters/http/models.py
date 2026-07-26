from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from bridgewire.audit import EventType, Severity
from bridgewire.controller import ControllerState, RelayCommand
from bridgewire.reader import ReaderHealthState


class HttpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(HttpModel):
    status: str
    controller_state: ControllerState | None
    reader_health: ReaderHealthState | None
    operational_snapshot_stale: bool


class StatusResponse(HttpModel):
    controller_state: ControllerState
    reader_connected: bool
    reader_health: ReaderHealthState
    snapshot_published_at: datetime
    snapshot_age_seconds: float
    last_credential_processed_at: datetime | None
    last_reader_record_age_seconds: float | None
    release_active: bool
    release_deadline_at: datetime | None
    release_remaining_seconds: float | None
    configured_release_seconds: float
    last_relay_command: RelayCommand | None
    authorization_loaded: bool
    authorization_record_count: int
    authorization_source_revision: str | None
    authorization_source_modified_at: datetime | None
    last_audit_event_at: datetime | None
    pending_notification_count: int
    application_started_at: datetime
    software_version: str


class EventResponse(HttpModel):
    event_id: str
    timestamp: datetime
    event_type: EventType
    severity: Severity
    correlation: dict[str, str | int | float | bool | None]
    reader_state: str | None
    controller_state: str | None
    delivery_status: str | None


class EventPageResponse(HttpModel):
    items: list[EventResponse]
    next_cursor: str | None
