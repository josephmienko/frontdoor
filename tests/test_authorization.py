from __future__ import annotations

import os
import tempfile
from io import StringIO
from pathlib import Path

import pytest

from bridgewire.authorization import (
    AuthorizationError,
    AuthorizationFile,
    AuthorizationInstallFailure,
    AuthorizationOutcome,
    AuthorizationRecord,
    AuthorizationStore,
    install_authorization_candidate,
)


@pytest.mark.unit
def test_existing_csv_shape_and_outcomes(
    schema: dict[str, object], authorization_fixture_root: Path
) -> None:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(authorization_fixture_root / "valid.csv")
    assert store.classify("0102030405") == AuthorizationOutcome.AUTHORIZED
    assert store.classify("11-12-13-14-15") == AuthorizationOutcome.DENIED
    assert store.classify("FFFFFFFFFF") == AuthorizationOutcome.UNKNOWN


@pytest.mark.unit
@pytest.mark.parametrize("fixture", ["malformed.csv", "duplicate.csv"])
def test_invalid_authorization_files_are_rejected(
    schema: dict[str, object], authorization_fixture_root: Path, fixture: str
) -> None:
    parser = AuthorizationFile(schema)
    with pytest.raises(AuthorizationError):
        parser.load(authorization_fixture_root / fixture)


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "",
        "KEY,NAME,ALLOW\n",
        "0102030405,Fixture,Y\n",
        "NAME,KEY,ALLOW\nFixture,0102030405,Y\n",
        "KEY,NAME\n0102030405,Fixture\n",
        "KEY,NAME,ALLOW,EXTRA\n0102030405,Fixture,Y,no\n",
        "KEY,NAME,ALLOW\n,Fixture,Y\n",
        "KEY,NAME,ALLOW\n0102030405,,Y\n",
        "KEY,NAME,ALLOW\n0102030405,   ,Y\n",
        "KEY,NAME,ALLOW\n0102030405,Fixture,\n",
        "KEY,NAME,ALLOW\n0102030405,Fixture,yes\n",
        "KEY,NAME,ALLOW\n01-0203-04-05,Fixture,Y\n",
        "KEY,NAME,ALLOW\n0102-03-0405,Fixture,Y\n",
        "KEY,NAME,ALLOW\n01-02-03-04-05-,Fixture,Y\n",
        "KEY,NAME,ALLOW\n-01-02-03-04-05,Fixture,Y\n",
        "KEY,NAME,ALLOW\n0102030405,One,Y\n01-02-03-04-05,Two,N\n",
    ],
)
def test_authorization_schema_rejects_edge_cases(schema: dict[str, object], text: str) -> None:
    with pytest.raises(AuthorizationError):
        AuthorizationFile(schema).load_stream(StringIO(text))


@pytest.mark.unit
@pytest.mark.parametrize("key", ["0102030405", "01-02-03-04-05", "a1a2a3a4a5"])
@pytest.mark.parametrize("allow", ["Y", "N", "y", "n"])
def test_valid_key_and_allow_forms_normalize(
    schema: dict[str, object], key: str, allow: str
) -> None:
    records = AuthorizationFile(schema).load_stream(
        StringIO(f"KEY,NAME,ALLOW\n{key},Fixture,{allow}\n")
    )
    assert list(records) == [key.replace("-", "").upper()]


@pytest.mark.unit
def test_mixed_case_key_and_case_variant_duplicate_are_normalized(
    schema: dict[str, object],
) -> None:
    parser = AuthorizationFile(schema)
    assert "A1B2C3D4E5" in parser.load_stream(StringIO("KEY,NAME,ALLOW\nA1b2C3d4E5,Fixture,Y\n"))
    with pytest.raises(AuthorizationError, match="duplicate"):
        parser.load_stream(StringIO("KEY,NAME,ALLOW\nA1B2C3D4E5,One,Y\na1-b2-c3-d4-e5,Two,N\n"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "fixture", ["invalid_header_only.csv", "invalid_malformed_quoting.csv", "invalid_bom.csv"]
)
def test_filesystem_specific_invalid_fixtures(
    schema: dict[str, object], authorization_fixture_root: Path, fixture: str
) -> None:
    with pytest.raises(AuthorizationError):
        AuthorizationFile(schema).load(authorization_fixture_root / fixture)


@pytest.mark.unit
def test_invalid_utf8_is_reported_as_sanitized_authorization_error(
    schema: dict[str, object], tmp_path: Path
) -> None:
    path = tmp_path / "invalid-encoding.csv"
    path.write_bytes(b"KEY,NAME,ALLOW\n0102030405,Fixture \xff,Y\n")
    with pytest.raises(AuthorizationError, match="encoding") as error:
        AuthorizationFile(schema).load(path)
    assert "\\xff" not in str(error.value)
    assert "Fixture" not in str(error.value)


@pytest.mark.unit
def test_valid_multiple_and_realistic_larger_files(
    schema: dict[str, object],
    authorization_fixture_root: Path,
) -> None:
    parser = AuthorizationFile(schema)
    assert len(parser.load(authorization_fixture_root / "valid_multiple_records.csv")) == 3
    rows = ["KEY,NAME,ALLOW"]
    for index in range(128):
        rows.append(f"{index:010X},Fixture {index},{'Y' if index % 2 == 0 else 'N'}")
    assert len(parser.load_stream(StringIO("\n".join(rows)))) == 128


@pytest.mark.unit
def test_failed_reload_retains_last_valid_set(
    schema: dict[str, object], authorization_fixture_root: Path
) -> None:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(authorization_fixture_root / "valid.csv")
    with pytest.raises(AuthorizationError):
        store.reload(authorization_fixture_root / "malformed.csv")
    assert store.record_count == 2
    assert store.classify("0102030405") == AuthorizationOutcome.AUTHORIZED


@pytest.mark.unit
def test_atomic_install_rejects_invalid_candidate_without_replacement(
    schema: dict[str, object], tmp_path: Path, authorization_fixture_root: Path
) -> None:
    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    with pytest.raises(AuthorizationError):
        install_authorization_candidate(
            authorization_fixture_root / "malformed.csv",
            destination,
            AuthorizationFile(schema),
        )
    assert destination.read_bytes() == original


@pytest.mark.unit
def test_atomic_install_never_exposes_partial_destination(
    schema: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization_fixture_root: Path,
) -> None:
    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    real_replace = os.replace
    observed_before_replace: list[bytes] = []

    def observing_replace(source: str | Path, target: str | Path) -> None:
        observed_before_replace.append(destination.read_bytes())
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", observing_replace)
    install_authorization_candidate(
        authorization_fixture_root / "replacement.csv",
        destination,
        AuthorizationFile(schema),
    )
    assert observed_before_replace == [original]
    assert destination.read_bytes() == (authorization_fixture_root / "replacement.csv").read_bytes()


@pytest.mark.failure_mode
@pytest.mark.parametrize("failure_point", ["temporary_creation", "fsync", "replace"])
def test_atomic_install_injected_failures_preserve_working_file(
    schema: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization_fixture_root: Path,
    failure_point: str,
) -> None:
    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(destination)
    failures: list[AuthorizationInstallFailure] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(f"injected {failure_point}")

    if failure_point == "temporary_creation":
        monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail)
    elif failure_point == "fsync":
        monkeypatch.setattr(os, "fsync", fail)
    else:
        monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        install_authorization_candidate(
            authorization_fixture_root / "replacement.csv",
            destination,
            AuthorizationFile(schema),
            on_failure=failures.append,
        )
    assert destination.read_bytes() == original
    assert store.classify("0102030405") is AuthorizationOutcome.AUTHORIZED
    assert not list(tmp_path.glob(".authorization.csv.*"))
    assert failures


@pytest.mark.failure_mode
def test_temporary_validation_failure_and_abandoned_file_cleanup(
    schema: dict[str, object],
    tmp_path: Path,
    authorization_fixture_root: Path,
) -> None:
    class RejectSecondLoad(AuthorizationFile):
        def __init__(self) -> None:
            super().__init__(schema)
            self.loads = 0

        def load(self, path: Path) -> dict[str, AuthorizationRecord]:
            self.loads += 1
            if self.loads == 2:
                raise AuthorizationError("injected temporary validation")
            return super().load(path)

    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    abandoned = tmp_path / ".authorization.csv.abandoned"
    abandoned.write_text("partial", encoding="utf-8")
    failures: list[AuthorizationInstallFailure] = []
    with pytest.raises(AuthorizationError, match="temporary validation"):
        install_authorization_candidate(
            authorization_fixture_root / "replacement.csv",
            destination,
            RejectSecondLoad(),
            on_failure=failures.append,
        )
    assert destination.read_bytes() == original
    assert not abandoned.exists()
    assert failures[-1].stage == "temporary_validation"


@pytest.mark.failure_mode
@pytest.mark.parametrize(
    "stage",
    [
        "candidate_validation",
        "temporary_creation",
        "candidate_copy",
        "temporary_flush",
        "temporary_fsync",
        "temporary_validation",
        "atomic_replace",
    ],
)
def test_every_pre_replace_failure_stage_preserves_original(
    schema: dict[str, object],
    tmp_path: Path,
    authorization_fixture_root: Path,
    stage: str,
) -> None:
    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    failures: list[AuthorizationInstallFailure] = []

    def inject(current: str) -> None:
        if current == stage:
            raise PermissionError(f"injected {stage}")

    with pytest.raises(PermissionError, match="injected"):
        install_authorization_candidate(
            authorization_fixture_root / "replacement.csv",
            destination,
            AuthorizationFile(schema),
            on_failure=failures.append,
            failure_injector=inject,
        )
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".authorization.csv.*"))
    assert failures[-1].stage == stage


@pytest.mark.failure_mode
def test_cleanup_failure_is_reported_and_next_run_removes_abandoned_temp(
    schema: dict[str, object],
    tmp_path: Path,
    authorization_fixture_root: Path,
) -> None:
    destination = tmp_path / "authorization.csv"
    original = (authorization_fixture_root / "valid.csv").read_bytes()
    destination.write_bytes(original)
    failures: list[AuthorizationInstallFailure] = []

    def inject(current: str) -> None:
        if current in {"candidate_copy", "temporary_cleanup"}:
            raise OSError(f"injected {current}")

    with pytest.raises(OSError, match="candidate_copy"):
        install_authorization_candidate(
            authorization_fixture_root / "replacement.csv",
            destination,
            AuthorizationFile(schema),
            on_failure=failures.append,
            failure_injector=inject,
        )
    assert destination.read_bytes() == original
    assert [failure.stage for failure in failures] == [
        "candidate_copy",
        "temporary_cleanup",
    ]
    assert list(tmp_path.glob(".authorization.csv.*"))
    install_authorization_candidate(
        authorization_fixture_root / "replacement.csv",
        destination,
        AuthorizationFile(schema),
    )
    assert not list(tmp_path.glob(".authorization.csv.*"))
