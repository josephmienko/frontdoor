from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.configuration import ConfigurationError, load_configuration


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
def test_approved_simulation_configuration() -> None:
    config = load_configuration(Path("configs/simulation.toml"))
    assert config.gpio.channel == 23
    assert config.gpio.release_seconds == 3
    assert config.serial.maximum_record_bytes == 16


@pytest.mark.unit
def test_configuration_cannot_change_approved_gpio_contract(tmp_path: Path) -> None:
    text = Path("configs/simulation.toml").read_text(encoding="utf-8")
    path = tmp_path / "bad.toml"
    path.write_text(text.replace("channel = 23", "channel = 13"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="GPIO"):
        load_configuration(path)
