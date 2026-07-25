from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from bridgewire.reader import (
    BackoffPolicy,
    DiscoveryStatus,
    ReaderDisconnectedError,
    ReaderEvent,
    ReaderEventType,
    ReaderHealthState,
    ReaderIdentity,
    ReaderRecordStream,
    ReaderSession,
    ReaderSupervisor,
    SerialDevice,
    discover_reader,
)
from bridgewire.simulation import SimulatedReaderSession

IDENTITY = ReaderIdentity(vid=0x1234, pid=0x5678, serial_number="SIMULATED")
MATCH = SerialDevice(
    path=Path("/dev/ttyUSB7"),
    vid=0x1234,
    pid=0x5678,
    serial_number="SIMULATED",
)
UNRELATED = SerialDevice(path=Path("/dev/ttyACM0"), vid=0x9999, pid=0x0001)


@pytest.mark.unit
def test_discovery_not_found_ignores_unrelated_devices() -> None:
    result = discover_reader([UNRELATED], IDENTITY)
    assert result.status is DiscoveryStatus.NOT_FOUND
    assert result.unrelated_count == 1


@pytest.mark.unit
def test_discovery_finds_one_stable_identity_despite_path_change() -> None:
    moved = SerialDevice(
        path=Path("/dev/ttyUSB9"),
        vid=MATCH.vid,
        pid=MATCH.pid,
        serial_number=MATCH.serial_number,
    )
    result = discover_reader([UNRELATED, moved], IDENTITY)
    assert result.status is DiscoveryStatus.FOUND
    assert result.device == moved


@pytest.mark.unit
def test_discovery_rejects_identity_ambiguity() -> None:
    second = SerialDevice(
        path=Path("/dev/ttyUSB8"),
        vid=MATCH.vid,
        pid=MATCH.pid,
        serial_number=MATCH.serial_number,
    )
    result = discover_reader([MATCH, second], IDENTITY)
    assert result.status is DiscoveryStatus.AMBIGUOUS
    assert result.device is None


@pytest.mark.unit
def test_by_id_path_can_identify_reader_without_placeholder_vid_pid() -> None:
    stable = Path("/dev/serial/by-id/bridgewire-reader")
    result = discover_reader(
        [SerialDevice(Path("/dev/ttyUSB3"), by_id_path=stable)],
        ReaderIdentity(by_id_path=stable),
    )
    assert result.status is DiscoveryStatus.FOUND


@pytest.mark.unit
def test_bounded_exponential_backoff() -> None:
    policy = BackoffPolicy(minimum=1, maximum=4, jitter=0)
    assert [policy.delay(attempt) for attempt in range(1, 6)] == [1, 2, 4, 4, 4]


@pytest.mark.failure_mode
def test_not_found_repeated_failure_and_interruptible_wait() -> None:
    events: list[ReaderEvent] = []
    waits: list[float] = []

    def wait(delay: float) -> bool:
        waits.append(delay)
        return len(waits) < 3

    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [UNRELATED],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=wait,
        emit=events.append,
        backoff=BackoffPolicy(1, 4, 0),
        repeated_failure_threshold=2,
    )
    assert not supervisor.connect_until_ready(10)
    types = [event.event_type for event in events]
    assert types.count(ReaderEventType.READER_NOT_FOUND) == 3
    assert ReaderEventType.RECONNECT_REPEATEDLY_FAILED in types
    assert waits == [1, 2, 4]


@pytest.mark.failure_mode
def test_open_failure_then_eventual_recovery() -> None:
    events: list[ReaderEvent] = []
    opens = 0

    def open_reader(_path: Path) -> SimulatedReaderSession:
        nonlocal opens
        opens += 1
        if opens == 1:
            raise OSError("simulated")
        return SimulatedReaderSession([])

    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=open_reader,
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(2)
    assert [event.event_type for event in events] == [
        ReaderEventType.READER_CONNECTING,
        ReaderEventType.READER_OPEN_FAILED,
        ReaderEventType.RECONNECT_SCHEDULED,
        ReaderEventType.READER_CONNECTING,
        ReaderEventType.READER_CONNECTED,
    ]


@pytest.mark.failure_mode
@pytest.mark.parametrize(
    ("failure", "event_type"),
    [
        (ReaderDisconnectedError("gone"), ReaderEventType.READER_DISCONNECTED),
        (OSError("read failed"), ReaderEventType.READER_READ_FAILED),
    ],
)
def test_read_failure_disconnect_and_recovery(
    failure: Exception, event_type: ReaderEventType
) -> None:
    events: list[ReaderEvent] = []
    sessions = [SimulatedReaderSession([failure]), SimulatedReaderSession([])]
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: sessions.pop(0),
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(1)
    assert supervisor.read_once() is None
    assert event_type in [event.event_type for event in events]
    assert supervisor.connect_until_ready(1)
    assert events[-1].event_type is ReaderEventType.READER_RECOVERED


@pytest.mark.unit
def test_reader_silence_is_not_classified_as_failure() -> None:
    events: list[ReaderEvent] = []
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(1)
    assert supervisor.read_once() == b""
    assert [event.event_type for event in events] == [
        ReaderEventType.READER_CONNECTING,
        ReaderEventType.READER_CONNECTED,
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("identity", "device"),
    [
        (
            ReaderIdentity(vid=1, pid=2, manufacturer="Fixture Manufacturer"),
            SerialDevice(Path("m"), vid=1, pid=2, manufacturer="Fixture Manufacturer"),
        ),
        (
            ReaderIdentity(vid=1, pid=2, product="Fixture Reader"),
            SerialDevice(Path("p"), vid=1, pid=2, product="Fixture Reader"),
        ),
        (
            ReaderIdentity(vid=1, pid=2, serial_number="FAKE-SERIAL"),
            SerialDevice(Path("s"), vid=1, pid=2, serial_number="FAKE-SERIAL"),
        ),
    ],
)
def test_optional_identity_attributes_match(identity: ReaderIdentity, device: SerialDevice) -> None:
    assert discover_reader([UNRELATED, device], identity).device == device


@pytest.mark.unit
def test_serial_mismatch_and_ambiguous_vid_pid() -> None:
    mismatch = SerialDevice(Path("x"), vid=0x1234, pid=0x5678, serial_number="OTHER")
    assert discover_reader([mismatch], IDENTITY).status is DiscoveryStatus.NOT_FOUND
    generic = ReaderIdentity(vid=1, pid=2)
    devices = [SerialDevice(Path(str(index)), vid=1, pid=2) for index in range(2)]
    assert discover_reader(devices, generic).status is DiscoveryStatus.AMBIGUOUS


@pytest.mark.unit
def test_non_unique_by_id_identity_rejects_broad_metadata_ambiguity() -> None:
    stable = Path("/dev/serial/by-id/fixture")
    identity = ReaderIdentity(by_id_path=stable, vid=1, pid=2)
    exact = SerialDevice(Path("exact"), stable, vid=1, pid=2)
    broad = SerialDevice(Path("broad"), vid=1, pid=2)
    assert discover_reader([broad, exact], identity).status is DiscoveryStatus.AMBIGUOUS


@pytest.mark.failure_mode
def test_unusable_open_result_is_reported_as_open_failure() -> None:
    events: list[ReaderEvent] = []
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: cast(ReaderSession, object()),
        wait=lambda _delay: False,
        emit=events.append,
    )
    assert not supervisor.connect_until_ready(1)
    assert ReaderEventType.READER_OPEN_FAILED in [event.event_type for event in events]


@pytest.mark.unit
def test_nonempty_read_emits_record_received_and_close_is_exactly_once() -> None:
    class CountingSession:
        def __init__(self) -> None:
            self.closes = 0

        def read(self) -> bytes:
            return b"sanitized"

        def close(self) -> None:
            self.closes += 1

    events: list[ReaderEvent] = []
    session = CountingSession()
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: session,
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(1)
    assert supervisor.read_once() == b"sanitized"
    assert events[-1].event_type is ReaderEventType.READER_RECORD_RECEIVED
    supervisor.close()
    supervisor.close()
    assert session.closes == 1


@pytest.mark.failure_mode
def test_repeated_reconnect_alert_emits_once_per_degraded_period() -> None:
    events: list[ReaderEvent] = []
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _delay: True,
        emit=events.append,
        repeated_failure_threshold=2,
    )
    assert not supervisor.connect_until_ready(6)
    assert [event.event_type for event in events].count(
        ReaderEventType.RECONNECT_REPEATEDLY_FAILED
    ) == 1


@pytest.mark.failure_mode
def test_each_connect_call_receives_its_own_attempt_budget() -> None:
    events: list[ReaderEvent] = []
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=lambda _delay: True,
        emit=events.append,
    )

    assert not supervisor.connect_until_ready(1)
    first_connecting_count = [event.event_type for event in events].count(
        ReaderEventType.READER_CONNECTING
    )

    assert not supervisor.connect_until_ready(1)
    assert [event.event_type for event in events].count(
        ReaderEventType.READER_CONNECTING
    ) == first_connecting_count + 1


@pytest.mark.failure_mode
def test_strict_decode_failure_emits_private_malformed_event() -> None:
    events: list[ReaderEvent] = []
    session = SimulatedReaderSession([b"\x02A1B2C3D4E500\r\n\x03"])
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: session,
        wait=lambda _delay: True,
        emit=events.append,
    )
    assert supervisor.connect_until_ready(1)
    results = supervisor.read_records_once(ReaderRecordStream())
    assert results
    assert events[-1] == ReaderEvent(ReaderEventType.READER_RECORD_MALFORMED)
    assert "A1B2C3D4E5" not in str(events)


@pytest.mark.unit
def test_last_record_age_is_telemetry_only() -> None:
    now = 10.0
    events: list[ReaderEvent] = []
    session = SimulatedReaderSession([b"record", b""])
    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [MATCH],
        open_reader=lambda _path: session,
        wait=lambda _delay: True,
        emit=events.append,
        monotonic=lambda: now,
    )
    assert supervisor.connect_until_ready(1)
    supervisor.read_once()
    now = 10_000.0
    assert supervisor.last_record_age == 9990
    assert supervisor.read_once() == b""
    assert supervisor.health_state is ReaderHealthState.READY
    assert supervisor.connected
    assert ReaderEventType.RECONNECT_SCHEDULED not in [event.event_type for event in events]


@pytest.mark.failure_mode
def test_shutdown_during_reconnect_wait_is_prompt(system: tuple[object, ...]) -> None:
    from bridgewire.controller import AccessController, ControllerState

    controller = cast(AccessController, system[0])
    controller.start()

    def interrupt(_delay: float) -> bool:
        controller.shutdown()
        return False

    supervisor = ReaderSupervisor(
        identity=IDENTITY,
        enumerate_devices=lambda: [],
        open_reader=lambda _path: SimulatedReaderSession([]),
        wait=interrupt,
        emit=lambda _event: None,
    )
    assert not supervisor.connect_until_ready(10)
    assert controller.state is ControllerState.STOPPED
