#!/usr/bin/env python3
"""Decode HTRAM telemetry payloads out of a mqtt_wss.py log.

    .venv/bin/python tools/decode_telemetry.py mqtt.log
    .venv/bin/python tools/decode_telemetry.py mqtt.log --follow

Payload is 27 bytes on topic C/<serial>, published every 30 s, unencrypted:

    [0:2]   magic "DC"
    [2:6]   constant 00 02 00 01
    [6:10]  Unix timestamp, little-endian, UTC
    [10:20] mostly constant, four varying bytes at the end
    [16:25] the measurement block, and exactly what the CRC covers
    [20:22] CO2 ppm, little-endian
    [22]    00
    [23]    temperature, degrees C
    [24]    humidity, percent
    [25:27] CRC-16 over [16:25]: poly 0x8005, init 0, MSB-first,
            stored LITTLE-endian, unlike the BLE framing which stores it big-
            endian. The same polynomial, and it deliberately excludes the
            timestamp -- two packets 30 s apart with identical readings carry
            identical tails, which is how the covered range was found.

Sentinel values appear while the NDIR sensor warms up after a boot: CO2
0xFFFE, temperature 0x81, humidity 0xFE. They are not readings and must be
discarded rather than published.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import time
from datetime import datetime, timezone


def crc16(data: bytes, poly: int = 0x8005) -> int:
    """CRC-16, init 0, MSB-first -- identical to the BLE protocol's."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

HEX_RE = re.compile(r"hex\s+([0-9a-f]{20,})")

CO2_INVALID = 0xFFFE
TEMP_INVALID = 0x81
HUM_INVALID = 0xFE


class Reading:
    __slots__ = ("timestamp", "co2", "temperature", "humidity", "mid", "tail",
                 "raw", "crc_ok")

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.timestamp = struct.unpack("<I", raw[6:10])[0]
        self.mid = raw[16:20]
        self.co2 = struct.unpack("<H", raw[20:22])[0]
        self.temperature = raw[23]
        self.humidity = raw[24]
        self.tail = raw[25:27]
        self.crc_ok = crc16(raw[16:25]) == int.from_bytes(raw[25:27], "little")

    @property
    def valid(self) -> bool:
        return not (self.co2 == CO2_INVALID
                    or self.temperature == TEMP_INVALID
                    or self.humidity == HUM_INVALID)

    def signed_temp(self) -> int:
        return self.temperature - 256 if self.temperature >= 128 else self.temperature


def parse(raw: bytes) -> Reading | None:
    if len(raw) != 27 or raw[0:2] != b"DC":
        return None
    return Reading(raw)


def render(r: Reading, prev: Reading | None) -> str:
    when = datetime.fromtimestamp(r.timestamp, timezone.utc).astimezone()
    gap = f"{r.timestamp - prev.timestamp:>3}s" if prev else "  -"
    if not r.valid:
        return (f"{when:%H:%M:%S}  {gap}   "
                f"CO2 ---- ppm   T --- C   RH --- %   "
                f"mid {r.mid.hex()}  crc {'ok' if r.crc_ok else 'BAD'}   <warm-up, discard>")
    return (f"{when:%H:%M:%S}  {gap}   "
            f"CO2 {r.co2:>4} ppm   T {r.signed_temp():>3} C   RH {r.humidity:>3} %   "
            f"mid {r.mid.hex()}  crc {'ok' if r.crc_ok else 'BAD'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--follow", "-f", action="store_true")
    args = ap.parse_args()

    seen: set[bytes] = set()
    readings: list[Reading] = []
    prev: Reading | None = None
    offset = 0

    def consume() -> None:
        nonlocal offset, prev
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for line in fh:
                match = HEX_RE.search(line)
                if not match:
                    continue
                raw = bytes.fromhex(match.group(1))
                if raw in seen:
                    continue
                seen.add(raw)
                r = parse(raw)
                if r is None:
                    print(f"  unparsed {raw.hex()}")
                    continue
                readings.append(r)
                print(render(r, prev), flush=True)
                prev = r
            offset = fh.tell()

    print("time      gap    readings")
    print("-" * 96)
    consume()

    if args.follow:
        try:
            while True:
                time.sleep(2)
                consume()
        except KeyboardInterrupt:
            pass

    good = [r for r in readings if r.valid]
    print("-" * 96)
    print(f"{len(readings)} packets, {len(good)} with real readings")
    if good:
        co2s = [r.co2 for r in good]
        print(f"  CO2  min {min(co2s)}  max {max(co2s)}  last {co2s[-1]} ppm")
        print(f"  temp {good[-1].signed_temp()} C   humidity {good[-1].humidity} %")
    if len(readings) > 1:
        gaps = [b.timestamp - a.timestamp for a, b in zip(readings, readings[1:])]
        print(f"  intervals: {sorted(set(gaps))} s")
    bad = [r for r in readings if not r.crc_ok]
    print(f"  CRC: {len(readings) - len(bad)}/{len(readings)} valid"
          + (f", {len(bad)} BAD" if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
