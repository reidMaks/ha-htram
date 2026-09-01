"""Tests for the HTRAM wire protocol.

Two independent ground truths are used, so a change that breaks either is
caught:

* the byte literals in the Android app's ``CMBLERequest``, for BLE framing
* 465 telemetry payloads captured from the device, for the MQTT format

Note the deliberate exception in ``KNOWN_FRAMES``: the settings command uses
the checksum the app *computes*, not the placeholder bytes in its array
literal, which the app overwrites before sending.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "htram"))

import protocol as p  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


# Frames as the Android app sends them. Each entry is (builder call, expected).
KNOWN_FRAMES = [
    ("realtime", p.realtime(), "7b41000740440200fc3e7d"),
    ("heartbeat", p.heartbeat(), "7b41000624010178227d"),
    ("sound_status", p.sound_status(), "7b4100072623010009c07d"),
    ("sound off", p.set_sound(False), "7b410009264301000000ab637d"),
    ("sound on", p.set_sound(True), "7b4100092643010000012b667d"),
    ("temperature_unit", p.temperature_unit(), "7b410007206e02067e307d"),
    ("unit celsius", p.set_temperature_unit(True), "7b4100082232020600a9e37d"),
    ("unit fahrenheit", p.set_temperature_unit(False), "7b410008223202060129e67d"),
    ("serial_number", p.serial_number(), "7b410006202003be7e7d"),
    ("sku", p.sku(), "7b410006202101b8727d"),
    ("firmware_version", p.firmware_version(), "7b41000620230134717d"),
    ("link_status", p.link_status(), "7b410006740002fa6b7d"),
    ("radio ble", p.set_radio_mode(False), "7b4100087458010100ab5d7d"),
    ("radio wifi", p.set_radio_mode(True), "7b41000874580101012b587d"),
    # The app's literal here is 7b410009404304006006 3f17 7d, but 3f17 is a
    # placeholder it overwrites with the computed checksum. bf11 is that value.
    ("settings", p.settings(), "7b410009404304006006bf117d"),
]


@pytest.mark.parametrize("name,built,expected", KNOWN_FRAMES, ids=[f[0] for f in KNOWN_FRAMES])
def test_frame_matches_app(name, built, expected):
    assert built.hex() == expected


@pytest.mark.parametrize("name,built,expected", KNOWN_FRAMES, ids=[f[0] for f in KNOWN_FRAMES])
def test_frame_self_validates(name, built, expected):
    assert p.frame_is_valid(built)


def test_wrong_polynomial_would_fail():
    """Guard against a regression to CRC-16/CCITT.

    The integration shipped 0x1021 for a while. It validates none of the app's
    frames, so pinning that here documents why the polynomial is not a matter
    of taste.
    """
    crc = 0
    for byte in bytes.fromhex("7b41000740440200"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    assert crc != 0xFC3E


def test_response_opcode():
    assert p.response_opcode(p.CMD_REALTIME) == b"\x41\x44"
    assert p.response_opcode(p.CMD_WIFI_CREDENTIALS) == b"\x75\x60"
    assert p.response_opcode(p.CMD_SKU) == b"\x21\x21"


def test_frame_rejects_corruption():
    frame = bytearray(p.realtime())
    frame[6] ^= 0xFF
    assert not p.frame_is_valid(bytes(frame))


def test_iter_frames_splits_a_concatenated_notification():
    """A heartbeat ack riding along with a real answer must not hide it."""
    ack = bytes.fromhex("7b410006250101f8357d")
    answer = bytes.fromhex("7b410007750002007d647d")
    assert list(p.iter_frames(ack + answer)) == [ack, answer]


def test_iter_frames_ignores_a_truncated_tail():
    whole = bytes.fromhex("7b410006250101f8357d")
    assert list(p.iter_frames(whole + whole[:4])) == [whole]


def test_wifi_credentials_layout():
    frame = p.wifi_credentials("TP-Link_B55B", "3151831518")
    assert len(frame) == 163
    assert frame[3] == 0x9F
    assert p.frame_is_valid(frame)
    body = frame[6:-3]
    assert body[0] == 0x01
    assert body[23] == len("3151831518")
    assert body[24:34] == b"3151831518"
    assert body[88:100] == b"TP-Link_B55B"


def test_wifi_credentials_rejects_oversized_input():
    with pytest.raises(ValueError):
        p.wifi_credentials("x" * 34, "pw")
    with pytest.raises(ValueError):
        p.wifi_credentials("ssid", "y" * 65)


def test_cloud_config_carries_url_verbatim():
    frame = p.cloud_config(b"0123456789abcdef", "0123456789abcdef", "tcp://mqtt.example")
    assert p.frame_is_valid(frame)
    assert b"tcp://mqtt.example" in frame


def test_sync_time_body():
    frame = p.sync_time(datetime(2026, 9, 1, 22, 15, 30))
    assert p.frame_is_valid(frame)
    assert frame[6:13] == bytes([1, 26, 9, 1, 22, 15, 30])


def test_parse_realtime():
    frame = p.build_frame(b"\x41\x44", bytes([0x02, 0x02, 0x26, 23, 58, 4, 1]))
    reading = p.parse_realtime(frame)
    assert reading.co2 == 550
    assert reading.temperature == 23
    assert reading.humidity == 58
    assert reading.battery == 100
    assert reading.charging is True


def test_parse_realtime_drops_warmup_sentinels():
    frame = p.build_frame(b"\x41\x44", bytes([0x02, 0xFF, 0xFE, 0x81, 0xFE, 0, 0]))
    reading = p.parse_realtime(frame)
    assert reading.co2 is None
    assert reading.temperature is None
    assert reading.humidity is None


def test_parse_realtime_handles_subzero():
    frame = p.build_frame(b"\x41\x44", bytes([0x02, 0x01, 0x90, 251, 40, 2, 0]))
    assert p.parse_realtime(frame).temperature == -5


def test_parse_settings():
    frame = p.build_frame(b"\x41\x43", bytes([0x04]) + b"\x04\xd2\x05\xdc\x00\x1e")
    settings = p.parse_settings(frame)
    assert (settings.alarm_low, settings.alarm_high, settings.screen_off) == (1234, 1500, 30)


def test_parse_sku_reads_hex_digits_as_decimal():
    assert p.parse_sku(p.build_frame(b"\x21\x21", bytes([0x01, 0x06, 0x51]))) == "1617"
    assert p.parse_sku(p.build_frame(b"\x21\x21", bytes([0x01, 0x06, 0x53]))) == "1619"


def test_parse_firmware():
    frame = p.build_frame(b"\x21\x23", bytes([0x01]) + b"V1.00 ")
    assert p.parse_firmware(frame) == "V1.00"


def test_parsers_tolerate_short_frames():
    assert p.parse_realtime(b"\x7b\x41") is None
    assert p.parse_settings(b"\x7b\x41") is None
    assert p.parse_sound(b"\x7b\x41") is None
    assert p.parse_link_status(b"\x7b\x41") is None


# ------------------------------------------------------------ MQTT telemetry


def _captured_frames() -> list[bytes]:
    text = (FIXTURES / "telemetry.txt").read_text()
    return [bytes.fromhex(line) for line in text.split() if line]


def test_fixture_is_not_empty():
    assert len(_captured_frames()) > 400


def test_every_captured_frame_decodes():
    """All 465 captured payloads must pass the checksum and decode.

    This is the regression guard for the checksum range and byte order: the
    range excludes the timestamp and the value is stored little-endian, the
    opposite of the BLE framing. Getting either wrong drops the count to
    roughly one.
    """
    frames = _captured_frames()
    decoded = [p.decode_telemetry(f) for f in frames]
    assert all(d is not None for d in decoded)


def test_captured_readings_are_plausible():
    for frame in _captured_frames():
        reading = p.decode_telemetry(frame)
        if reading.co2 is not None:
            assert 300 <= reading.co2 <= 10000
        if reading.temperature is not None:
            assert -20 <= reading.temperature <= 60
        if reading.humidity is not None:
            assert 0 <= reading.humidity <= 100


def test_decode_telemetry_known_frame():
    reading = p.decode_telemetry(
        bytes.fromhex("444300020001c818976a5106000c000001042d108a0200173a150c")
    )
    assert reading.co2 == 650
    assert reading.temperature == 23
    assert reading.humidity == 58


def test_decode_telemetry_rejects_corruption():
    frame = bytearray.fromhex("444300020001c818976a5106000c000001042d108a0200173a150c")
    frame[20] ^= 0xFF
    assert p.decode_telemetry(bytes(frame)) is None


def test_decode_telemetry_ignores_timestamp_in_checksum():
    """Two payloads differing only in the timestamp share a checksum.

    This is the property that identified the covered range, so it is worth
    pinning: a change that widens the range breaks it.
    """
    a = bytearray.fromhex("444300020001c818976a5106000c000001042d108a0200173a150c")
    b = bytearray(a)
    b[6] ^= 0x5A
    assert p.decode_telemetry(bytes(a)) is not None
    assert p.decode_telemetry(bytes(b)) is not None
    assert a[25:27] == b[25:27]


def test_decode_telemetry_rejects_foreign_payloads():
    assert p.decode_telemetry(b"") is None
    assert p.decode_telemetry(b"not a telemetry payload!!!!") is None
    assert p.decode_telemetry(bytes(27)) is None


def test_iter_frames_survives_a_split_frame():
    """A frame arriving in two notifications must still be seen.

    The device answers with more bytes than a single BLE notification carries,
    so the buffer has to be accumulated rather than matched packet by packet.
    """
    whole = bytes.fromhex("7b410007750002007d647d")
    first, second = whole[:6], whole[6:]
    assert list(p.iter_frames(first)) == []
    assert list(p.iter_frames(first + second)) == [whole]


def test_iter_frames_finds_a_frame_after_leading_noise():
    whole = bytes.fromhex("7b410006250101f8357d")
    assert list(p.iter_frames(b"\x00\xff" + whole)) == [whole]
