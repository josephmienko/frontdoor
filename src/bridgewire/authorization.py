from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TextIO, cast

from jsonschema import Draft202012Validator


class AuthorizationError(ValueError):
    pass


class AuthorizationOutcome(StrEnum):
    AUTHORIZED = "authorized"
    DENIED = "denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AuthorizationRecord:
    key: str
    name: str
    allow: bool


def normalize_key(key: str) -> str:
    return key.replace("-", "").upper()


class AuthorizationFile:
    def __init__(self, schema: dict[str, object]) -> None:
        self._validator = Draft202012Validator(schema)

    def load(self, path: Path) -> dict[str, AuthorizationRecord]:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return self.load_stream(handle)
        except OSError as exc:
            raise AuthorizationError(str(exc)) from exc

    def load_stream(self, handle: TextIO) -> dict[str, AuthorizationRecord]:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["KEY", "NAME", "ALLOW"]:
            raise AuthorizationError("authorization header must be exactly KEY,NAME,ALLOW")
        raw_rows = [dict(row) for row in reader]
        errors = sorted(self._validator.iter_errors(raw_rows), key=lambda error: list(error.path))
        if errors:
            raise AuthorizationError(errors[0].message)
        records: dict[str, AuthorizationRecord] = {}
        for raw in raw_rows:
            row = cast(dict[str, str], raw)
            normalized = normalize_key(row["KEY"])
            if normalized in records:
                raise AuthorizationError("duplicate authorization key")
            records[normalized] = AuthorizationRecord(
                key=normalized,
                name=row["NAME"],
                allow=row["ALLOW"].lower() == "y",
            )
        return records


class AuthorizationStore:
    def __init__(self, parser: AuthorizationFile) -> None:
        self._parser = parser
        self._records: dict[str, AuthorizationRecord] = {}

    @property
    def record_count(self) -> int:
        return len(self._records)

    def reload(self, path: Path) -> None:
        candidate = self._parser.load(path)
        self._records = candidate

    def classify(self, credential: str) -> str:
        record = self._records.get(normalize_key(credential))
        if record is None:
            return AuthorizationOutcome.UNKNOWN.value
        if record.allow:
            return AuthorizationOutcome.AUTHORIZED.value
        return AuthorizationOutcome.DENIED.value


def install_authorization_candidate(
    candidate: Path,
    destination: Path,
    parser: AuthorizationFile,
) -> None:
    parser.load(candidate)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with (
            candidate.open("rb") as source,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as temporary,
        ):
            temporary_path = Path(temporary.name)
            while chunk := source.read(64 * 1024):
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        parser.load(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
