#!/usr/bin/env python3
"""Read the HTRAM's ESP32 console over a USB-UART adapter.

The device's radio is an ESP32-WROOM-32E, which prints its boot log and every
error string on UART0 at 115200 8N1. Since the BLE protocol acknowledges any
well-formed frame regardless of whether it did anything (see README §5), the
console is the first source in this project that can say *why* something did
not happen rather than merely that nothing was observed.

Wiring, read-only -- nothing is transmitted unless you pass --interactive:

    module pin 35 (TXD0 / GPIO1)  ->  adapter RX
    module pin 1 / 15 / 38 (GND)  ->  adapter GND

Do NOT connect the adapter's 3V3 or 5V; the device powers itself.

    .venv/bin/python tools/htram_uart.py --list
    .venv/bin/python tools/htram_uart.py                       # 115200, log + highlight
    .venv/bin/python tools/htram_uart.py --scan-baud           # find the rate
    .venv/bin/python tools/htram_uart.py --raw                 # hex dump too
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial is missing:  uv add pyserial", file=sys.stderr)
    raise SystemExit(2)

# Lines worth pulling out of the noise. The MQTT and crypto patterns are the
# whole point of the exercise; the panic ones distinguish a hard crash from a
# handled failure, which BLE observation could not.
HIGHLIGHTS: list[tuple[str, str]] = [
    (r"Guru Meditation|abort\(\)|panic|Backtrace:|rst:0x|CORRUPT HEAP", "PANIC"),
    (r"E \(\d+\)|ESP_ERR|ERROR|error|failed|Failed|FAIL", "ERROR"),
    (r"mqtt|MQTT", "MQTT"),
    (r"aes|AES|cipher|crypt|decrypt|encrypt", "CRYPTO"),
    (r"wifi|WIFI|WiFi|sta:|ip:|dhcp|DHCP|got ip", "WIFI"),
    (r"bind|Bind|BIND|enroll|token|cert", "BIND"),
    (r"ssid|SSID|password", "CREDS"),
]
COMPILED = [(re.compile(pat), tag) for pat, tag in HIGHLIGHTS]

# Rates worth trying. 115200 is the ESP-IDF default; 74880 is the ROM
# bootloader's rate on a 26 MHz crystal and prints the reset reason.
BAUD_RATES = [115200, 74880, 921600, 460800, 230400, 57600, 38400, 9600]


def classify(line: str) -> str | None:
    for pattern, tag in COMPILED:
        if pattern.search(line):
            return tag
    return None


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S.%f")[:-3]


def cmd_list() -> int:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return 1
    for port in ports:
        print(f"  {port.device}")
        print(f"    {port.description}")
        if port.hwid:
            print(f"    {port.hwid}")
    return 0


def open_port(device: str, baud: int) -> "serial.Serial":
    return serial.Serial(
        device, baud, timeout=0.2,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, rtscts=False, dsrdtr=False,
    )


def printable_ratio(data: bytes) -> float:
    """Share of bytes that look like text. A wrong baud rate yields garbage."""
    if not data:
        return 0.0
    good = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return good / len(data)


def cmd_scan_baud(args) -> int:
    """Try each rate briefly and report which produces readable text."""
    print("Sampling each baud rate for readable output.")
    print("Reset the device (unplug/replug power, or double-click its button)")
    print("while this runs -- a silent line proves nothing about the rate.\n")

    results: list[tuple[int, float, int, bytes]] = []
    for baud in BAUD_RATES:
        try:
            with open_port(args.port, baud) as port:
                port.reset_input_buffer()
                deadline = time.time() + args.dwell
                buf = b""
                while time.time() < deadline:
                    buf += port.read(4096)
        except serial.SerialException as err:
            print(f"  {baud:>7}  cannot open: {err}")
            return 2
        ratio = printable_ratio(buf)
        results.append((baud, ratio, len(buf), buf))
        verdict = "text" if ratio > 0.85 and len(buf) > 8 else (
            "garbage" if buf else "silent")
        preview = buf[:60].decode("ascii", "replace").replace("\n", "\\n")
        print(f"  {baud:>7}  {len(buf):>5} B  printable {ratio:5.0%}  {verdict}"
              + (f"   {preview!r}" if buf else ""))

    good = [r for r in results if r[1] > 0.85 and r[2] > 8]
    print()
    if good:
        best = max(good, key=lambda r: r[2])
        print(f"  Use --baud {best[0]}")
        return 0
    if any(r[2] for r in results):
        print("  Data arrived but never looked like text. Check that you are on")
        print("  TXD0 (module pin 35) and that grounds are common.")
    else:
        print("  Nothing arrived at any rate. Either the line is not connected,")
        print("  the device was not reset during the sample, or this pad is not")
        print("  TXD0. Note the ESP32 only prints at boot unless the firmware")
        print("  logs at runtime -- power-cycle the device while sampling.")
    return 1


def cmd_read(args) -> int:
    log_path = Path(args.log) if args.log else None
    handle = log_path.open("a", encoding="utf-8") if log_path else None
    counts: dict[str, int] = {}

    print(f"Reading {args.port} at {args.baud} 8N1. Ctrl-C to stop.")
    if log_path:
        print(f"Logging to {log_path}")
    print("Power-cycle the device now to capture its boot log.\n")

    try:
        with open_port(args.port, args.baud) as port:
            buf = bytearray()
            while True:
                chunk = port.read(4096)
                if not chunk:
                    continue
                if args.raw:
                    print(f"[{stamp()}] raw {chunk.hex()}", flush=True)
                buf.extend(chunk)
                while b"\n" in buf:
                    raw, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    line = raw.decode("utf-8", "replace").rstrip("\r")
                    if not line.strip():
                        continue
                    tag = classify(line)
                    if tag:
                        counts[tag] = counts.get(tag, 0) + 1
                    prefix = f"[{stamp()}]"
                    marker = f" <{tag}>" if tag else ""
                    if tag or not args.only_tagged:
                        print(f"{prefix}{marker} {line}", flush=True)
                    if handle:
                        handle.write(f"{prefix}{marker} {line}\n")
                        handle.flush()
    except KeyboardInterrupt:
        print("\n\nStopped.")
    except serial.SerialException as err:
        print(f"\nSerial error: {err}", file=sys.stderr)
        return 2
    finally:
        if handle:
            handle.close()

    if counts:
        print("Tagged lines seen:")
        for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {tag:<7} {n}")
    else:
        print("No tagged lines. If nothing at all appeared, try --scan-baud.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--log", help="append every line to this file")
    parser.add_argument("--raw", action="store_true", help="also hex-dump chunks")
    parser.add_argument("--only-tagged", action="store_true",
                        help="print only lines matching a highlight pattern")
    parser.add_argument("--list", action="store_true", help="list serial ports")
    parser.add_argument("--scan-baud", action="store_true",
                        help="sample every rate and report which reads as text")
    parser.add_argument("--dwell", type=float, default=4.0,
                        help="seconds per rate when scanning")
    args = parser.parse_args()

    if args.list:
        return cmd_list()
    if args.scan_baud:
        return cmd_scan_baud(args)
    return cmd_read(args)


if __name__ == "__main__":
    sys.exit(main())
