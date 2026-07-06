import struct

import pytest

from antrack.core.axis.axis_protocol import AxisCommand, pack_axis_request, parse_axis_response


def test_axis_command_values_match_legacy_protocol():
    assert AxisCommand.MOVE_CW.value == 1
    assert AxisCommand.STOP_AZ.value == 5
    assert AxisCommand.SPEED_AZ.value == 7
    assert AxisCommand.QUERY_AXIS_SERVER_VER.value == 50
    assert AxisCommand.CLOCK.value == 200


def test_pack_axis_request_matches_current_wire_format():
    assert pack_axis_request(AxisCommand.MOVE_CW, 0) == struct.pack("B3xH2x", 1, 0)
    assert pack_axis_request(AxisCommand.SPEED_AZ, 300) == struct.pack("B3xH2x", 7, 300)


def test_pack_axis_request_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        pack_axis_request(AxisCommand.SPEED_AZ, -1)
    with pytest.raises(ValueError):
        pack_axis_request(AxisCommand.SPEED_AZ, 70000)


def test_parse_axis_response_decodes_command_and_signed_payload():
    frame = bytes.fromhex("14000000d6ffffff")
    command, value = parse_axis_response(frame)
    assert command is AxisCommand.QUERY_AZ
    assert value == -42


def test_parse_axis_response_rejects_invalid_length():
    with pytest.raises(ValueError):
        parse_axis_response(b"\x14\x00")
