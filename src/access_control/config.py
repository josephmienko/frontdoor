from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HardwareConfig:
    card_source: str
    exit_button: str
    relay: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    unlock_seconds: float
    hardware: HardwareConfig


def _string_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a mapping with string keys")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ConfigurationError(f"{name} must be a mapping with string keys")
    return cast(dict[str, object], mapping)


def load_config(path: Path) -> AppConfig:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        root = _string_mapping(raw, "configuration root")
        door = _string_mapping(root["door"], "door")
        hardware = _string_mapping(root["hardware"], "hardware")
        raw_unlock_seconds = door["unlock_seconds"]
        if isinstance(raw_unlock_seconds, bool) or not isinstance(raw_unlock_seconds, (int, float)):
            raise ConfigurationError("door.unlock_seconds must be a number")
        unlock_seconds = float(raw_unlock_seconds)
        if unlock_seconds <= 0:
            raise ConfigurationError("door.unlock_seconds must be positive")
        values = HardwareConfig(
            card_source=str(hardware["card_source"]),
            exit_button=str(hardware["exit_button"]),
            relay=str(hardware["relay"]),
        )
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(str(exc)) from exc

    supported = {"simulated"}
    for name, value in (
        ("card_source", values.card_source),
        ("exit_button", values.exit_button),
        ("relay", values.relay),
    ):
        if value not in supported:
            raise ConfigurationError(f"hardware.{name}: unsupported adapter {value!r}")
    return AppConfig(unlock_seconds=unlock_seconds, hardware=values)
