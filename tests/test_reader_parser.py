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

# Literal manufacturer-contract fixtures. They deliberately do not use build_record().
VALID_0102030405 = b"\x02010203040501\r\n\x03"
VALID_1112131415 = b"\x02111213141511\r\n\x03"


@pytest.mark.unit
def test_valid_documented_literal_record() -> None:
    assert parse_reader_record(VALID_0102030405) == ParsedRecord("0102030405")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (b"", "invalid_length"),
        (b"\x02010203040501\r\n", "invalid_length"),
        (b"X010203040501\r\n\x03", "invalid_framing"),
        (b"\x02010203040501\r\nX", "invalid_framing"),
        (b"\x02010203040501X\n\x03", "invalid_terminator"),
        (b"\x02010203040501\rX\x03", "invalid_terminator"),
        (b"\x02\xff10203040501\r\n\x03", "invalid_encoding"),
        (b"\x02010203040500\r\n\x03", "checksum_mismatch"),
        (b"\x0201020304050\r\n\x03", "invalid_length"),
        (b"\x020102030405010\r\n\x03", "invalid_length"),
        (b"\x02GG0203040501\r\n\x03", "invalid_identifier"),
        (b"\x020102030405GG\r\n\x03", "invalid_checksum_encoding"),
    ],
)
def test_literal_malformed_records_are_strictly_rejected(record: bytes, reason: str) -> None:
    with pytest.raises(ReaderRecordError, match=reason):
        parse_reader_record(record)


@pytest.mark.unit
def test_literal_partial_and_multiple_records() -> None:
    stream = ReaderRecordStream()
    assert stream.feed(VALID_0102030405[:7]) == []
    assert stream.feed(VALID_0102030405[7:] + VALID_1112131415) == [
        ParsedRecord("0102030405"),
        ParsedRecord("1112131415"),
    ]


@pytest.mark.unit
def test_extra_bytes_after_etx_are_separately_malformed() -> None:
    assert ReaderRecordStream().feed(VALID_0102030405 + b"extra") == [
        ParsedRecord("0102030405"),
        MalformedRecord("invalid_framing"),
    ]


@pytest.mark.unit
def test_stream_reports_excessive_length_without_raw_data() -> None:
    assert ReaderRecordStream(maximum_buffer=16).feed(b"\x02" + b"A" * 20) == [
        MalformedRecord("excessive_length")
    ]


@pytest.mark.unit
def test_simulation_builder_matches_independent_literal() -> None:
    assert build_record("0102030405") == VALID_0102030405
