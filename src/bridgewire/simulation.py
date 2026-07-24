from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bridgewire.audit import InMemoryAuditSink, InMemoryNotificationQueue
from bridgewire.authorization import AuthorizationFile, AuthorizationStore
from bridgewire.clock import ManualClock
from bridgewire.controller import AccessController
from bridgewire.escalation import EscalationTracker
from bridgewire.gpio import SimulatedRelay
from bridgewire.reader import (
    BackoffPolicy,
    MalformedRecord,
    ReaderDisconnectedError,
    ReaderEvent,
    ReaderIdentity,
    ReaderSession,
    ReaderSupervisor,
    SerialDevice,
    parse_reader_record,
)


@dataclass(slots=True)
class SimulatedReaderSession(ReaderSession):
    records: list[bytes | Exception]
    closed: bool = False

    def read(self) -> bytes:
        if not self.records:
            return b""
        result = self.records.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


def build_record(credential: str) -> bytes:
    checksum = 0
    for offset in range(0, len(credential), 2):
        checksum ^= int(credential[offset : offset + 2], 16)
    return b"\x02" + credential.encode("ascii") + f"{checksum:02X}".encode() + b"\r\n\x03"


def run_vertical_slice(schema_path: Path, authorization_path: Path) -> list[dict[str, object]]:
    clock = ManualClock()
    audit = InMemoryAuditSink()
    notifications = InMemoryNotificationQueue()
    authorization = AuthorizationStore(AuthorizationFile(_load_schema(schema_path)))
    authorization.reload(authorization_path)
    relay = SimulatedRelay(clock)
    controller = AccessController(
        authorization=authorization,
        relay=relay,
        audit=audit,
        notifications=notifications,
        clock=clock,
        escalation=EscalationTracker(),
    )
    controller.start()
    authorized = parse_reader_record(build_record("0102030405"))
    controller.process(authorized)
    clock.advance(1)
    controller.process(authorized)
    for credential in ("1112131415", "A1A2A3A4A5"):
        controller.process(parse_reader_record(build_record(credential)))
    controller.process(MalformedRecord("invalid_framing"))
    clock.advance(2)
    controller.tick()
    controller.process(parse_reader_record(build_record("B1B2B3B4B5")))
    controller.process(MalformedRecord("checksum_mismatch"))

    reader_events: list[ReaderEvent] = []
    devices: list[SerialDevice] = [
        SerialDevice(
            path=Path("/dev/ttySIM1"),
            by_id_path=Path("/dev/serial/by-id/bridgewire-simulated-reader"),
        )
    ]
    sessions = [
        SimulatedReaderSession([ReaderDisconnectedError("simulated disconnect")]),
        SimulatedReaderSession([]),
    ]
    supervisor = ReaderSupervisor(
        identity=ReaderIdentity(by_id_path=Path("/dev/serial/by-id/bridgewire-simulated-reader")),
        enumerate_devices=lambda: devices,
        open_reader=lambda _path: sessions.pop(0),
        wait=lambda _seconds: True,
        emit=reader_events.append,
        backoff=BackoffPolicy(1, 4, 0),
    )
    supervisor.connect_until_ready(1)
    supervisor.read_once()
    supervisor.connect_until_ready(1)
    controller.shutdown()
    output = [event.as_dict() for event in audit.events]
    output.extend(
        {
            "event_type": event.event_type.value,
            "attempt": event.attempt,
            "backoff_seconds": event.backoff_seconds,
        }
        for event in reader_events
    )
    return output


def _load_schema(path: Path) -> dict[str, object]:
    import json
    from typing import cast

    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
