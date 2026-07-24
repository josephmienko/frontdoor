from __future__ import annotations

from pathlib import Path

import pytest

from bridgewire.reader import (
    BackoffPolicy,
    DiscoveryStatus,
    ReaderDisconnectedError,
    ReaderEvent,
    ReaderEventType,
    ReaderIdentity,
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
