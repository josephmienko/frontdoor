from __future__ import annotations

import pytest

from bridgewire.audit import EventType, InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController
from bridgewire.gpio import SimulatedRelay
from bridgewire.reader import MalformedRecord, ParsedRecord

System = tuple[
    AccessController,
    ManualClock,
    SimulatedRelay,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
]


def suspicious(controller: AccessController, kind: int) -> None:
    records = (
        ParsedRecord("1112131415"),
        ParsedRecord("FFFFFFFFFF"),
        MalformedRecord("invalid_framing"),
    )
    controller.process(records[kind % len(records)])


@pytest.mark.unit
def test_fewer_than_three_events_do_not_escalate(system: System) -> None:
    controller, _clock, _relay, audit, _notifications = system
    controller.start()
    suspicious(controller, 0)
    suspicious(controller, 1)
    assert not any("escalation" in event.event_type.value for event in audit.events)


@pytest.mark.unit
def test_warning_at_three_within_sixty_seconds(system: System) -> None:
    controller, clock, _relay, audit, _notifications = system
    controller.start()
    for index in range(3):
        suspicious(controller, index)
        clock.advance(20)
    assert EventType.ESCALATION_WARNING in [event.event_type for event in audit.events]


@pytest.mark.unit
def test_events_outside_warning_window_do_not_warn(system: System) -> None:
    controller, clock, _relay, audit, _notifications = system
    controller.start()
    for index in range(3):
        suspicious(controller, index)
        clock.advance(61)
    assert EventType.ESCALATION_WARNING not in [event.event_type for event in audit.events]


@pytest.mark.unit
def test_critical_at_five_queues_notification_without_blocking(system: System) -> None:
    controller, clock, _relay, audit, notifications = system
    controller.start()
    notifications.available = False
    for index in range(5):
        suspicious(controller, index)
        clock.advance(30)
    assert EventType.ESCALATION_CRITICAL in [event.event_type for event in audit.events]
    assert len(notifications.events) == 1
    controller.process(ParsedRecord("0102030405"))


@pytest.mark.unit
def test_escalation_resets_after_fifteen_quiet_minutes(system: System) -> None:
    controller, clock, _relay, audit, notifications = system
    controller.start()
    for index in range(5):
        suspicious(controller, index)
    clock.advance(900)
    suspicious(controller, 0)
    assert len(notifications.events) == 1
    assert [event.event_type for event in audit.events].count(EventType.ESCALATION_WARNING) == 1
