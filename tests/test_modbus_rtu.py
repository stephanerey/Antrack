import pytest

from antrack.core.axis.modbus_rtu import (
    ModbusCrcError,
    ModbusFrameError,
    append_crc,
    build_fc03_request,
    build_fc06_request,
    crc16,
    parse_fc03_response,
    parse_fc06_response,
)


def test_crc16_matches_expected_legacy_values():
    assert crc16(bytes.fromhex("0a0300670001")) == 0xAE34
    assert crc16(bytes.fromhex("0a0600c90064")) == 0x6459


def test_build_fc03_request_matches_expected_bytes():
    assert build_fc03_request(10, 103, 1).hex() == "0a030067000134ae"


def test_parse_fc03_response_extracts_register_value():
    response = append_crc(bytes.fromhex("0a03021234"))
    assert parse_fc03_response(response, slave=10, length=1) == [0x1234]


def test_parse_fc03_response_rejects_crc_mismatch():
    bad = bytes.fromhex("0a030212340000")
    with pytest.raises(ModbusCrcError):
        parse_fc03_response(bad, slave=10, length=1)


def test_build_fc06_request_matches_expected_bytes():
    assert build_fc06_request(10, 201, 100).hex() == "0a0600c900645964"


def test_parse_fc06_accepts_standard_echo():
    frame = build_fc06_request(10, 201, 100)
    assert parse_fc06_response(frame, slave=10, register=201, value=100) == (201, 100)


def test_parse_fc06_optionally_accepts_legacy_short_response():
    frame = build_fc06_request(10, 201, 100)
    short_frame = frame[:7]
    assert parse_fc06_response(
        short_frame,
        slave=10,
        register=201,
        value=100,
        accept_legacy_short_response=True,
    ) == (201, 100)
    with pytest.raises(ModbusFrameError):
        parse_fc06_response(
            short_frame,
            slave=10,
            register=201,
            value=100,
            accept_legacy_short_response=False,
        )
