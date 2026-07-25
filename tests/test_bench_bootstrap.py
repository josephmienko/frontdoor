from __future__ import annotations

from pathlib import Path


def test_bench_bootstrap_is_safe_and_versioned() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = (repository / "scripts" / "bootstrap-rp4-bench.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert "--dry-run" in script
    assert "VERSION_CODENAME" in script
    assert '"arm64"' in script
    assert "python3.13" in script
    assert "/opt/bridgewire" in script
    assert "/releases/$version" in script
    assert "sha256sum" in script
    assert "/usr/sbin/nologin" in script
    assert "ln -sfn" in script
    assert "serve-simulated" in script
    assert "NoNewPrivileges=true" in script
    assert "ProtectSystem=strict" in script
    assert 'chgrp -R bridgewire "$venv_dir"' in script
    assert 'chmod -R g+rX,o-rwx "$venv_dir"' in script
    assert "private key" not in script.lower()


def test_bench_bootstrap_service_is_explicitly_opt_in() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = (repository / "scripts" / "bootstrap-rp4-bench.sh").read_text(encoding="utf-8")

    marker = "--install-service)"
    assert marker in script
    assert "INSTALL_SERVICE=false" in script
    assert 'if "${INSTALL_SERVICE:-false}"' in script
