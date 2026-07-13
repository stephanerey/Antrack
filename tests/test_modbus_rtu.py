from antrack.core.axis.modbus_rtu import append_crc, build_fc16_request, parse_fc16_response


def test_fc16_builds_write_multiple_registers_request():
    request = build_fc16_request(10, 201, [100, 25])

    assert request == append_crc(bytes.fromhex("0a 10 00 c9 00 02 04 00 64 00 19"))


def test_fc16_parses_write_multiple_registers_response():
    response = append_crc(bytes.fromhex("0a 10 00 c9 00 02"))

    assert parse_fc16_response(response, slave=10, start_register=201, quantity=2) == (201, 2)
