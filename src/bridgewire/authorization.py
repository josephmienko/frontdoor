from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class AuthorizationInstallFailure:
    stage: str


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    loaded: bool
    record_count: int
    version: str | None
    modified_at: datetime | None


def normalize_key(key: str) -> str:
    return key.replace("-", "").upper()


class AuthorizationFile:
    def __init__(self, schema: dict[str, object]) -> None:
        self._validator = Draft202012Validator(schema)

    def load(self, path: Path) -> dict[str, AuthorizationRecord]:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return self.load_stream(handle)
        except UnicodeError as exc:
            raise AuthorizationError("authorization file has invalid UTF-8 encoding") from exc
        except OSError as exc:
            raise AuthorizationError(str(exc)) from exc

    def load_stream(self, handle: TextIO) -> dict[str, AuthorizationRecord]:
        try:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != ["KEY", "NAME", "ALLOW"]:
                raise AuthorizationError("authorization header must be exactly KEY,NAME,ALLOW")
            raw_rows = [dict(row) for row in reader]
        except csv.Error as exc:
            raise AuthorizationError("authorization CSV is malformed") from exc
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
        self._version: str | None = None
        self._modified_at: datetime | None = None

    @property
    def record_count(self) -> int:
        return len(self._records)

    def reload(self, path: Path) -> None:
        candidate = self._parser.load(path)
        metadata = path.stat()
        self._records = candidate
        self._version = f"{metadata.st_mtime_ns}:{metadata.st_size}"
        self._modified_at = datetime.fromtimestamp(metadata.st_mtime, UTC)

    def snapshot(self) -> AuthorizationSnapshot:
        return AuthorizationSnapshot(
            loaded=self._version is not None,
            record_count=len(self._records),
            version=self._version,
            modified_at=self._modified_at,
        )

    def classify(self, credential: str) -> AuthorizationOutcome:
        record = self._records.get(normalize_key(credential))
        if record is None:
            return AuthorizationOutcome.UNKNOWN
        if record.allow:
            return AuthorizationOutcome.AUTHORIZED
        return AuthorizationOutcome.DENIED


def install_authorization_candidate(
    candidate: Path,
    destination: Path,
    parser: AuthorizationFile,
    *,
    on_failure: Callable[[AuthorizationInstallFailure], None] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> None:
    temporary_path: Path | None = None
    stage = "candidate_validation"
    try:
        if failure_injector is not None:
            failure_injector(stage)
        parser.load(candidate)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for abandoned in destination.parent.glob(f".{destination.name}.*"):
            try:
                abandoned.unlink()
            except OSError:
                if on_failure is not None:
                    on_failure(AuthorizationInstallFailure("abandoned_cleanup"))
        stage = "temporary_creation"
        if failure_injector is not None:
            failure_injector(stage)
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
            stage = "candidate_copy"
            if failure_injector is not None:
                failure_injector(stage)
            while chunk := source.read(64 * 1024):
                temporary.write(chunk)
            stage = "temporary_flush"
            if failure_injector is not None:
                failure_injector(stage)
            temporary.flush()
            stage = "temporary_fsync"
            if failure_injector is not None:
                failure_injector(stage)
            os.fsync(temporary.fileno())
        stage = "temporary_validation"
        if failure_injector is not None:
            failure_injector(stage)
        parser.load(temporary_path)
        stage = "atomic_replace"
        if failure_injector is not None:
            failure_injector(stage)
        os.replace(temporary_path, destination)
        temporary_path = None
    except Exception:
        if on_failure is not None:
            on_failure(AuthorizationInstallFailure(stage))
        raise
    finally:
        if temporary_path is not None:
            try:
                if failure_injector is not None:
                    failure_injector("temporary_cleanup")
                temporary_path.unlink(missing_ok=True)
            except OSError:
                if on_failure is not None:
                    on_failure(AuthorizationInstallFailure("temporary_cleanup"))
