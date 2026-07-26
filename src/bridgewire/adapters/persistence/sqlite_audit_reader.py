from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridgewire.audit import AuditEvent, EventType, Severity


class SQLiteAuditReader:
    """Audit queries using an independent read-only connection per operation."""

    def __init__(self, path: Path) -> None:
        self._database_uri = f"{path.resolve().as_uri()}?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_uri, uri=True)

    def list_events(
        self,
        *,
        limit: int,
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
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_id, timestamp, event_type, severity, correlation, "
                "reader_state, controller_state, delivery_status "
                f"FROM audit_events{where} ORDER BY timestamp DESC, event_id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(self._event(row) for row in rows)

    def get_event(self, event_id: str) -> AuditEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT event_id, timestamp, event_type, severity, correlation, "
                "reader_state, controller_state, delivery_status "
                "FROM audit_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._event(row) if row is not None else None

    def latest_event_time(self) -> datetime | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT timestamp FROM audit_events ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
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
