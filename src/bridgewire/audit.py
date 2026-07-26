from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
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
    READER_CONNECTING = "reader_connecting"
    READER_CONNECTED = "reader_connected"
    READER_NOT_FOUND = "reader_not_found"
    READER_IDENTITY_AMBIGUOUS = "reader_identity_ambiguous"
    READER_OPEN_FAILED = "reader_open_failed"
    READER_READ_FAILED = "reader_read_failed"
    READER_DISCONNECTED = "reader_disconnected"
    READER_RECOVERED = "reader_recovered"
    READER_RECORD_RECEIVED = "reader_record_received"
    READER_RECORD_MALFORMED = "reader_record_malformed"
    RECONNECT_SCHEDULED = "reconnect_scheduled"
    RECONNECT_REPEATEDLY_FAILED = "reconnect_repeatedly_failed"


_FORBIDDEN_KEYS = ("credential", "card", "name", "secret", "token", "webhook", "password")
_ALLOWED_CORRELATION_KEYS = frozenset({"reason", "duration_seconds", "attempt", "event_count"})
_ALLOWED_REASONS = frozenset(
    {
        "invalid_length",
        "invalid_framing",
        "invalid_terminator",
        "invalid_encoding",
        "invalid_identifier",
        "invalid_checksum_encoding",
        "checksum_mismatch",
        "excessive_length",
        "malformed_record",
        "queue_unavailable",
        "setup_failed",
        "high_failed",
        "low_failed",
        "cleanup_failed",
    }
)


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
            if key not in _ALLOWED_CORRELATION_KEYS:
                raise ValueError(f"correlation key is not allow-listed: {key}")
        reason = self.correlation.get("reason")
        if reason is not None and reason not in _ALLOWED_REASONS:
            raise ValueError("correlation reason is not allow-listed")

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

    def latest_event_time(self) -> datetime | None:
        return self.events[-1].timestamp if self.events else None


class SQLiteAuditSink:
    """Transactional durable event repository with privacy-preserving payloads."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                correlation TEXT NOT NULL,
                reader_state TEXT,
                controller_state TEXT,
                delivery_status TEXT
            )
            """
        )
        self._connection.commit()

    def append(self, event: AuditEvent) -> None:
        payload = event.as_dict()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["timestamp"],
                    payload["event_type"],
                    payload["severity"],
                    json.dumps(payload["correlation"], sort_keys=True),
                    payload["reader_state"],
                    payload["controller_state"],
                    payload["delivery_status"],
                ),
            )

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
        assert row is not None
        return int(row[0])

    def latest_event_time(self) -> datetime | None:
        row = self._connection.execute(
            "SELECT timestamp FROM audit_events ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return datetime.fromisoformat(str(row[0])) if row is not None else None

    def close(self) -> None:
        self._connection.close()


class InMemoryNotificationQueue:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def enqueue(self, event: AuditEvent) -> None:
        self.events.append(event)

    def pending_count(self) -> int:
        return len(self.events)


class NotificationEndpoint(Protocol):
    def deliver(self, event: dict[str, Any]) -> None: ...


class NotificationWorker:
    """Delivers one durable item; failures leave it pending for a later retry."""

    def __init__(self, queue: DurableNotificationQueue, endpoint: NotificationEndpoint) -> None:
        self._queue = queue
        self._endpoint = endpoint

    def deliver_one(self) -> bool:
        pending = self._queue.pending()
        if not pending:
            return False
        event = pending[0]
        self._endpoint.deliver(event)
        self._queue.mark_delivered(str(event["event_id"]))
        return True


class DurableNotificationQueue:
    """Small append-only queue for the simulated increment."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    def enqueue(self, event: AuditEvent) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._path.exists():
                return []
            return [
                json.loads(line)
                for line in self._path.read_text(encoding="utf-8").splitlines()
                if line
            ]

    def pending_count(self) -> int:
        return len(self.pending())

    def mark_delivered(self, event_id: str) -> None:
        with self._lock:
            remaining = [
                event for event in self.pending() if str(event.get("event_id")) != event_id
            ]
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
