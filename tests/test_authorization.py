from __future__ import annotations

import os
from pathlib import Path

import pytest

from bridgewire.authorization import (
    AuthorizationError,
    AuthorizationFile,
    AuthorizationOutcome,
    AuthorizationStore,
    install_authorization_candidate,
)


@pytest.mark.unit
def test_existing_csv_shape_and_outcomes(schema: dict[str, object]) -> None:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(Path("tests/fixtures/authorization/valid.csv"))
    assert store.classify("0102030405") == AuthorizationOutcome.AUTHORIZED
    assert store.classify("11-12-13-14-15") == AuthorizationOutcome.DENIED
    assert store.classify("FFFFFFFFFF") == AuthorizationOutcome.UNKNOWN


@pytest.mark.unit
@pytest.mark.parametrize("fixture", ["malformed.csv", "duplicate.csv"])
def test_invalid_authorization_files_are_rejected(schema: dict[str, object], fixture: str) -> None:
    parser = AuthorizationFile(schema)
    with pytest.raises(AuthorizationError):
        parser.load(Path("tests/fixtures/authorization") / fixture)


@pytest.mark.unit
def test_failed_reload_retains_last_valid_set(schema: dict[str, object]) -> None:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(Path("tests/fixtures/authorization/valid.csv"))
    with pytest.raises(AuthorizationError):
        store.reload(Path("tests/fixtures/authorization/malformed.csv"))
    assert store.record_count == 2
    assert store.classify("0102030405") == AuthorizationOutcome.AUTHORIZED


@pytest.mark.unit
def test_atomic_install_rejects_invalid_candidate_without_replacement(
    schema: dict[str, object], tmp_path: Path
) -> None:
    destination = tmp_path / "authorization.csv"
    original = Path("tests/fixtures/authorization/valid.csv").read_bytes()
    destination.write_bytes(original)
    with pytest.raises(AuthorizationError):
        install_authorization_candidate(
            Path("tests/fixtures/authorization/malformed.csv"),
            destination,
            AuthorizationFile(schema),
        )
    assert destination.read_bytes() == original


@pytest.mark.unit
def test_atomic_install_never_exposes_partial_destination(
    schema: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "authorization.csv"
    original = Path("tests/fixtures/authorization/valid.csv").read_bytes()
    destination.write_bytes(original)
    real_replace = os.replace
    observed_before_replace: list[bytes] = []

    def observing_replace(source: str | Path, target: str | Path) -> None:
        observed_before_replace.append(destination.read_bytes())
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", observing_replace)
    install_authorization_candidate(
        Path("tests/fixtures/authorization/replacement.csv"),
        destination,
        AuthorizationFile(schema),
    )
    assert observed_before_replace == [original]
    assert (
        destination.read_bytes()
        == Path("tests/fixtures/authorization/replacement.csv").read_bytes()
    )
