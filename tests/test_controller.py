from __future__ import annotations

import pytest

from bridgewire.audit import EventType, InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.authorization import AuthorizationOutcome
from bridgewire.clock import ManualClock
from bridgewire.controller import (
    AccessController,
    ControllerState,
    PhysicalReleaseStatus,
)
from bridgewire.gpio import RelayActionType, SimulatedRelay
from bridgewire.reader import MalformedRecord, ParsedRecord

System = tuple[
    AccessController,
    ManualClock,
    SimulatedRelay,
    InMemoryAuditSink,
    InMemoryNotificationQueue,
]


@pytest.mark.unit
def test_startup_explicitly_commands_low_before_ready(system: System) -> None:
    controller, _clock, relay, audit, _notifications = system
    controller.start()
    assert [action.action for action in relay.actions] == [
        RelayActionType.SETUP,
        RelayActionType.LOW,
    ]
    assert relay.numbering == "BCM"
    assert relay.channel == 23
    assert controller.state is ControllerState.READY
    assert audit.events[-1].event_type is EventType.SERVICE_STARTED


@pytest.mark.unit
def test_authorized_release_is_non_blocking_and_restored_at_deadline(system: System) -> None:
    controller, clock, relay, audit, _notifications = system
    controller.start()
    result = controller.process(ParsedRecord("0102030405"))
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert result.physical_release is PhysicalReleaseStatus.ASSERTED
    assert relay.is_high
    assert controller.release_deadline == 3
    clock.advance(2.999)
    controller.tick()
    assert relay.is_high
    clock.advance(0.001)
    controller.tick()
    assert not relay.is_high
    assert audit.events[-1].event_type is EventType.RELAY_RESTORED


@pytest.mark.unit
def test_second_authorized_credential_does_not_extend_deadline(system: System) -> None:
    controller, clock, relay, audit, _notifications = system
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    original_deadline = controller.release_deadline
    clock.advance(1)
    result = controller.process(ParsedRecord("0102030405"))
    assert result.physical_release is PhysicalReleaseStatus.ALREADY_RELEASED
    assert controller.release_deadline == original_deadline
    assert [action.action for action in relay.actions].count(RelayActionType.HIGH) == 1
    assert [event.event_type for event in audit.events].count(EventType.CREDENTIAL_AUTHORIZED) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("record", "expected_event"),
    [
        (ParsedRecord("1112131415"), EventType.CREDENTIAL_DENIED),
        (ParsedRecord("FFFFFFFFFF"), EventType.CREDENTIAL_UNKNOWN),
        (MalformedRecord("invalid_encoding"), EventType.MALFORMED_RECORD),
    ],
)
def test_non_authorized_records_never_assert_high(
    system: System, record: ParsedRecord | MalformedRecord, expected_event: EventType
) -> None:
    controller, _clock, relay, audit, _notifications = system
    controller.start()
    controller.process(record)
    assert RelayActionType.HIGH not in [action.action for action in relay.actions]
    assert audit.events[-1].event_type is expected_event


@pytest.mark.unit
def test_denied_unknown_and_malformed_are_processed_during_release(system: System) -> None:
    controller, _clock, _relay, audit, _notifications = system
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    controller.process(ParsedRecord("1112131415"))
    controller.process(ParsedRecord("FFFFFFFFFF"))
    controller.process(MalformedRecord("checksum_mismatch"))
    credential_events = [
        event.event_type
        for event in audit.events
        if event.event_type
        in {
            EventType.CREDENTIAL_DENIED,
            EventType.CREDENTIAL_UNKNOWN,
            EventType.MALFORMED_RECORD,
        }
    ]
    assert credential_events == [
        EventType.CREDENTIAL_DENIED,
        EventType.CREDENTIAL_UNKNOWN,
        EventType.MALFORMED_RECORD,
    ]


@pytest.mark.unit
def test_graceful_shutdown_commands_low_then_cleanup(system: System) -> None:
    controller, _clock, relay, audit, _notifications = system
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    controller.shutdown()
    assert [action.action for action in relay.actions][-2:] == [
        RelayActionType.LOW,
        RelayActionType.CLEANUP,
    ]
    assert controller.state is ControllerState.STOPPED
    assert audit.events[-1].event_type is EventType.SERVICE_SHUTDOWN


@pytest.mark.failure_mode
def test_failed_high_is_not_reported_as_physical_release(system: System) -> None:
    controller, _clock, relay, audit, _notifications = system
    controller.start()
    relay.fail_next_high = True
    result = controller.process(ParsedRecord("0102030405"))
    assert result.authorization is AuthorizationOutcome.AUTHORIZED
    assert result.physical_release is PhysicalReleaseStatus.ACTUATION_FAILED
    assert not relay.is_high
    assert controller.state is ControllerState.FAULTED
    assert audit.events[-1].event_type is EventType.RELAY_CONTROL_ERROR


@pytest.mark.failure_mode
def test_recoverable_failure_attempts_immediate_low(system: System) -> None:
    controller, _clock, relay, _audit, _notifications = system
    controller.start()
    controller.process(ParsedRecord("0102030405"))
    controller.recoverable_failure()
    assert not relay.is_high
    assert relay.actions[-1].action is RelayActionType.LOW
