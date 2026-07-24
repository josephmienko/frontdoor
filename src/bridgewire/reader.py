from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

STX = 0x02
ETX = 0x03
RECORD_LENGTH = 16


class ReaderRecordError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    credential: str


@dataclass(frozen=True, slots=True)
class MalformedRecord:
    reason: str


RecordResult = ParsedRecord | MalformedRecord


def parse_reader_record(record: bytes) -> ParsedRecord:
    if len(record) != RECORD_LENGTH:
        raise ReaderRecordError("invalid_length")
    if record[0] != STX or record[-1] != ETX:
        raise ReaderRecordError("invalid_framing")
    if record[13:15] != b"\r\n":
        raise ReaderRecordError("invalid_terminator")
    try:
        data = record[1:11].decode("ascii", errors="strict")
        checksum_text = record[11:13].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReaderRecordError("invalid_encoding") from exc
    if len(data) != 10 or any(character not in "0123456789ABCDEF" for character in data):
        raise ReaderRecordError("invalid_identifier")
    if len(checksum_text) != 2 or any(
        character not in "0123456789ABCDEF" for character in checksum_text
    ):
        raise ReaderRecordError("invalid_checksum_encoding")
    checksum = 0
    for offset in range(0, len(data), 2):
        checksum ^= int(data[offset : offset + 2], 16)
    if checksum != int(checksum_text, 16):
        raise ReaderRecordError("checksum_mismatch")
    return ParsedRecord(data)


class ReaderRecordStream:
    def __init__(self, maximum_buffer: int = RECORD_LENGTH * 4) -> None:
        self._buffer = bytearray()
        self._maximum_buffer = maximum_buffer

    def feed(self, chunk: bytes) -> list[RecordResult]:
        if not chunk:
            return []
        self._buffer.extend(chunk)
        results: list[RecordResult] = []
        while self._buffer:
            if self._buffer[0] != STX:
                start = self._buffer.find(STX)
                if start < 0:
                    self._buffer.clear()
                else:
                    del self._buffer[:start]
                results.append(MalformedRecord("invalid_framing"))
                continue
            end = self._buffer.find(ETX, 1)
            if end < 0:
                if len(self._buffer) > self._maximum_buffer:
                    self._buffer.clear()
                    results.append(MalformedRecord("excessive_length"))
                break
            raw = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            try:
                results.append(parse_reader_record(raw))
            except ReaderRecordError as exc:
                results.append(MalformedRecord(exc.reason))
        return results


@dataclass(frozen=True, slots=True)
class ReaderIdentity:
    by_id_path: Path | None = None
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None

    def __post_init__(self) -> None:
        if self.by_id_path is None and (self.vid is None or self.pid is None):
            raise ValueError("reader identity requires by-id path or VID/PID")


@dataclass(frozen=True, slots=True)
class SerialDevice:
    path: Path
    by_id_path: Path | None = None
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None


class DiscoveryStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    status: DiscoveryStatus
    device: SerialDevice | None = None
    unrelated_count: int = 0


def _matches(device: SerialDevice, identity: ReaderIdentity) -> bool:
    if identity.by_id_path is not None and device.by_id_path == identity.by_id_path:
        return True
    if identity.vid is None or identity.pid is None:
        return False
    if device.vid != identity.vid or device.pid != identity.pid:
        return False
    checks = (
        (identity.serial_number, device.serial_number),
        (identity.manufacturer, device.manufacturer),
        (identity.product, device.product),
    )
    return all(expected is None or expected == actual for expected, actual in checks)


def discover_reader(
    devices: Sequence[SerialDevice],
    identity: ReaderIdentity,
) -> DiscoveryResult:
    matches = [device for device in devices if _matches(device, identity)]
    if not matches:
        return DiscoveryResult(DiscoveryStatus.NOT_FOUND, unrelated_count=len(devices))
    if len(matches) > 1:
        return DiscoveryResult(
            DiscoveryStatus.AMBIGUOUS, unrelated_count=len(devices) - len(matches)
        )
    return DiscoveryResult(
        DiscoveryStatus.FOUND,
        matches[0],
        unrelated_count=len(devices) - 1,
    )


class ReaderEventType(StrEnum):
    READER_CONNECTING = "reader_connecting"
    READER_CONNECTED = "reader_connected"
    READER_NOT_FOUND = "reader_not_found"
    READER_IDENTITY_AMBIGUOUS = "reader_identity_ambiguous"
    READER_OPEN_FAILED = "reader_open_failed"
    READER_READ_FAILED = "reader_read_failed"
    READER_DISCONNECTED = "reader_disconnected"
    RECONNECT_SCHEDULED = "reconnect_scheduled"
    RECONNECT_REPEATEDLY_FAILED = "reconnect_repeatedly_failed"
    READER_RECOVERED = "reader_recovered"
    READER_RECORD_RECEIVED = "reader_record_received"


@dataclass(frozen=True, slots=True)
class ReaderEvent:
    event_type: ReaderEventType
    attempt: int = 0
    backoff_seconds: float | None = None
    unrelated_count: int = 0


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    minimum: float = 1.0
    maximum: float = 30.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if self.minimum <= 0 or self.maximum < self.minimum:
            raise ValueError("invalid backoff bounds")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between zero and one")

    def delay(self, attempt: int, random_unit: float = 0.5) -> float:
        base = min(self.maximum, self.minimum * (2 ** max(0, attempt - 1)))
        spread = base * self.jitter
        return cast(float, base - spread + (2 * spread * random_unit))


class ReaderSession(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


class ReaderDisconnectedError(OSError):
    pass


class ReaderSupervisor:
    def __init__(
        self,
        *,
        identity: ReaderIdentity,
        enumerate_devices: Callable[[], Sequence[SerialDevice]],
        open_reader: Callable[[Path], ReaderSession],
        wait: Callable[[float], bool],
        emit: Callable[[ReaderEvent], None],
        backoff: BackoffPolicy | None = None,
        repeated_failure_threshold: int = 3,
        random_unit: Callable[[], float] = lambda: 0.5,
    ) -> None:
        self._identity = identity
        self._enumerate_devices = enumerate_devices
        self._open_reader = open_reader
        self._wait = wait
        self._emit = emit
        self._backoff = backoff or BackoffPolicy()
        self._repeated_failure_threshold = repeated_failure_threshold
        self._random_unit = random_unit
        self._session: ReaderSession | None = None
        self._attempt = 0
        self._had_connection = False

    @property
    def connected(self) -> bool:
        return self._session is not None

    def connect_until_ready(self, maximum_attempts: int) -> bool:
        while self._attempt < maximum_attempts:
            self._attempt += 1
            self._emit(ReaderEvent(ReaderEventType.READER_CONNECTING, self._attempt))
            discovery = discover_reader(self._enumerate_devices(), self._identity)
            failure: ReaderEventType | None = None
            if discovery.status is DiscoveryStatus.NOT_FOUND:
                failure = ReaderEventType.READER_NOT_FOUND
            elif discovery.status is DiscoveryStatus.AMBIGUOUS:
                failure = ReaderEventType.READER_IDENTITY_AMBIGUOUS
            else:
                assert discovery.device is not None
                try:
                    self._session = self._open_reader(discovery.device.path)
                except OSError:
                    failure = ReaderEventType.READER_OPEN_FAILED
                else:
                    event_type = (
                        ReaderEventType.READER_RECOVERED
                        if self._had_connection
                        else ReaderEventType.READER_CONNECTED
                    )
                    self._emit(
                        ReaderEvent(
                            event_type,
                            self._attempt,
                            unrelated_count=discovery.unrelated_count,
                        )
                    )
                    self._had_connection = True
                    self._attempt = 0
                    return True
            assert failure is not None
            self._emit(
                ReaderEvent(
                    failure,
                    self._attempt,
                    unrelated_count=discovery.unrelated_count,
                )
            )
            if self._attempt >= self._repeated_failure_threshold:
                self._emit(ReaderEvent(ReaderEventType.RECONNECT_REPEATEDLY_FAILED, self._attempt))
            delay = self._backoff.delay(self._attempt, self._random_unit())
            self._emit(ReaderEvent(ReaderEventType.RECONNECT_SCHEDULED, self._attempt, delay))
            if not self._wait(delay):
                return False
        return False

    def read_once(self) -> bytes | None:
        if self._session is None:
            return None
        try:
            data = self._session.read()
        except ReaderDisconnectedError:
            self._emit(ReaderEvent(ReaderEventType.READER_DISCONNECTED))
            self._disconnect()
            return None
        except OSError:
            self._emit(ReaderEvent(ReaderEventType.READER_READ_FAILED))
            self._disconnect()
            return None
        if data:
            self._emit(ReaderEvent(ReaderEventType.READER_RECORD_RECEIVED))
        return data

    def os_disconnected(self) -> None:
        if self._session is not None:
            self._emit(ReaderEvent(ReaderEventType.READER_DISCONNECTED))
            self._disconnect()

    def close(self) -> None:
        self._disconnect()

    def _disconnect(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
