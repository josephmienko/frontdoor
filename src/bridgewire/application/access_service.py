from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from bridgewire.authorization import AuthorizationOutcome
from bridgewire.controller import (
    AccessController,
    AccessResult,
    ControllerState,
    PhysicalReleaseStatus,
)
from bridgewire.reader import ParsedRecord, RecordResult


class CredentialSource(StrEnum):
    PHYSICAL_READER = "physical_reader"
    SIMULATOR = "simulator"
    TEST = "test"
    API_INJECTED_READER_EVENT = "api_injected_reader_event"


@dataclass(frozen=True, slots=True)
class AccessServiceResult:
    accepted: bool
    authorization: AuthorizationOutcome | None
    controller_state: ControllerState
    physical_release: PhysicalReleaseStatus
    release_initiated: bool
    source: CredentialSource
    audit_event_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            key: value.value if isinstance(value, StrEnum) else value
            for key, value in asdict(self).items()
        }


class AccessService:
    """Stable application use cases above the access-control state machine."""

    def __init__(self, controller: AccessController) -> None:
        self._controller = controller

    @property
    def controller_state(self) -> ControllerState:
        return self._controller.state

    def start(self) -> None:
        self._controller.start()

    def submit_record(
        self,
        record: RecordResult,
        *,
        source: CredentialSource,
    ) -> AccessServiceResult:
        return self._map(self._controller.process(record), source)

    def submit_parsed_record(
        self,
        record: ParsedRecord,
        *,
        source: CredentialSource = CredentialSource.PHYSICAL_READER,
    ) -> AccessServiceResult:
        return self.submit_record(record, source=source)

    def submit_credential(
        self,
        credential: str,
        *,
        source: CredentialSource,
    ) -> AccessServiceResult:
        if re.fullmatch(r"[0-9A-F]{10}", credential) is None:
            raise ValueError("credential must be ten uppercase hexadecimal characters")
        return self.submit_parsed_record(ParsedRecord(credential), source=source)

    def tick(self) -> None:
        self._controller.tick()

    def recoverable_failure(self) -> None:
        self._controller.recoverable_failure()

    def shutdown(self) -> None:
        self._controller.shutdown()

    def _map(self, result: AccessResult, source: CredentialSource) -> AccessServiceResult:
        return AccessServiceResult(
            accepted=result.authorization is AuthorizationOutcome.AUTHORIZED,
            authorization=result.authorization,
            controller_state=self._controller.state,
            physical_release=result.physical_release,
            release_initiated=(result.physical_release is PhysicalReleaseStatus.ASSERTED),
            source=source,
        )
