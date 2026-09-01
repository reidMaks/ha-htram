"""Wire protocol for the Honeywell HTRAM air monitor.

Single source of truth for both transports the integration speaks:

* the vendor BLE framing, recovered from the Android app's ``CMBLERequest``
* the MQTT telemetry payload the device publishes on ``C/<serial>``

Frames are *built* here rather than stored as literals. That is deliberate:
the previous hardcoded table carried a transcription error in the settings
command, where the decompiler rendered ``0x3F`` as ``Utf8.REPLACEMENT_BYTE``
and it was copied as ``0xEF``. The device silently rejected that frame. Those
two bytes were a placeholder in the original anyway -- the app overwrites them
with the computed checksum before sending.

Both transports use CRC-16 with polynomial 0x8005, but store it differently:
BLE big-endian over the whole frame, MQTT little-endian over one slice. That
asymmetry is the device's, not a porting mistake.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FRAME_START = 0x7B
FRAME_END = 0x7D
FRAME_TYPE = 0x41

CRC_POLY = 0x8005

# Sentinels published while the NDIR sensor warms up after a boot. They are not
# readings: publishing them puts a 65534 ppm spike in the history.
CO2_INVALID = 0xFFFE
TEMP_INVALID = 0x81
HUM_INVALID = 0xFE

TELEMETRY_MAGIC = b"DC"
TELEMETRY_LENGTH = 27
# The checksum covers this slice only, and notably excludes the timestamp:
# two packets 30 s apart with identical readings carry identical checksums,
# which is how the range was identified.
TELEMETRY_CRC_RANGE = slice(16, 25)


def _crc16_table(poly: int = CRC_POLY) -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _crc16_table()


def crc16(data: bytes) -> int:
    """CRC-16, polynomial 0x8005, init 0, MSB-first, no reflection."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc


# ---------------------------------------------------------------- BLE framing


def build_frame(cmd: bytes, body: bytes = b"") -> bytes:
    """Build a vendor frame: 7B 41 00 <len> <cmd> <body> <crc BE> 7D.

    The length byte counts the opcode, the body, the checksum and the closing
    delimiter -- not the four header bytes.
    """
    length = 2 + len(body) + 3
    pre_crc = bytes([FRAME_START, FRAME_TYPE, 0x00, length]) + cmd + body
    return pre_crc + crc16(pre_crc).to_bytes(2, "big") + bytes([FRAME_END])


def frame_is_valid(frame: bytes) -> bool:
    """Check delimiters and checksum of a received frame."""
    if len(frame) < 7 or frame[0] != FRAME_START or frame[-1] != FRAME_END:
        return False
    return crc16(frame[:-3]) == int.from_bytes(frame[-3:-1], "big")


def frame_opcode(frame: bytes) -> bytes | None:
    """Opcode of a received frame, or None if it is too short to have one."""
    return frame[4:6] if len(frame) >= 6 else None


def response_opcode(cmd: bytes) -> bytes:
    """The opcode the device answers a request with: request + 0x0100."""
    return ((int.from_bytes(cmd, "big") + 0x0100) & 0xFFFF).to_bytes(2, "big")


def iter_frames(buffer: bytes):
    """Yield complete frames from a notification buffer.

    Notifications can carry several frames at once -- a heartbeat ack riding
    along with a real answer is routine -- and can also split one frame across
    packets, so callers keep a buffer and feed it here.
    """
    start = 0
    while True:
        begin = buffer.find(bytes([FRAME_START]), start)
        if begin < 0 or len(buffer) < begin + 4:
            return
        # The length byte counts everything after the four header bytes, so a
        # whole frame is four plus that.
        end = begin + 4 + buffer[begin + 3]
        if end > len(buffer):
            return
        candidate = buffer[begin:end]
        if candidate and candidate[-1] == FRAME_END:
            yield candidate
        start = begin + 1


# --------------------------------------------------------------- BLE commands

CMD_REALTIME = b"\x40\x44"
CMD_HEARTBEAT = b"\x24\x01"
CMD_SOUND_GET = b"\x26\x23"
CMD_SOUND_SET = b"\x26\x43"
CMD_SETTINGS_GET = b"\x40\x43"
CMD_SETTINGS_SET = b"\x42\x43"
CMD_TEMP_UNIT_GET = b"\x20\x6E"
CMD_TEMP_UNIT_SET = b"\x22\x32"
CMD_SERIAL = b"\x20\x20"
CMD_SKU = b"\x20\x21"
CMD_FIRMWARE = b"\x20\x23"
CMD_LINK_STATUS = b"\x74\x00"
CMD_RADIO_MODE = b"\x74\x58"
CMD_TIME_SYNC = b"\x22\x42"
CMD_WIFI_CREDENTIALS = b"\x74\x60"
CMD_CLOUD_CONFIG = b"\x20\xB0"


def realtime() -> bytes:
    """Request the current CO2, temperature, humidity and battery."""
    return build_frame(CMD_REALTIME, b"\x02\x00")


def heartbeat() -> bytes:
    """Keepalive. The device drops idle links without it."""
    return build_frame(CMD_HEARTBEAT, b"\x01")


def sound_status() -> bytes:
    """Request the alarm buzzer state."""
    return build_frame(CMD_SOUND_GET, b"\x01\x00")


def set_sound(enabled: bool) -> bytes:
    """Turn the alarm buzzer on or off."""
    return build_frame(CMD_SOUND_SET, b"\x01\x00\x00" + bytes([1 if enabled else 0]))


def settings() -> bytes:
    """Request the alarm thresholds and the screen-off timer."""
    return build_frame(CMD_SETTINGS_GET, b"\x04\x00\x60\x06")


def set_thresholds(low: int, high: int, screen_off: int) -> bytes:
    """Set the low and high CO2 alarm thresholds and the screen-off timer."""
    return build_frame(
        CMD_SETTINGS_SET,
        b"\x04\x00\x40\x06" + struct.pack(">HHH", low, high, screen_off),
    )


def set_screen_off(minutes: int) -> bytes:
    """Set the screen-off timer alone, using the magic the app sends for it."""
    return build_frame(CMD_SETTINGS_SET, b"\x04\x00\x20\x00" + struct.pack(">H", minutes))


def temperature_unit() -> bytes:
    """Request the display's temperature unit."""
    return build_frame(CMD_TEMP_UNIT_GET, b"\x02\x06")


def set_temperature_unit(celsius: bool) -> bytes:
    """Switch the display between Celsius and Fahrenheit."""
    return build_frame(CMD_TEMP_UNIT_SET, b"\x02\x06" + bytes([0 if celsius else 1]))


def serial_number() -> bytes:
    """Request the serial number."""
    return build_frame(CMD_SERIAL, b"\x03")


def sku() -> bytes:
    """Request the SKU."""
    return build_frame(CMD_SKU, b"\x01")


def firmware_version() -> bytes:
    """Request the firmware version."""
    return build_frame(CMD_FIRMWARE, b"\x01")


def link_status() -> bytes:
    """Request the WiFi link status."""
    return build_frame(CMD_LINK_STATUS, b"\x02")


def set_radio_mode(wifi: bool) -> bytes:
    """Switch the radio between BLE and WiFi mode.

    The app calls the WiFi side ``changeBleMode(false)``. It is a precondition
    for provisioning: sent afterwards, or not at all, the credentials do not
    take effect.
    """
    return build_frame(CMD_RADIO_MODE, b"\x01\x01" + bytes([1 if wifi else 0]))


def sync_time(dt) -> bytes:
    """Set the device clock. Body is 01 YY MM DD HH MM SS as plain integers."""
    return build_frame(
        CMD_TIME_SYNC,
        bytes([1, dt.year % 100, dt.month, dt.day, dt.hour, dt.minute, dt.second]),
    )


def wifi_credentials(ssid: str, password: str) -> bytes:
    """Provision the WiFi network.

    Body layout, 154 bytes:
        01              flag
        00 x 22         reserved
        <len>           password length
        <password>      zero-padded to 64
        <ssid>          zero-padded to 33
        00 x 33         reserved
    """
    pw = password.encode()
    ss = ssid.encode()
    if len(pw) > 64:
        raise ValueError("password longer than 64 bytes")
    if len(ss) > 33:
        raise ValueError("SSID longer than 33 bytes")

    body = bytearray(b"\x01" + bytes(22))
    body.append(len(pw))
    body += pw.ljust(64, b"\x00")
    body += ss.ljust(33, b"\x00")
    body += bytes(33)
    return build_frame(CMD_WIFI_CREDENTIALS, bytes(body))


def cloud_config(aes_key: bytes, aes_iv: str, mqtt_url: str) -> bytes:
    """Provision the MQTT endpoint and crypto material.

    The key arrives Base64-decoded while the IV goes as the raw ASCII of the
    string -- that asymmetry is in the original app, not a porting mistake.

    The URL needs a six-character scheme prefix such as ``tcp://``. The device
    strips six characters without reading them, so a bare host loses its first
    six. A hostname works as well as an address; ESP-AT resolves it.
    """
    url = mqtt_url.encode()
    iv = aes_iv.encode()
    body = (
        b"\x01"
        + bytes([len(aes_key)])
        + aes_key
        + bytes([len(iv)])
        + iv
        + bytes([len(url)])
        + url
    )
    return build_frame(CMD_CLOUD_CONFIG, body)


# ------------------------------------------------------------ BLE responses


@dataclass(frozen=True)
class Realtime:
    """A 0x4144 answer."""

    co2: int | None
    temperature: int | None
    humidity: int | None
    battery: int
    charging: bool


def parse_realtime(frame: bytes) -> Realtime | None:
    """Decode a 0x4144 answer, or None if it is malformed."""
    if len(frame) < 13:
        return None
    co2 = int.from_bytes(frame[7:9], "big")
    temp = frame[9] - 256 if frame[9] > 128 else frame[9]
    humidity = frame[10]
    return Realtime(
        co2=None if co2 == CO2_INVALID else co2,
        temperature=None if frame[9] == TEMP_INVALID else temp,
        humidity=None if humidity == HUM_INVALID else humidity,
        battery=min(frame[11] * 25, 100),
        charging=frame[12] == 1,
    )


@dataclass(frozen=True)
class Settings:
    """A 0x4143 answer: alarm thresholds and screen-off timer."""

    alarm_low: int
    alarm_high: int
    screen_off: int


def parse_settings(frame: bytes) -> Settings | None:
    """Decode a 0x4143 answer, or None if it is malformed."""
    if len(frame) < 13:
        return None
    low, high, screen_off = struct.unpack(">HHH", frame[7:13])
    return Settings(alarm_low=low, alarm_high=high, screen_off=screen_off)


def parse_sound(frame: bytes) -> bool | None:
    """Decode a 0x2723 answer. True means the buzzer is enabled."""
    if len(frame) < 10:
        return None
    return frame[9] != 0


def parse_temperature_unit(frame: bytes) -> bool | None:
    """Decode a 0x216E answer. True means Celsius."""
    if len(frame) < 10:
        return None
    return frame[9] == 0


def parse_sku(frame: bytes) -> str | None:
    """Decode a 0x2121 answer.

    The app does ``Integer.parseInt(hex(bytes[7:9]), 16)``, so the wire value
    0x0651 becomes "1617". That is not a byte-order quirk -- the hex digits are
    literally the SKU.
    """
    if len(frame) < 9:
        return None
    return str(int(frame[7:9].hex(), 16))


def parse_firmware(frame: bytes) -> str | None:
    """Decode a 0x2123 answer: six ASCII bytes at [7:13]."""
    if len(frame) < 13:
        return None
    text = frame[7:13].decode("ascii", "ignore").strip("\x00").strip()
    return text or None


def parse_link_status(frame: bytes) -> int | None:
    """Decode a 0x7500 answer.

    Byte [7]: 2 or 3 means joined, 4 means tried and failed, 0 means the device
    has not attempted to join. Note that SKU 1617 reports 0 even when it is
    demonstrably on the network, so this cannot be used to verify provisioning.
    """
    if len(frame) < 8:
        return None
    return frame[7]


# ------------------------------------------------------------ MQTT telemetry


@dataclass(frozen=True)
class Telemetry:
    """A decoded 27-byte payload from ``C/<serial>``."""

    timestamp: int
    co2: int | None
    temperature: int | None
    humidity: int | None


def decode_telemetry(payload: bytes) -> Telemetry | None:
    """Decode a telemetry payload, or None if it is not one or is corrupt.

    Layout::

        [0:2]   magic "DC"
        [2:6]   constant 00 02 00 01
        [6:10]  Unix timestamp, little-endian, UTC
        [10:16] constant
        [16:20] varies as the reading settles; purpose unknown
        [20:22] CO2 ppm, little-endian
        [22]    padding
        [23]    temperature, degrees C, signed
        [24]    humidity, percent
        [25:27] CRC-16 over [16:25], little-endian
    """
    if len(payload) != TELEMETRY_LENGTH or not payload.startswith(TELEMETRY_MAGIC):
        return None
    if crc16(payload[TELEMETRY_CRC_RANGE]) != int.from_bytes(payload[25:27], "little"):
        return None

    co2 = int.from_bytes(payload[20:22], "little")
    raw_temp = payload[23]
    humidity = payload[24]
    return Telemetry(
        timestamp=int.from_bytes(payload[6:10], "little"),
        co2=None if co2 == CO2_INVALID else co2,
        temperature=None if raw_temp == TEMP_INVALID else (raw_temp - 256 if raw_temp > 128 else raw_temp),
        humidity=None if humidity == HUM_INVALID else humidity,
    )
