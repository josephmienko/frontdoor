from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from bridgewire.escalation import EscalationPolicy
from bridgewire.reader import BackoffPolicy, ReaderIdentity


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SerialConfiguration:
    baud_rate: int
    data_bits: int
    parity: str
    stop_bits: int
    timeout_seconds: float
    maximum_record_bytes: int


@dataclass(frozen=True, slots=True)
class GpioConfiguration:
    numbering: str
    channel: int
    normal_high: bool
    release_high: bool
    release_seconds: float


@dataclass(frozen=True, slots=True)
class SystemConfiguration:
    reader_identity: ReaderIdentity
    serial: SerialConfiguration
    gpio: GpioConfiguration
    backoff: BackoffPolicy
    escalation: EscalationPolicy


def _table(parent: dict[str, object], name: str) -> dict[str, object]:
    value = parent.get(name)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a table")
    return cast(dict[str, object], value)


def _integer(table: dict[str, object], name: str) -> int:
    value = table.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} has invalid type")
    return value


def _number(table: dict[str, object], name: str) -> float:
    value = table.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} has invalid type")
    return float(value)


def _string(table: dict[str, object], name: str) -> str:
    value = table.get(name)
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} has invalid type")
    return value


def _boolean(table: dict[str, object], name: str) -> bool:
    value = table.get(name)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} has invalid type")
    return value


def _optional_integer(table: dict[str, object], name: str) -> int | None:
    value = table.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} has invalid type")
    return value


def _optional_string(table: dict[str, object], name: str) -> str | None:
    value = table.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} has invalid type")
    return value


def load_configuration(path: Path) -> SystemConfiguration:
    try:
        raw = cast(dict[str, object], tomllib.loads(path.read_text(encoding="utf-8")))
        reader = _table(raw, "reader")
        serial = _table(raw, "serial")
        gpio = _table(raw, "gpio")
        reconnect = _table(raw, "reconnect")
        escalation = _table(raw, "escalation")
        by_id_path = _optional_string(reader, "by_id_path")
        identity = ReaderIdentity(
            by_id_path=Path(by_id_path) if by_id_path else None,
            vid=_optional_integer(reader, "vid"),
            pid=_optional_integer(reader, "pid"),
            serial_number=_optional_string(reader, "serial_number"),
            manufacturer=_optional_string(reader, "manufacturer"),
            product=_optional_string(reader, "product"),
        )
        serial_config = SerialConfiguration(
            baud_rate=_integer(serial, "baud_rate"),
            data_bits=_integer(serial, "data_bits"),
            parity=_string(serial, "parity"),
            stop_bits=_integer(serial, "stop_bits"),
            timeout_seconds=_number(serial, "timeout_seconds"),
            maximum_record_bytes=_integer(serial, "maximum_record_bytes"),
        )
        gpio_config = GpioConfiguration(
            numbering=_string(gpio, "numbering"),
            channel=_integer(gpio, "channel"),
            normal_high=_boolean(gpio, "normal_high"),
            release_high=_boolean(gpio, "release_high"),
            release_seconds=_number(gpio, "release_seconds"),
        )
        backoff = BackoffPolicy(
            minimum=_number(reconnect, "minimum_seconds"),
            maximum=_number(reconnect, "maximum_seconds"),
            jitter=_number(reconnect, "jitter"),
        )
        escalation_policy = EscalationPolicy(
            warning_count=_integer(escalation, "warning_count"),
            warning_window=_number(escalation, "warning_window_seconds"),
            critical_count=_integer(escalation, "critical_count"),
            critical_window=_number(escalation, "critical_window_seconds"),
            reset_after=_number(escalation, "reset_after_seconds"),
        )
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(str(exc)) from exc
    if serial_config != SerialConfiguration(9600, 8, "none", 1, 1.0, 16):
        raise ConfigurationError("serial configuration violates the approved reader contract")
    if gpio_config != GpioConfiguration("BCM", 23, False, True, 3.0):
        raise ConfigurationError("GPIO configuration violates the approved GPIO contract")
    return SystemConfiguration(identity, serial_config, gpio_config, backoff, escalation_policy)
