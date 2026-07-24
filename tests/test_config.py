from pathlib import Path

import pytest

from access_control.config import ConfigurationError, load_config


@pytest.mark.unit
def test_local_config_is_valid() -> None:
    config = load_config(Path("configs/local.yaml"))
    assert config.unlock_seconds == 1.0
    assert config.hardware.relay == "simulated"


@pytest.mark.unit
def test_unsafe_duration_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "door:\n  unlock_seconds: 0\n"
        "hardware:\n  card_source: simulated\n"
        "  exit_button: simulated\n  relay: simulated\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(path)
