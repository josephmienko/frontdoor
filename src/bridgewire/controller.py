from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


class RelayCommand(StrEnum):
    RELEASED = "released"
    SECURED = "secured"


@dataclass(frozen=True, slots=True)
class AccessResult:
    authorization: AuthorizationOutcome | None
    physical_release: PhysicalReleaseStatus


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    state: ControllerState
    release_active: bool
    release_deadline_monotonic: float | None
    release_remaining_seconds: float | None
    configured_release_seconds: float
    last_relay_command: RelayCommand | None
    last_credential_processed_at: datetime | None


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
        self._last_relay_command_high: bool | None = None
        self._last_credential_processed_at: datetime | None = None

    def snapshot(self) -> ControllerSnapshot:
        remaining = (
            max(0.0, self.release_deadline - self._clock.monotonic())
            if self.release_deadline is not None
            else None
        )
        return ControllerSnapshot(
            state=self.state,
            release_active=self.state is ControllerState.RELEASED,
            release_deadline_monotonic=self.release_deadline,
            release_remaining_seconds=remaining,
            configured_release_seconds=self._release_seconds,
            last_relay_command=(
                RelayCommand.RELEASED
                if self._last_relay_command_high is True
                else RelayCommand.SECURED
                if self._last_relay_command_high is False
                else None
            ),
            last_credential_processed_at=self._last_credential_processed_at,
        )

    def start(self) -> None:
        if self.state is ControllerState.READY or self.state is ControllerState.RELEASED:
            return
        if self.state is not ControllerState.INITIALIZING:
            raise RuntimeError("controller cannot be restarted")
        try:
            self._relay.setup(numbering="BCM", channel=self._gpio_channel)
        except Exception:
            self.state = ControllerState.FAULTED
            self._audit(
                EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL, {"reason": "setup_failed"}
            )
            raise
        try:
            self._relay.command(False)
            self._last_relay_command_high = False
        except Exception:
            self.state = ControllerState.FAULTED
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL, {"reason": "low_failed"})
            raise
        self.state = ControllerState.READY
        self._audit(EventType.SERVICE_STARTED, Severity.INFO)

    def process(self, record: RecordResult) -> AccessResult:
        if self.state not in {ControllerState.READY, ControllerState.RELEASED}:
            raise RuntimeError("controller is not accepting credentials")
        if isinstance(record, MalformedRecord):
            safe_reason = (
                record.reason
                if record.reason
                in {
                    "invalid_length",
                    "invalid_framing",
                    "invalid_terminator",
                    "invalid_encoding",
                    "invalid_identifier",
                    "invalid_checksum_encoding",
                    "checksum_mismatch",
                    "excessive_length",
                }
                else "malformed_record"
            )
            self._audit(
                EventType.MALFORMED_RECORD,
                Severity.WARNING,
                {"reason": safe_reason},
            )
            self._record_suspicious()
            return AccessResult(None, PhysicalReleaseStatus.NOT_REQUESTED)
        return self._process_parsed(record)

    def _process_parsed(self, record: ParsedRecord) -> AccessResult:
        outcome = self._authorization.classify(record.credential)
        self._last_credential_processed_at = self._clock.now()
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
            self._last_relay_command_high = True
        except Exception:
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL, {"reason": "high_failed"})
            self._attempt_safe_state()
            self.state = ControllerState.FAULTED
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
                self._last_relay_command_high = False
            except Exception:
                self.state = ControllerState.FAULTED
                self._audit(
                    EventType.RELAY_CONTROL_ERROR,
                    Severity.CRITICAL,
                    {"reason": "low_failed"},
                )
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
        if self.state is ControllerState.STOPPED:
            return
        self.state = ControllerState.SHUTTING_DOWN
        safe = self._attempt_safe_state()
        self.release_deadline = None
        cleanup_ok = True
        try:
            self._relay.cleanup()
        except Exception:
            cleanup_ok = False
            self._audit(
                EventType.RELAY_CONTROL_ERROR,
                Severity.CRITICAL,
                {"reason": "cleanup_failed"},
            )
        if safe and cleanup_ok:
            self.state = ControllerState.STOPPED
            self._audit(EventType.SERVICE_SHUTDOWN, Severity.INFO)
        else:
            self.state = ControllerState.FAULTED

    def _attempt_safe_state(self) -> bool:
        try:
            self._relay.command(False)
            self._last_relay_command_high = False
        except Exception:
            self._audit(EventType.RELAY_CONTROL_ERROR, Severity.CRITICAL, {"reason": "low_failed"})
            return False
        return True

    def _record_suspicious(self) -> None:
        escalation = self._escalation.record(self._clock.monotonic())
        if escalation is EscalationLevel.WARNING:
            self._audit(EventType.ESCALATION_WARNING, Severity.WARNING)
        elif escalation is EscalationLevel.CRITICAL:
            event = self._new_event(EventType.ESCALATION_CRITICAL, Severity.CRITICAL)
            self._audit_sink.append(event)
            try:
                self._notifications.enqueue(event)
            except Exception:
                self._audit(
                    EventType.NOTIFICATION_DELIVERY_FAILED,
                    Severity.ERROR,
                    {"reason": "queue_unavailable"},
                )

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
