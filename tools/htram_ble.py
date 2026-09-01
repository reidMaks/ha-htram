"""Standalone HTRAM BLE console tool: protocol codec + client.

Pure bleak, no Home Assistant. Frame format (both directions):

    7B 41 00 <len> <cmd BE u16> <body...> <crc16 BE u16> 7D

`len` = 2 (cmd) + len(body) + 3 (crc16 + 0x7D), so total frame = 4 + len.
CRC-16 is poly 0x8005, init 0, MSB-first, over every byte before the CRC field.

The 0x8005 polynomial is verified against the hardcoded packets in
custom_components/htram/const.py (8/9 match; see `selftest`). Note that
custom_components/htram/utils.py uses 0x1021, which matches 0/9 -- any packet
that utils.py builds from scratch carries a CRC the device will reject.

Responses use the request opcode + 0x0100 (0x4044 -> 0x4144, 0x7460 -> 0x7560).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

FRAME_START = 0x7B
FRAME_END = 0x7D

SERVICE_UUID = "fc247940-6e08-11e4-80fc-0002a5d5c51b"
WRITE_UUID = "3d115840-6e0b-11e4-b24f-0002a5d5c51b"
NOTIFY_UUID = "f833d6c0-6e0b-11e4-9136-0002a5d5c51b"

_LOGGER = logging.getLogger("htram")


def _crc16_table(poly: int = 0x8005) -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


CRC16_TABLE = _crc16_table()


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc


def build_packet(cmd_id: bytes, body: bytes = b"") -> bytes:
    """Build a full frame: 7B 41 00 <len> <cmd> <body> <crc16 BE> 7D."""
    length = 2 + len(body) + 3
    if length > 0xFF:
        raise ValueError(f"body too long for the 1-byte length field: {len(body)}")
    pre_crc = bytes([FRAME_START, 0x41, 0x00, length]) + cmd_id + body
    return pre_crc + crc16(pre_crc).to_bytes(2, "big") + bytes([FRAME_END])


def response_cmd(cmd_id: bytes) -> bytes:
    """The opcode the device answers a given request with (request + 0x0100)."""
    return ((int.from_bytes(cmd_id, "big") + 0x0100) & 0xFFFF).to_bytes(2, "big")


# --- Commands ---------------------------------------------------------------
# Opcode names follow noname122021/honeywell-htram-v1w-ble-monitor's PROTOCOL.md,
# which derives them from MicroRAECmdClass.smali in the decompiled Android app.

CMD_CHANGE_BLE_MODE = build_packet(b"\x74\x58", b"\x01\x01\x00")
CMD_GET_REALTIME = build_packet(b"\x40\x44", b"\x02\x00")
CMD_HEARTBEAT = build_packet(b"\x24\x01", b"\x01")

# Read-only probes. Safe to send: every one is a GET.
#
# Every probe carries a non-empty body. An earlier revision sent the optional
# ones with an empty body and read their silence as "opcode not implemented" --
# but 0x7461 is also silent on an empty body and answers with a 1-byte one, so
# empty-body silence says nothing about the opcode. Do not reintroduce it.
READ_PROBES: list[tuple[str, bytes, bytes]] = [
    # (label, cmd_id, body)
    ("REALTIME            ", b"\x40\x44", b"\x02\x00"),
    ("SENSOR_PARA_GET     ", b"\x40\x43", b"\x04\x00\x60\x06"),
    ("SOUND_STATUS_GET    ", b"\x26\x23", b"\x01\x00"),
    ("TEMP_UNIT_GET       ", b"\x20\x6e", b"\x02\x06"),
    ("TIME_OUT_SETTING_GET", b"\x40\xa4", b"\x01"),
    ("DC_MODE             ", b"\x40\xa3", b"\x01"),
]

# The question this tool exists to answer: does this unit's firmware implement
# the WiFi/backhaul command family at all? All GETs, so all safe to send.
WIFI_PROBES: list[tuple[str, bytes, bytes]] = [
    ("WIFI_CFG_GET (empty)", b"\x74\x61", b""),
    ("WIFI_CFG_GET (01)   ", b"\x74\x61", b"\x01"),
    ("WIFI_CFG_GET (0100) ", b"\x74\x61", b"\x01\x00"),
    ("SAFETY_NEX_WIFI_GET ", b"\x74\x65", b"\x01"),
    ("MSG_COMMAND_GET     ", b"\x74\x63", b"\x01"),
    ("NBIOT_BASIC_INFO_GET", b"\x20\xa3", b"\x01"),
    ("WIRELESS_PAN_ID_GET ", b"\x27\x08", b"\x01"),
    ("WIRELESS_CHANNEL_GET", b"\x27\x0a", b"\x01"),
]

# Opcodes that cannot plausibly be implemented, sent with the same 1-byte body
# the real commands accept. If these draw the same "<opcode+0x0100> 01" reply
# that 0x7460 and 0x7461 draw, then that reply is a generic receipt emitted for
# any well-formed frame -- and an ACK from 0x7460 proves nothing whatsoever.
# This is the control that decides how every other result here is read.
NEGATIVE_CONTROLS: list[tuple[str, bytes, bytes]] = [
    ("bogus 0x74FE (01)   ", b"\x74\xfe", b"\x01"),
    ("bogus 0x74AA (01)   ", b"\x74\xaa", b"\x01"),
    ("bogus 0x6BCD (01)   ", b"\x6b\xcd", b"\x01"),
    ("bogus 0x1234 (01)   ", b"\x12\x34", b"\x01"),
    # The receipt echoes the first body byte, so a control must use the SAME
    # body as the command it is a control for. getDeviceLinkNetStatus sends
    # 0x02 and comes back with "02 00" -- one byte more than the echo. These
    # decide whether that extra byte is a status field or just more echo.
    ("bogus 0x74FE (02)   ", b"\x74\xfe", b"\x02"),
    ("bogus 0x1234 (02)   ", b"\x12\x34", b"\x02"),
    ("real  0x7400 (02)   ", b"\x74\x00", b"\x02"),
]


def sync_time_command(dt_utc: datetime | None = None) -> bytes:
    """Time-sync command (0x2242); body is 01 YY MM DD HH MM SS, plain ints."""
    dt = dt_utc or datetime.now(timezone.utc)
    return build_packet(
        b"\x22\x42",
        bytes([0x01, dt.year % 100, dt.month, dt.day, dt.hour, dt.minute, dt.second]),
    )


def submit_ssid_command(ssid: str, password: str) -> bytes:
    """WIFI_CFG_SET (0x7460) -- provision WiFi credentials.

    Body layout, ported from CMBLERequest.submitSSID in the Android app:

        01                 flag
        00 * 22            reserved
        <len>              password length, 1 byte
        <password>         zero-padded to 64 bytes
        <ssid>             zero-padded to 33 bytes
        00 * 33            reserved

    Total body 154 bytes -> length byte 0x9f -> 163-byte frame.
    """
    pwd = password.encode("utf-8")
    ssid_b = ssid.encode("utf-8")
    if len(pwd) > 64:
        raise ValueError(f"password is {len(pwd)} bytes, max 64")
    if len(ssid_b) > 33:
        raise ValueError(f"SSID is {len(ssid_b)} bytes, max 33")

    body = (
        b"\x01"
        + b"\x00" * 22
        + bytes([len(pwd)])
        + pwd.ljust(64, b"\x00")
        + ssid_b.ljust(33, b"\x00")
        + b"\x00" * 33
    )
    return build_packet(b"\x74\x60", body)


# The reference project's README prints the init frame as this literal, but its
# length byte (0x0c) and CRC (4e08) disagree with a freshly computed frame over
# the same body -- its own air_monitor.py sends the computed form. Kept as a
# fallback for firmwares that might want the literal the app actually emits.
CMD_CHANGE_BLE_MODE_LEGACY = bytes.fromhex("7b41000c74580101004e087d")


# --- Reassembly -------------------------------------------------------------


class FrameAccumulator:
    """Reassemble complete frames from fragmented/coalesced BLE notifications."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buf.find(FRAME_START)
            if start < 0:
                self._buf.clear()
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < 4:
                break
            total = 4 + self._buf[3]
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            if frame[-1] == FRAME_END:
                frames.append(frame)
            else:
                # Bad length byte -- drop the start marker and resynchronise.
                self._buf[:0] = frame[1:]
        return frames


def frame_is_valid(frame: bytes) -> bool:
    return (
        len(frame) >= 8
        and frame[0] == FRAME_START
        and frame[-1] == FRAME_END
        and crc16(frame[:-3]) == int.from_bytes(frame[-3:-1], "big")
    )


# --- Client -----------------------------------------------------------------


@dataclass
class Exchange:
    """One request and whatever the device sent back within the timeout."""

    label: str
    request: bytes
    responses: list[bytes]

    @property
    def answered(self) -> bool:
        return bool(self.responses)


class HtramClient:
    """Thin request/response wrapper around a connected BleakClient."""

    # Heartbeat ACK (0x2401 + 0x0100). The keepalive task generates these
    # asynchronously, so they must never be mistaken for a reply to whatever
    # request happens to be in flight.
    IGNORED_CMDS = frozenset({b"\x25\x01"})

    def __init__(self, client, verbose: bool = True) -> None:
        self._client = client
        self._verbose = verbose
        self._acc = FrameAccumulator()
        self._frames: asyncio.Queue[bytes] = asyncio.Queue()
        self.all_frames: list[bytes] = []

    def _on_notify(self, _sender, data: bytearray) -> None:
        for frame in self._acc.feed(bytes(data)):
            self.all_frames.append(frame)
            ignored = frame[4:6] in self.IGNORED_CMDS
            if self._verbose:
                note = ""
                if not frame_is_valid(frame):
                    note = "  <-- BAD CRC"
                elif ignored:
                    note = "  (heartbeat ack, ignored)"
                print(f"    <- {frame.hex()}{note}")
            if not ignored:
                self._frames.put_nowait(frame)

    async def start(self) -> None:
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)

    async def acquire_mtu(self) -> int:
        """Resolve the real ATT MTU.

        The BlueZ backend reports a placeholder 23 until the MTU is acquired,
        which would force needless chunking of the 163-byte provisioning frame.
        """
        backend = getattr(self._client, "_backend", None)
        acquire = getattr(backend, "_acquire_mtu", None)
        if acquire is not None and getattr(backend, "_mtu_size", None) is None:
            try:
                await acquire()
            except Exception as err:
                _LOGGER.debug("could not acquire MTU: %s", err)
        return getattr(self._client, "mtu_size", 23)

    async def stop(self) -> None:
        try:
            await self._client.stop_notify(NOTIFY_UUID)
        except Exception:
            pass

    def _drain(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()

    async def write(self, packet: bytes, response: bool = False) -> None:
        """Write a frame, chunking it if it exceeds the negotiated ATT MTU."""
        payload_max = max(20, getattr(self._client, "mtu_size", 23) - 3)
        if response or len(packet) <= payload_max:
            # A Write Request lets ATT perform a long write, so the peripheral
            # receives one value instead of N unrelated chunks.
            await self._client.write_gatt_char(WRITE_UUID, packet, response=response)
            return
        if self._verbose:
            print(f"    (chunking {len(packet)} B into {payload_max} B writes)")
        for offset in range(0, len(packet), payload_max):
            await self._client.write_gatt_char(
                WRITE_UUID, packet[offset : offset + payload_max], response=False
            )
            await asyncio.sleep(0.05)

    async def request(
        self,
        label: str,
        cmd_id: bytes,
        body: bytes = b"",
        timeout: float = 3.0,
        settle: float = 0.4,
    ) -> Exchange:
        """Send a command and collect frames until `timeout` passes with none.

        Collects *every* frame that arrives, not just the expected opcode -- an
        error or status frame carrying a different opcode is exactly the kind of
        evidence this tool is looking for.
        """
        packet = build_packet(cmd_id, body)
        self._drain()
        if self._verbose:
            print(f"  {label}  -> {packet.hex()}")
        await self.write(packet)

        responses: list[bytes] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                frame = await asyncio.wait_for(self._frames.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            responses.append(frame)
            # Got something; give coalesced follow-up frames a moment to land.
            deadline = asyncio.get_running_loop().time() + settle
        if self._verbose and not responses:
            print("       (no response)")
        return Exchange(label.strip(), packet, responses)


def submit_ssid_variants(ssid: str, password: str) -> list[tuple[str, bytes]]:
    """Candidate 0x7460 bodies, most informative first.

    The device validates body length -- 0x7461 with an empty body is ignored
    while 0x7461 01 is answered -- so silence on 0x7460 is ambiguous between
    "no handler" and "wrong body length". These variants separate the two.

    The one-byte probe comes first: if the opcode itself is live, it should
    react to a minimal body the same way 0x7461 does, which settles the
    question without depending on any guess about the layout.
    """
    pwd = password.encode("utf-8")
    ssid_b = ssid.encode("utf-8")
    pwd64 = pwd.ljust(64, b"\x00")
    ssid33 = ssid_b.ljust(33, b"\x00")
    n = bytes([len(pwd)])

    return [
        # Is the opcode alive at all? Mirrors the 0x7461 flag-only request.
        ("flag only (1 B)          ", b"\x01"),
        # The layout ported from CMBLERequest.submitSSID.
        ("app layout (154 B)       ", b"\x01" + b"\x00" * 22 + n + pwd64 + ssid33 + b"\x00" * 33),
        # Same, minus the trailing reserved block.
        ("no trailing pad (121 B)  ", b"\x01" + b"\x00" * 22 + n + pwd64 + ssid33),
        # Same, minus both reserved blocks.
        ("no reserved (99 B)       ", b"\x01" + n + pwd64 + ssid33),
        # SSID first, the more common ordering in this protocol family.
        ("ssid first (99 B)        ", b"\x01" + ssid33 + n + pwd64),
        # Fully length-prefixed, no padding.
        ("length-prefixed (var)    ",
         b"\x01" + bytes([len(ssid_b)]) + ssid_b + n + pwd),
    ]


def submit_aes_key_command(aes_key: str, aes_iv: str, mqtt_server: str) -> bytes:
    """AES_KEY_SET (0x20B0) -- hand the device its MQTT endpoint and crypto.

    Ported from CMBLERequest.submitAESKey. Body layout:

        01                 flag
        <len> <key>        AES key, length-prefixed
        <len> <iv>         AES IV, length-prefixed
        <len> <server>     MQTT server URL, length-prefixed

    The Java takes the key as a Base64 string and decodes it to bytes, while
    the IV and the server URL are used as raw string bytes. That asymmetry is
    in the original, not a mistake here -- so a Base64 key is decoded, and
    anything that is not valid Base64 is passed through as UTF-8.

    Honeywell's cloud issued these three values at enrollment, which is why the
    app only offered WiFi setup after an account had claimed the device. With
    those servers gone, pointing the device at a self-hosted broker is the only
    way this step can be performed at all.
    """
    import base64

    try:
        key_bytes = base64.b64decode(aes_key, validate=True)
    except Exception:
        key_bytes = aes_key.encode("utf-8")
    iv_bytes = aes_iv.encode("utf-8")
    server_bytes = mqtt_server.encode("utf-8")

    for name, value, limit in (
        ("AES key", key_bytes, 255),
        ("AES IV", iv_bytes, 255),
        ("MQTT server", server_bytes, 255),
    ):
        if len(value) > limit:
            raise ValueError(f"{name} is {len(value)} bytes, max {limit}")

    body = (
        b"\x01"
        + bytes([len(key_bytes)]) + key_bytes
        + bytes([len(iv_bytes)]) + iv_bytes
        + bytes([len(server_bytes)]) + server_bytes
    )
    return build_packet(b"\x20\xb0", body)


# --- Commands recovered from the decompiled app (com.honeywell.sps.airmonitor) --
# CMBLERequest.java. These are byte-for-byte the app's own literals; every one
# validates under the 0x8005 CRC, which is what pinned the polynomial for good.

CMD_FETCH_SN = bytes.fromhex("7b410006202003be7e7d")            # 0x2020
CMD_FETCH_SKU = bytes.fromhex("7b410006202101b8727d")           # 0x2021
CMD_FETCH_FIRMWARE = bytes.fromhex("7b41000620230134717d")      # 0x2023
CMD_LINK_NET_STATUS = bytes.fromhex("7b410006740002fa6b7d")     # 0x7400

# changeBleMode(z): body is 01 + (z ? 01 00 : 01 01).
# The app calls changeBleMode(FALSE) from a runnable named setWifiModeRunnable,
# so 01 01 01 is what puts the device into WiFi mode. Every published project
# sends only the `true` form, which keeps it in BLE mode -- that omission is why
# provisioning attempts based on those projects cannot work.
CMD_SET_BLE_MODE = build_packet(b"\x74\x58", b"\x01\x01\x00")
CMD_SET_WIFI_MODE = build_packet(b"\x74\x58", b"\x01\x01\x01")

# SKU values from Constants.java.
#
# Do NOT read these as "BLE-only" vs "WiFi" -- that claim comes from a third
# party's README, and the app's own code contradicts it. WifiListPrecenter runs
# the WiFi provisioning flow for BOTH. They differ in two ways only:
#
#   * AES key transfer: MOV1 uses single-packet submitAESKey, MOV2 chunks it
#     via submitAESKeyForMVO2.
#   * Verification: after 0x7560, ConnectToWifiActivity.sendSsidFinish polls
#     getDeviceLinkNetStatus for MOV2, but for MOV1 it just drops the BLE link
#     and shows the "enrolled" screen without checking anything.
#
# So a MOV1 gets provisioned blind. That is why the app could appear to work.
SKU_MOV1 = "1617"    # encoded on the wire as 0x0651; single-packet AES key
SKU_MOV2 = "1619"    # encoded on the wire as 0x0653; chunked AES key + status poll


def parse_sku(frame: bytes) -> str | None:
    """Decode a 0x2121 response. Body bytes [7:9] read as hex digits.

    The app does Integer.parseInt(hex(bytes[7:9]), 16), so the wire value
    0x0651 becomes the decimal string "1617". That is not a byte-order quirk --
    the digits are literally the SKU.
    """
    if len(frame) < 9 or frame[4:6] != b"\x21\x21":
        return None
    return str(int(frame[7:9].hex(), 16))


def parse_firmware(frame: bytes) -> str | None:
    """Decode a 0x2123 response: six ASCII bytes at [7:13]."""
    if len(frame) < 13 or frame[4:6] != b"\x21\x23":
        return None
    return frame[7:13].decode("ascii", "replace").strip("\x00").strip()


LINK_STATUS = {
    0: "idle / not configured",
    1: "connecting",
    2: "connected",
    3: "connected",
    4: "failed",
}


def parse_link_status(frame: bytes) -> tuple[int, str] | None:
    """Decode a 0x7500 response. Byte [7]: 2 or 3 means the device is on the
    network, 4 means it tried and failed -- the app's own interpretation."""
    if len(frame) < 8 or frame[4:6] != b"\x75\x00":
        return None
    code = frame[7]
    return code, LINK_STATUS.get(code, f"unknown ({code})")
