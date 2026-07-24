from __future__ import annotations

from pathlib import Path

import pytest

from bridgewire.simulation import run_vertical_slice


@pytest.mark.integration
def test_simulated_vertical_slice_contains_required_lifecycle_without_secrets() -> None:
    output = run_vertical_slice(
        Path("schemas/authorization-file/schema.json"),
        Path("tests/fixtures/authorization/valid.csv"),
    )
    types = [str(event["event_type"]) for event in output]
    for required in (
        "service_started",
        "credential_authorized",
        "relay_asserted",
        "credential_denied",
        "credential_unknown",
        "malformed_record",
        "escalation_warning",
        "escalation_critical",
        "relay_restored",
        "reader_connected",
        "reader_disconnected",
        "reader_recovered",
        "service_shutdown",
    ):
        assert required in types
    serialized = str(output)
    assert "0102030405" not in serialized
    assert "Authorized Fixture" not in serialized
