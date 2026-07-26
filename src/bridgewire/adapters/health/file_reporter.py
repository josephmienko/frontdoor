from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class FileHealthReporter:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = path
        self._now = now

    def report(self, status: str, **details: object) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        reported_at = self._now()
        if reported_at.tzinfo is None or reported_at.utcoffset() is None:
            raise ValueError("health timestamp must be timezone-aware")
        payload = {
            "status": status,
            "reported_at": reported_at.isoformat(),
            **details,
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=".health-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
