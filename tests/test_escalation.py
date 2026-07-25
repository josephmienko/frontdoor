from __future__ import annotations

from collections.abc import Callable

import pytest

from bridgewire.audit import EventType, InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationLevel, EscalationPolicy, EscalationTracker
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timestamps", "expected"),
    [
        ([0, 30, 60], EscalationLevel.WARNING),
        ([0, 30, 60.001], EscalationLevel.NONE),
        ([0, 75, 150, 225, 300], EscalationLevel.CRITICAL),
        ([0, 75, 150, 225, 300.001], EscalationLevel.NONE),
    ],
)
def test_escalation_exact_window_boundaries(
    timestamps: list[float], expected: EscalationLevel
) -> None:
    tracker = EscalationTracker()
    emitted = [tracker.record(timestamp) for timestamp in timestamps]
    assert emitted[-1] is expected


@pytest.mark.unit
def test_thresholds_emit_once_until_new_cycle() -> None:
    tracker = EscalationTracker()
    emitted = [tracker.record(float(index)) for index in range(7)]
    assert emitted.count(EscalationLevel.WARNING) == 1
    assert emitted.count(EscalationLevel.CRITICAL) == 1
    assert tracker.record(906) is EscalationLevel.NONE
    assert tracker.level is EscalationLevel.NONE


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: EscalationPolicy(warning_count=0),
        lambda: EscalationPolicy(critical_count=3),
        lambda: EscalationPolicy(warning_window=0),
        lambda: EscalationPolicy(critical_window=59),
        lambda: EscalationPolicy(reset_after=0),
    ],
)
def test_invalid_escalation_policy_is_rejected(
    factory: Callable[[], EscalationPolicy],
) -> None:
    with pytest.raises(ValueError, match=r"thresholds|windows"):
        factory()


@pytest.mark.unit
def test_custom_escalation_policy() -> None:
    tracker = EscalationTracker(EscalationPolicy(2, 10, 3, 20, 30))
    assert tracker.record(0) is EscalationLevel.NONE
    assert tracker.record(10) is EscalationLevel.WARNING
    assert tracker.record(20) is EscalationLevel.CRITICAL


@pytest.mark.unit
@pytest.mark.parametrize(
    ("next_timestamp", "expected_level"),
    [
        (903.999, EscalationLevel.CRITICAL),
        (904.0, EscalationLevel.NONE),
        (904.001, EscalationLevel.NONE),
    ],
)
def test_quiet_reset_adjacent_boundaries(
    next_timestamp: float, expected_level: EscalationLevel
) -> None:
    tracker = EscalationTracker()
    for timestamp in range(5):
        tracker.record(float(timestamp))
    tracker.record(next_timestamp)
    assert tracker.level is expected_level
