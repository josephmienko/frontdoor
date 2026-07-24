from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from bridgewire.interfaces import Correlation


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    ERROR = "error"


class EventType(StrEnum):
    SERVICE_STARTED = "service_started"
    SERVICE_SHUTDOWN = "service_shutdown"
    CONFIGURATION_ERROR = "configuration_error"
    CREDENTIAL_AUTHORIZED = "credential_authorized"
    CREDENTIAL_DENIED = "credential_denied"
    CREDENTIAL_UNKNOWN = "credential_unknown"
    MALFORMED_RECORD = "malformed_record"
    ESCALATION_WARNING = "escalation_warning"
    ESCALATION_CRITICAL = "escalation_critical"
    RELAY_ASSERTED = "relay_asserted"
    RELAY_RESTORED = "relay_restored"
    RELAY_CONTROL_ERROR = "relay_control_error"
    AUTHORIZATION_RELOADED = "authorization_reloaded"
    AUTHORIZATION_RELOAD_FAILED = "authorization_reload_failed"
    NOTIFICATION_DELIVERY_FAILED = "notification_delivery_failed"
    NOTIFICATION_BACKLOG_RECOVERED = "notification_backlog_recovered"


_FORBIDDEN_KEYS = ("credential", "card", "name", "secret", "token", "webhook", "password")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: EventType
    severity: Severity
    timestamp: datetime
    correlation: Correlation = field(default_factory=lambda: MappingProxyType({}))
    reader_state: str | None = None
    controller_state: str | None = None
    delivery_status: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("audit timestamps must be timezone-aware")
        for key in self.correlation:
            lowered = key.lower()
            if any(forbidden in lowered for forbidden in _FORBIDDEN_KEYS):
                raise ValueError(f"sensitive correlation key is prohibited: {key}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "correlation": dict(self.correlation),
            "reader_state": self.reader_state,
            "controller_state": self.controller_state,
            "delivery_status": self.delivery_status,
        }


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class InMemoryNotificationQueue:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.available = True

    def enqueue(self, event: AuditEvent) -> None:
        self.events.append(event)


class DurableNotificationQueue:
    """Small append-only queue for the simulated increment."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def enqueue(self, event: AuditEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def pending(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [
            json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line
        ]

    def mark_delivered(self, event_id: str) -> None:
        remaining = [event for event in self.pending() if str(event.get("event_id")) != event_id]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                for event in remaining:
                    temporary.write(json.dumps(event, sort_keys=True) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
