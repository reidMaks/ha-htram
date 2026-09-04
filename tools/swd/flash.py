#!/usr/bin/env python3
"""
Flash a firmware image into the GD32F150 main flash over SWD, using the
SRAM-resident streaming programmer (flash_writer.c).

Why this instead of `pyocd flash`: openocd can't examine a GD32 over SWD-only
(no NRST), and pyocd's stm32f103rc flash algorithm overflows this chip's 8 KB
SRAM. So flash_writer.c drives the FMC directly and receives the image over
USART1 (PA2/PA3) in ACKed chunks. See tools/swd/flash_writer.c and
docs/GD32_HARDWARE_MAP.md §6.8.

Prereqs (see docs/BENCH.md):
  - Raspberry Pi Pico debugprobe on TP16(SWCLK)/TP17(SWDIO) + UART bridge
    (Pico GP4 -> PA3, GP5 <- PA2), exposed as a serial port (default ttyACM0).
  - RDP already removed (tools/swd/rdp_unlock.c) so the flash is writable.
  - arm-none-eabi-gcc + the repo venv's pyocd on PATH.

Run with the repo venv python (it has pyserial + pyocd):
  .venv/bin/python tools/swd/flash.py                    # flash our firmware (build/gd32_firmware.bin)
  .venv/bin/python tools/swd/flash.py --factory          # restore the factory dump (gd32_flash.bin)
  .venv/bin/python tools/swd/flash.py path/to/image.bin  # flash an arbitrary raw image at 0x08000000
  .venv/bin/python tools/swd/flash.py --port /dev/ttyACM1  # different UART bridge
  .venv/bin/python tools/swd/flash.py --no-reset         # don't reset+run after flashing

A `reset halt` is issued before loading the writer: if the currently-running
firmware is asleep (factory standby WFI) a plain halt can leave peripherals in a
state where the writer never emits READY. reset-halt catches the core cleanly at
the reset vector first.
"""
import argparse
import struct
import subprocess
import sys
import time
from pathlib import Path

import serial

REPO = Path(__file__).resolve().parents[2]
SWD = REPO / "tools/swd"
PYOCD = REPO / ".venv/bin/pyocd"
FW_IMAGE = REPO / "firmware/gd32/build/gd32_firmware.bin"
FACTORY_IMAGE = SWD / "gd32_flash.bin"
CHUNK = 256


def crc16_ccitt(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def pyocd(*cmds, freq="100k", timeout=60):
    args = [str(PYOCD), "cmd", "-t", "cortex_m", "-f", freq]
    for c in cmds:
        args += ["-c", c]
    return subprocess.run(args, timeout=timeout, check=True)


def build_writer() -> tuple[Path, str]:
    """Build flash_writer.c for SRAM exec; return (bin path, thumb entry PC)."""
    elf, binf = SWD / "flash_writer.elf", SWD / "flash_writer.bin"
    subprocess.run(
        ["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib",
         "-nostartfiles", "-ffreestanding", "-T", str(SWD / "sram.ld"),
         "-Wl,--entry=main", "-o", str(elf), str(SWD / "flash_writer.c")],
        check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", str(elf), str(binf)],
                   check=True)
    nm = subprocess.run(["arm-none-eabi-nm", str(elf)],
                        capture_output=True, text=True).stdout
    entry = next(l for l in nm.splitlines() if " T main" in l).split()[0]
    pc = f"0x{int(entry, 16) | 1:08X}"  # thumb bit
    print(f"[build] writer {binf.stat().st_size} B, entry {pc}")
    return binf, pc


def main() -> int:
    ap = argparse.ArgumentParser(description="Flash a GD32F150 image over SWD.")
    ap.add_argument("image", nargs="?", help="raw image at 0x08000000 "
                    "(default: our firmware build)")
    ap.add_argument("--factory", action="store_true",
                    help="flash the factory dump (tools/swd/gd32_flash.bin)")
    ap.add_argument("--port", default="/dev/ttyACM0", help="UART bridge port")
    ap.add_argument("--no-reset", action="store_true",
                    help="do not reset+run the target after flashing")
    args = ap.parse_args()

    if args.factory:
        img_path = FACTORY_IMAGE
    elif args.image:
        img_path = Path(args.image)
    else:
        img_path = FW_IMAGE
    if not img_path.exists():
        print(f"image not found: {img_path}", file=sys.stderr)
        return 1

    binf, pc = build_writer()

    # Clean core state first, then load + run the writer from SRAM.
    print("[pyocd] reset halt")
    pyocd("reset halt")
    time.sleep(0.5)
    print("[pyocd] load writer to 0x20000000 and run")
    pyocd("halt", f"loadmem 0x20000000 {binf}", "wreg sp 0x20002000",
          f"wreg pc {pc}", "wreg xpsr 0x01000000", "c")

    img = img_path.read_bytes()
    if len(img) & 1:
        img += b"\xFF"
    host_crc = crc16_ccitt(img)
    print(f"[img] {img_path.name}: {len(img)} bytes, host CRC=0x{host_crc:04X}")

    ser = serial.Serial(args.port, 115200, timeout=3)
    t0, banner = time.time(), b""
    while time.time() - t0 < 6:
        banner += ser.read(64)
        if b"READY" in banner:
            break
    print("[dev]", banner.decode("latin1").strip())
    if b"READY" not in banner:
        print("no READY from writer, abort", file=sys.stderr)
        return 1

    sent = 0
    for off in range(0, len(img), CHUNK):
        piece = img[off:off + CHUNK]
        ser.write(struct.pack("<H", len(piece)))
        ser.write(piece)
        ser.flush()
        ack = ser.read(1)
        if ack != b"\x06":
            print(f"\n[NAK] at offset {off}: got {ack!r}, tail={ser.read(200)!r}",
                  file=sys.stderr)
            return 1
        sent += len(piece)
        print(f"\r[stream] {sent}/{len(img)}", end="", flush=True)
    print()
    ser.write(struct.pack("<H", 0))  # end marker
    ser.flush()
    time.sleep(0.5)
    done = ser.read(200).decode("latin1").strip()
    print("[dev]", done)
    ser.close()

    dev_crc = None
    if "crc=" in done:
        dev_crc = int(done.split("crc=")[1].split()[0], 16)
    if dev_crc is not None and dev_crc != host_crc:
        print(f"CRC MISMATCH: host 0x{host_crc:04X} != device 0x{dev_crc:04X}",
              file=sys.stderr)
        return 1
    print(f"[ok] CRC match 0x{host_crc:04X}")

    if not args.no_reset:
        print("[pyocd] reset; go")
        pyocd("reset", "go")
    return 0


if __name__ == "__main__":
    sys.exit(main())
