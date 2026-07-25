from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire.audit import (
    AuditEvent,
    EventType,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
    Severity,
)
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.configuration import ConfigurationError, load_configuration
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationTracker
from bridgewire.gpio import SimulatedRelay
from bridgewire.reader import MalformedRecord, ParsedRecord, ReaderRecordError, parse_reader_record


@pytest.mark.unit
def test_audit_event_is_structured_timezone_aware_and_private() -> None:
    event = AuditEvent(EventType.CREDENTIAL_AUTHORIZED, Severity.INFO, datetime.now(UTC))
    encoded = json.dumps(event.as_dict())
    assert event.event_id
    assert "+00:00" in event.as_dict()["timestamp"]
    assert "0102030405" not in encoded


@pytest.mark.unit
@pytest.mark.parametrize("key", ["credential_id", "card", "person_name", "webhook_url"])
def test_sensitive_correlation_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        AuditEvent(
            EventType.CREDENTIAL_DENIED,
            Severity.WARNING,
            datetime.now(UTC),
            {key: "sanitized"},
        )


@pytest.mark.unit
def test_sensitive_data_cannot_hide_in_generic_correlation_value() -> None:
    with pytest.raises(ValueError, match="reason"):
        AuditEvent(
            EventType.CREDENTIAL_DENIED,
            Severity.WARNING,
            datetime.now(UTC),
            {"reason": "0102030405"},
        )


@pytest.mark.integration
def test_actual_access_and_parser_flows_do_not_expose_forbidden_values(
    schema: dict[str, object],
    tmp_path: Path,
    forbidden_values: tuple[str, ...],
) -> None:
    authorization_path = tmp_path / "authorization.csv"
    authorization_path.write_text(
        "KEY,NAME,ALLOW\nA1B2C3D4E5,Sanitized Cardholder,N\n",
        encoding="utf-8",
    )
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(authorization_path)
    clock = ManualClock()
    audit = InMemoryAuditSink()
    notifications = InMemoryNotificationQueue()
    controller = AccessController(
        authorization=store,
        relay=SimulatedRelay(clock),
        audit=audit,
        notifications=notifications,
        clock=clock,
        escalation=EscalationTracker(),
    )
    controller.start()
    controller.process(ParsedRecord("A1B2C3D4E5"))
    controller.process(MalformedRecord("A1B2C3D4E5"))
    with pytest.raises(ReaderRecordError) as error:
        parse_reader_record(b"\x02A1B2C3D4E500\r\n\x03")
    serialized = json.dumps([event.as_dict() for event in audit.events])
    serialized += str(error.value)
    serialized += json.dumps([event.as_dict() for event in notifications.events])
    assert all(value not in serialized for value in forbidden_values)


@pytest.mark.unit
def test_approved_simulation_configuration(repo_root: Path) -> None:
    config = load_configuration(repo_root / "configs" / "simulation.toml")
    assert config.gpio.channel == 23
    assert config.gpio.release_seconds == 3
    assert config.serial.maximum_record_bytes == 16


@pytest.mark.unit
def test_configuration_cannot_change_approved_gpio_contract(
    tmp_path: Path, repo_root: Path
) -> None:
    text = (repo_root / "configs" / "simulation.toml").read_text(encoding="utf-8")
    path = tmp_path / "bad.toml"
    path.write_text(text.replace("channel = 23", "channel = 13"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="GPIO"):
        load_configuration(path)


@pytest.mark.unit
def test_custom_escalation_policy_loads_from_configuration(tmp_path: Path, repo_root: Path) -> None:
    text = (repo_root / "configs" / "simulation.toml").read_text(encoding="utf-8")
    path = tmp_path / "custom.toml"
    path.write_text(
        text.replace("warning_count = 3", "warning_count = 2").replace(
            "critical_count = 5", "critical_count = 4"
        ),
        encoding="utf-8",
    )
    policy = load_configuration(path).escalation
    assert (policy.warning_count, policy.critical_count) == (2, 4)


@pytest.mark.unit
def test_invalid_escalation_policy_configuration_is_rejected(
    tmp_path: Path, repo_root: Path
) -> None:
    text = (repo_root / "configs" / "simulation.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid.toml"
    path.write_text(text.replace("warning_count = 3", "warning_count = 0"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="threshold"):
        load_configuration(path)
