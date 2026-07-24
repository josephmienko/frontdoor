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
def schema() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(Path("schemas/authorization-file/schema.json").read_text(encoding="utf-8")),
    )


@pytest.fixture
def authorization(schema: dict[str, object]) -> AuthorizationStore:
    store = AuthorizationStore(AuthorizationFile(schema))
    store.reload(Path("tests/fixtures/authorization/valid.csv"))
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
