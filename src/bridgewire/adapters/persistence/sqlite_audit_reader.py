from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridgewire.application.query_service import EventCursor, QueryUnavailableError
from bridgewire.audit import AuditEvent, EventType, Severity

SQLITE_READ_TIMEOUT_SECONDS = 1.0
logger = logging.getLogger(__name__)


class SQLiteAuditReader:
    """Audit queries using an independent read-only connection per operation."""

    def __init__(self, path: Path, *, timeout_seconds: float = SQLITE_READ_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("SQLite read timeout must be positive")
        self._database_uri = f"{path.resolve().as_uri()}?mode=ro"
        self._timeout_seconds = timeout_seconds

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_uri, uri=True, timeout=self._timeout_seconds)

    def list_events(
        self,
        *,
        limit: int,
        cursor: EventCursor | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> tuple[AuditEvent, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, operator, value in (
            ("timestamp", "<", before.isoformat() if before else None),
            ("timestamp", ">", after.isoformat() if after else None),
            ("event_type", "=", event_type.value if event_type else None),
            ("severity", "=", severity.value if severity else None),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                parameters.append(value)
        if cursor is not None:
            clauses.append("(timestamp < ? OR (timestamp = ? AND event_id < ?))")
            cursor_timestamp = cursor.timestamp.isoformat()
            parameters.extend([cursor_timestamp, cursor_timestamp, cursor.event_id])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT event_id, timestamp, event_type, severity, correlation, "
                    "reader_state, controller_state, delivery_status "
                    f"FROM audit_events{where} "
                    "ORDER BY timestamp DESC, event_id DESC LIMIT ?",
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("audit list query unavailable", exc_info=True)
            raise QueryUnavailableError("audit query unavailable") from exc
        try:
            return tuple(self._event(row) for row in rows)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("audit query returned unreadable data", exc_info=True)
            raise QueryUnavailableError("audit query unavailable") from exc

    def get_event(self, event_id: str) -> AuditEvent | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT event_id, timestamp, event_type, severity, correlation, "
                    "reader_state, controller_state, delivery_status "
                    "FROM audit_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("audit detail query unavailable", exc_info=True)
            raise QueryUnavailableError("audit query unavailable") from exc
        try:
            return self._event(row) if row is not None else None
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("audit query returned unreadable data", exc_info=True)
            raise QueryUnavailableError("audit query unavailable") from exc

    def latest_event_time(self) -> datetime | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT timestamp FROM audit_events ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("audit latest-event query unavailable", exc_info=True)
            raise QueryUnavailableError("audit query unavailable") from exc
        return datetime.fromisoformat(str(row[0])) if row is not None else None

    @staticmethod
    def _event(row: tuple[Any, ...]) -> AuditEvent:
        return AuditEvent(
            event_id=str(row[0]),
            timestamp=datetime.fromisoformat(str(row[1])),
            event_type=EventType(str(row[2])),
            severity=Severity(str(row[3])),
            correlation=MappingProxyType(json.loads(str(row[4]))),
            reader_state=str(row[5]) if row[5] is not None else None,
            controller_state=str(row[6]) if row[6] is not None else None,
            delivery_status=str(row[7]) if row[7] is not None else None,
        )
