#!/usr/bin/env python3
"""
Capture flash dump from GD32F150 UART output.

Listens on serial port, captures hex lines between DUMP: and END: markers,
converts to binary file, and verifies checksum.

Usage: python3 capture_dump.py [--port /dev/ttyACM0] [--baud 115200] [-o flash_dump.bin]
"""

import argparse
import serial
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Capture GD32 flash dump from UART")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("-o", "--output", default="gd32_flash.bin", help="Output binary file")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    ser = serial.Serial(args.port, args.baud, timeout=1)
    ser.reset_input_buffer()

    print("Waiting for READY signal from GD32...")

    data = bytearray()
    checksum = 0
    capturing = False
    lines_captured = 0
    start_time = time.time()
    expected_size = 0

    while True:
        if time.time() - start_time > args.timeout:
            print(f"\n✗ Timeout after {args.timeout}s")
            if lines_captured > 0:
                print(f"  Captured {lines_captured} lines ({len(data)} bytes) before timeout")
            sys.exit(1)

        raw = ser.readline()
        if not raw:
            continue

        try:
            line = raw.decode("ascii", errors="replace").strip()
        except Exception:
            continue

        if not line:
            continue

        # Status messages
        if line == "READY":
            elapsed = time.time() - start_time
            print(f"✓ GD32 is ready ({elapsed:.1f}s)")
            print("  Waiting for CDBGPWRUPREQ clear + dump start...")
            continue

        if line.startswith("DUMP:"):
            # Parse: DUMP:08000000:00010000
            parts = line.split(":")
            if len(parts) >= 3:
                start_addr = int(parts[1], 16)
                expected_size = int(parts[2], 16)
            print(f"✓ Dump started: 0x{start_addr:08X}, size={expected_size} bytes ({expected_size//1024} KB)")
            capturing = True
            start_time = time.time()  # Reset timeout for dump phase
            continue

        if line.startswith("END:"):
            remote_checksum = int(line.split(":")[1], 16)
            print(f"\n✓ Dump complete!")
            print(f"  Received:  {len(data)} bytes")
            print(f"  Expected:  {expected_size} bytes")
            print(f"  Checksum:  local=0x{checksum:08X}  remote=0x{remote_checksum:08X}", end="")
            if (checksum & 0xFFFFFFFF) == remote_checksum:
                print("  ✓ MATCH")
            else:
                print("  ✗ MISMATCH!")

            # Save binary
            with open(args.output, "wb") as f:
                f.write(data)
            print(f"  Saved to:  {args.output}")
            sys.exit(0)

        if line == "DONE":
            continue

        # Data line: :08000000 AA BB CC DD ... [line_checksum]
        if capturing and line.startswith(":"):
            parts = line.split()
            # parts[0] = :ADDR, parts[1:-1] = hex bytes, parts[-1] = line checksum
            if len(parts) >= 18:  # :addr + 16 bytes + checksum
                hex_bytes = parts[1:-1]  # exclude addr and line checksum
                for hb in hex_bytes:
                    try:
                        b = int(hb, 16)
                        data.append(b)
                        checksum += b
                    except ValueError:
                        pass
                lines_captured += 1

                # Progress
                pct = len(data) * 100 // expected_size if expected_size else 0
                print(f"\r  Progress: {len(data)}/{expected_size} bytes ({pct}%) "
                      f"[{lines_captured} lines]", end="", flush=True)

        # Debug: print unexpected lines
        elif not capturing and line not in ("READY", "DONE"):
            print(f"  > {line}")


if __name__ == "__main__":
    main()
