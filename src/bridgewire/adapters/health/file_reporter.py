from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class FileHealthReporter:
    def __init__(self, path: Path) -> None:
        self._path = path

    def report(self, status: str, **details: object) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"status": status, **details}
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
