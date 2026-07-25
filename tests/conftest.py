from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from bridgewire.audit import InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationTracker
from bridgewire.gpio import SimulatedRelay


@pytest.fixture
def forbidden_values() -> tuple[str, ...]:
    return (
        "A1B2C3D4E5",
        "A1-B2-C3-D4-E5",
        "Sanitized Cardholder",
        "https://hooks.invalid/FAKE-WEBHOOK-SECRET",
        "FAKE-API-TOKEN-123",
        "FAKE-USB-SERIAL-987",
    )


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def tests_root(repo_root: Path) -> Path:
    return repo_root / "tests"


@pytest.fixture
def fixture_root(tests_root: Path) -> Path:
    return tests_root / "fixtures"


@pytest.fixture
def authorization_fixture_root(fixture_root: Path) -> Path:
    return fixture_root / "authorization"


@pytest.fixture
def schema_root(repo_root: Path) -> Path:
    return repo_root / "schemas"


@pytest.fixture
def schema(schema_root: Path) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(
            (schema_root / "authorization-file" / "schema.json").read_text(encoding="utf-8")
        ),
    )


@pytest.fixture
def authorization(
    schema: dict[str, object], authorization_fixture_root: Path
) -> AuthorizationStore:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(authorization_fixture_root / "valid.csv")
    return store


@pytest.fixture
def system(
    authorization: AuthorizationStore,
) -> tuple[
    AccessController,
    ManualClock,
    SimulatedRelay,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
]:
    clock = ManualClock()
    relay = SimulatedRelay(clock)
    audit = InMemoryAuditSink()
    notifications = InMemoryNotificationQueue()
    controller = AccessController(
        authorization=authorization,
        relay=relay,
        audit=audit,
        notifications=notifications,
        clock=clock,
        escalation=EscalationTracker(),
    )
    return controller, clock, relay, audit, notifications
