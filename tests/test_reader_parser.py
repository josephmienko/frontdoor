from __future__ import annotations

import pytest

from bridgewire.reader import (
    MalformedRecord,
    ParsedRecord,
    ReaderRecordError,
    ReaderRecordStream,
    parse_reader_record,
)
from bridgewire.simulation import build_record


@pytest.mark.unit
def test_valid_documented_record() -> None:
    assert parse_reader_record(build_record("0102030405")) == ParsedRecord("0102030405")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (b"", "invalid_length"),
        (b"\x02" + b"0" * 13 + b"\x03", "invalid_length"),
        (b"\x01" + build_record("0102030405")[1:], "invalid_framing"),
        (build_record("0102030405")[:-3] + b"\n\n\x03", "invalid_terminator"),
        (b"\x02" + b"\xff" + build_record("0102030405")[2:], "invalid_encoding"),
        (b"\x02GG02030405" + build_record("0102030405")[11:], "invalid_identifier"),
        (build_record("0102030405")[:11] + b"GG\r\n\x03", "invalid_checksum_encoding"),
        (build_record("0102030405")[:11] + b"00\r\n\x03", "checksum_mismatch"),
    ],
)
def test_malformed_records_are_strictly_rejected(record: bytes, reason: str) -> None:
    with pytest.raises(ReaderRecordError, match=reason):
        parse_reader_record(record)


@pytest.mark.unit
def test_partial_and_multiple_records() -> None:
    stream = ReaderRecordStream()
    first = build_record("0102030405")
    second = build_record("1112131415")
    assert stream.feed(first[:7]) == []
    assert stream.feed(first[7:] + second) == [
        ParsedRecord("0102030405"),
        ParsedRecord("1112131415"),
    ]


@pytest.mark.unit
def test_stream_reports_invalid_framing_and_excessive_length_without_raw_data() -> None:
    stream = ReaderRecordStream(maximum_buffer=16)
    assert stream.feed(b"garbage") == [MalformedRecord("invalid_framing")]
    assert stream.feed(b"\x02" + b"A" * 20) == [MalformedRecord("excessive_length")]
