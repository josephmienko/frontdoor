from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from bridgewire.audit import AuditEvent, EventType, Severity
from bridgewire.authorization import AuthorizationOutcome
from bridgewire.escalation import EscalationLevel, EscalationTracker
from bridgewire.interfaces import AuditSink, AuthorizationSource, Clock, NotificationQueue, Relay
from bridgewire.reader import MalformedRecord, ParsedRecord, RecordResult


class ControllerState(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    RELEASED = "released"
    FAULTED = "faulted"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class PhysicalReleaseStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    ASSERTED = "asserted"
    ALREADY_RELEASED = "already_released"
    ACTUATION_FAILED = "actuation_failed"


@dataclass(frozen=True, slots=True)
class AccessResult:
    authorization: AuthorizationOutcome | None
    physical_release: PhysicalReleaseStatus


class AccessController:
    def __init__(
        self,
        *,
        authorization: AuthorizationSource,
        relay: Relay,
        audit: AuditSink,
        notifications: NotificationQueue,
        clock: Clock,
        escalation: EscalationTracker,
        release_seconds: float = 3.0,
        gpio_channel: int = 23,
    ) -> None:
        if release_seconds <= 0:
            raise ValueError("release duration must be positive")
        self._authorization = authorization
        self._relay = relay
        self._audit_sink = audit
        self._notifications = notifications
        self._clock = clock
        self._escalation = escalation
        self._release_seconds = release_seconds
        self._gpio_channel = gpio_channel
        self.state = ControllerState.INITIALIZING
        self.release_deadline: float | None = None

    def start(self) -> None:
        self._relay.setup(numbering="BCM", channel=self._gpio_channel)
        try:
            self._relay.command(False)
        except Exception:
            self.state = ControllerState.FAULTED
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL)
            raise
        self.state = ControllerState.READY
        self._audit(EventType.SERVICE_STARTED, Severity.INFO)

    def process(self, record: RecordResult) -> AccessResult:
        if self.state not in {ControllerState.READY, ControllerState.RELEASED}:
            raise RuntimeError("controller is not accepting credentials")
        if isinstance(record, MalformedRecord):
            self._audit(
                EventType.MALFORMED_RECORD,
                Severity.WARNING,
                {"reason": record.reason},
            )
            self._record_suspicious()
            return AccessResult(None, PhysicalReleaseStatus.NOT_REQUESTED)
        return self._process_parsed(record)

    def _process_parsed(self, record: ParsedRecord) -> AccessResult:
        outcome = AuthorizationOutcome(self._authorization.classify(record.credential))
        if outcome is AuthorizationOutcome.DENIED:
            self._audit(EventType.CREDENTIAL_DENIED, Severity.WARNING)
            self._record_suspicious()
            return AccessResult(outcome, PhysicalReleaseStatus.NOT_REQUESTED)
        if outcome is AuthorizationOutcome.UNKNOWN:
            self._audit(EventType.CREDENTIAL_UNKNOWN, Severity.WARNING)
            self._record_suspicious()
            return AccessResult(outcome, PhysicalReleaseStatus.NOT_REQUESTED)
        self._audit(EventType.CREDENTIAL_AUTHORIZED, Severity.INFO)
        if self.state is ControllerState.RELEASED:
            return AccessResult(outcome, PhysicalReleaseStatus.ALREADY_RELEASED)
        try:
            self._relay.command(True)
        except Exception:
            self._attempt_safe_state()
            self.state = ControllerState.FAULTED
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL)
            return AccessResult(outcome, PhysicalReleaseStatus.ACTUATION_FAILED)
        self.release_deadline = self._clock.monotonic() + self._release_seconds
        self.state = ControllerState.RELEASED
        self._audit(
            EventType.RELAY_ASSERTED,
            Severity.INFO,
            {"duration_seconds": self._release_seconds},
        )
        return AccessResult(outcome, PhysicalReleaseStatus.ASSERTED)

    def tick(self) -> None:
        if (
            self.state is ControllerState.RELEASED
            and self.release_deadline is not None
            and self._clock.monotonic() >= self.release_deadline
        ):
            try:
                self._relay.command(False)
            except Exception:
                self.state = ControllerState.FAULTED
                self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL)
                return
            self.release_deadline = None
            self.state = ControllerState.READY
            self._audit(EventType.RELAY_RESTORED, Severity.INFO)

    def recoverable_failure(self) -> None:
        self._attempt_safe_state()
        self.release_deadline = None
        self.state = ControllerState.FAULTED
        self._audit(EventType.RELAY_CONTROL_ERROR, Severity.ERROR)

    def shutdown(self) -> None:
        self.state = ControllerState.SHUTTING_DOWN
        self._attempt_safe_state()
        self.release_deadline = None
        self._relay.cleanup()
        self.state = ControllerState.STOPPED
        self._audit(EventType.SERVICE_SHUTDOWN, Severity.INFO)

    def _attempt_safe_state(self) -> None:
        try:
            self._relay.command(False)
        except Exception:
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL)

    def _record_suspicious(self) -> None:
        escalation = self._escalation.record(self._clock.monotonic())
        if escalation is EscalationLevel.WARNING:
            self._audit(EventType.ESCALATION_WARNING, Severity.WARNING)
        elif escalation is EscalationLevel.CRITICAL:
            event = self._new_event(EventType.ESCALATION_CRITICAL, Severity.CRITICAL)
            self._audit_sink.append(event)
            self._notifications.enqueue(event)

    def _audit(
        self,
        event_type: EventType,
        severity: Severity,
        correlation: dict[str, str | int | float | bool | None] | None = None,
    ) -> None:
        self._audit_sink.append(self._new_event(event_type, severity, correlation))

    def _new_event(
        self,
        event_type: EventType,
        severity: Severity,
        correlation: dict[str, str | int | float | bool | None] | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            severity=severity,
            timestamp=self._clock.now(),
            correlation=MappingProxyType(correlation or {}),
            controller_state=self.state.value,
        )
