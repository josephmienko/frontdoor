from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from bridgewire.authorization import AuthorizationOutcome
from bridgewire.controller import (
    AccessController,
    AccessResult,
    ControllerSnapshot,
    ControllerState,
    PhysicalReleaseStatus,
)
from bridgewire.reader import (
    MalformedRecord,
    ParsedRecord,
    ReaderRecordError,
    RecordResult,
    parse_credential_identifier,
)


class CredentialSource(StrEnum):
    PHYSICAL_READER = "physical_reader"
    SIMULATOR = "simulator"
    TEST = "test"
    API_INJECTED_READER_EVENT = "api_injected_reader_event"


@dataclass(frozen=True, slots=True)
class AccessServiceResult:
    authorized: bool
    authorization: AuthorizationOutcome | None
    malformed: bool
    controller_state: ControllerState
    physical_release: PhysicalReleaseStatus
    relay_actuation_requested: bool
    relay_actuation_succeeded: bool
    source: CredentialSource

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

    def snapshot(self) -> ControllerSnapshot:
        return self._controller.snapshot()

    def start(self) -> None:
        self._controller.start()

    def submit_record(
        self,
        record: RecordResult,
        *,
        source: CredentialSource,
    ) -> AccessServiceResult:
        return self._map(
            self._controller.process(record),
            source,
            malformed=isinstance(record, MalformedRecord),
        )

    def submit_parsed_record(
        self,
        record: ParsedRecord,
        *,
        source: CredentialSource,
    ) -> AccessServiceResult:
        return self.submit_record(record, source=source)

    def submit_credential(
        self,
        credential: str,
        *,
        source: CredentialSource,
    ) -> AccessServiceResult:
        try:
            record: RecordResult = parse_credential_identifier(credential)
        except ReaderRecordError as exc:
            record = MalformedRecord(exc.reason)
        return self.submit_record(record, source=source)

    def tick(self) -> None:
        self._controller.tick()

    def _recoverable_failure(self) -> None:
        self._controller.recoverable_failure()

    def shutdown(self) -> None:
        self._controller.shutdown()

    def _map(
        self,
        result: AccessResult,
        source: CredentialSource,
        *,
        malformed: bool,
    ) -> AccessServiceResult:
        return AccessServiceResult(
            authorized=result.authorization is AuthorizationOutcome.AUTHORIZED,
            authorization=result.authorization,
            malformed=malformed,
            controller_state=self._controller.state,
            physical_release=result.physical_release,
            relay_actuation_requested=result.physical_release
            in {
                PhysicalReleaseStatus.ASSERTED,
                PhysicalReleaseStatus.ACTUATION_FAILED,
            },
            relay_actuation_succeeded=(result.physical_release is PhysicalReleaseStatus.ASSERTED),
            source=source,
        )
