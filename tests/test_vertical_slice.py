from __future__ import annotations

from pathlib import Path

import pytest

from bridgewire.audit import EventType
from bridgewire.gpio import RelayActionType
from bridgewire.reader import ReaderEventType
from bridgewire.simulation import run_vertical_slice_result


@pytest.mark.integration
def test_simulated_vertical_slice_contains_required_lifecycle_without_secrets(
    schema_root: Path, authorization_fixture_root: Path
) -> None:
    result = run_vertical_slice_result(
        schema_root / "authorization-file" / "schema.json",
        authorization_fixture_root / "valid.csv",
    )
    assert [event.event_type for event in result.audit_events] == [
        EventType.SERVICE_STARTED,
        EventType.CREDENTIAL_AUTHORIZED,
        EventType.RELAY_ASSERTED,
        EventType.CREDENTIAL_AUTHORIZED,
        EventType.CREDENTIAL_DENIED,
        EventType.CREDENTIAL_UNKNOWN,
        EventType.MALFORMED_RECORD,
        EventType.ESCALATION_WARNING,
        EventType.RELAY_RESTORED,
        EventType.CREDENTIAL_UNKNOWN,
        EventType.MALFORMED_RECORD,
        EventType.ESCALATION_CRITICAL,
        EventType.SERVICE_SHUTDOWN,
    ]
    assert [event.timestamp.timestamp() for event in result.audit_events] == [
        1767225600.0,
        1767225600.0,
        1767225600.0,
        1767225601.0,
        1767225601.0,
        1767225601.0,
        1767225601.0,
        1767225601.0,
        1767225603.0,
        1767225603.0,
        1767225603.0,
        1767225603.0,
        1767225603.0,
    ]
    assert [action.action for action in result.relay_actions] == [
        RelayActionType.SETUP,
        RelayActionType.LOW,
        RelayActionType.HIGH,
        RelayActionType.LOW,
        RelayActionType.LOW,
        RelayActionType.CLEANUP,
    ]
    assert [event.event_type for event in result.reader_events] == [
        ReaderEventType.READER_CONNECTING,
        ReaderEventType.READER_CONNECTED,
        ReaderEventType.READER_DISCONNECTED,
        ReaderEventType.READER_CONNECTING,
        ReaderEventType.READER_RECOVERED,
    ]
    assert result.notification_count == 1
    assert result.reader_state_transitions == [
        "connecting",
        "ready",
        "degraded",
        "connecting",
        "ready",
    ]
    assert result.final_controller_state == "stopped"
    assert not result.final_relay_high
    assert [action.action for action in result.relay_actions].count(RelayActionType.HIGH) == 1
    serialized = str(result.events)
    assert "0102030405" not in serialized
    assert "Authorized Fixture" not in serialized
