#!/usr/bin/env python3
"""
Flash a firmware image into the GD32F150 main flash over SWD, using the
SRAM-resident streaming programmer (flash_writer.c), OR via OTA through the ESP32.

Run with the repo venv python:
  .venv/bin/python tools/swd/flash.py                    # flash our firmware (build/gd32_firmware.bin)
  .venv/bin/python tools/swd/flash.py --factory          # restore the factory dump (gd32_flash.bin)
  .venv/bin/python tools/swd/flash.py --ota htram.local  # flash via OTA over ESP32 HTTP
"""
import argparse
import struct
import subprocess
import sys
import time
import json
from pathlib import Path

import serial
import requests

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
    pc = f"0x{int(entry, 16) | 1:08X}"
    print(f"[build] writer {binf.stat().st_size} B, entry {pc}")
def build_swd_writer() -> tuple[Path, int]:
    """Build swd_flash_writer.c for SRAM exec; return (bin path, thumb entry PC int)."""
    elf, binf = SWD / "swd_flash_writer.elf", SWD / "swd_flash_writer.bin"
    subprocess.run(
        ["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib",
         "-nostartfiles", "-ffreestanding", "-T", str(SWD / "sram.ld"),
         "-Wl,--entry=main", "-o", str(elf), str(SWD / "swd_flash_writer.c")],
        check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", str(elf), str(binf)],
                   check=True)
    nm = subprocess.run(["arm-none-eabi-nm", str(elf)],
                        capture_output=True, text=True).stdout
    entry = next(l for l in nm.splitlines() if " T main" in l).split()[0]
    pc = int(entry, 16) | 1
    print(f"[build] swd_writer {binf.stat().st_size} B, entry 0x{pc:08X}")
    return binf, pc


def flash_via_swd_mem(img: bytes, no_reset: bool, host_crc: int) -> int:
    from pyocd.core.helpers import ConnectHelper

    binf, pc = build_swd_writer()
    bin_data = list(binf.read_bytes())

    MAILBOX_ADDR = 0x20001000
    CHUNK_SIZE = 512
    FLASH_BASE = 0x08000000

    session = None
    for attempt in range(1, 6):
        s = None
        try:
            print(f"[pyocd] connecting via SWD @ 10kHz (attempt {attempt}/5)...")
            s = ConnectHelper.session_with_chosen_probe(
                target_override="cortex_m",
                options={'connect_mode': 'attach', 'frequency': 10000}
            )
            s.open()
            target = s.board.target
            target.halt()
            session = s
            break
        except Exception as e:
            print(f"[pyocd] attempt {attempt} failed ({e}), retrying...")
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(0.5)

    if not session:
        print("[pyocd] Failed to connect to target after 5 attempts", file=sys.stderr)
        return 1

    try:
        target = session.board.target
        time.sleep(0.1)

        print(f"[pyocd] loading swd_writer ({len(bin_data)} B) to 0x20000000...")
        target.write_memory_block8(0x20000000, bin_data)

        core = target.selected_core
        core.write_core_register_raw('sp', 0x20002000)
        core.write_core_register_raw('pc', pc)
        core.write_core_register_raw('xpsr', 0x01000000)
        target.resume()

        # Wait for writer initialization
        t0 = time.time()
        ready = False
        while time.time() - t0 < 3.0:
            magic = target.read32(MAILBOX_ADDR)
            status = target.read32(MAILBOX_ADDR + 8)
            if magic == 0x4D41494C and status == 1:
                ready = True
                break
            time.sleep(0.05)

        if not ready:
            print("[pyocd] ERROR: swd_writer failed to initialize mailbox!", file=sys.stderr)
            return 1
        print("[pyocd] swd_writer running and ready")

        total = len(img)
        sent = 0
        t_start = time.time()

        for off in range(0, total, CHUNK_SIZE):
            chunk = list(img[off:off + CHUNK_SIZE])
            target_addr = FLASH_BASE + off

            # Write chunk data into mailbox buf
            target.write_memory_block8(MAILBOX_ADDR + 0x18, chunk)
            target.write32(MAILBOX_ADDR + 0x0C, target_addr)
            target.write32(MAILBOX_ADDR + 0x10, len(chunk))
            # Trigger write command (cmd = 1)
            target.write32(MAILBOX_ADDR + 0x04, 1)

            # Wait for FMC erase/program to finish before querying AHB-AP
            time.sleep(0.06)

            # Poll for completion
            t_chunk = time.time()
            ok = False
            while time.time() - t_chunk < 2.0:
                cmd = target.read32(MAILBOX_ADDR + 0x04)
                status = target.read32(MAILBOX_ADDR + 0x08)
                if cmd == 0 and status == 1:
                    ok = True
                    break
                elif status == 2:
                    err = target.read16(MAILBOX_ADDR + 0x16)
                    print(f"\n[pyocd] ERROR writing chunk at 0x{target_addr:08X}: code {err}", file=sys.stderr)
                    return 1
                time.sleep(0.05)

            if not ok:
                print(f"\n[pyocd] Timeout waiting for chunk at 0x{target_addr:08X}", file=sys.stderr)
                return 1

            sent += len(chunk)
            print(f"\r[swd-mem] Flashed {sent}/{total} bytes ({sent*100//total}%)", end="", flush=True)

        print()
        dev_crc = target.read16(MAILBOX_ADDR + 0x14)
        target.write32(MAILBOX_ADDR + 0x04, 2)  # DONE

        dt = time.time() - t_start
        print(f"[swd-mem] Flashed {total} bytes in {dt:.2f}s ({total/dt/1024:.1f} KB/s)")
        print(f"[dev] CRC=0x{dev_crc:04X}, host CRC=0x{host_crc:04X}")

        if dev_crc != host_crc:
            print(f"[pyocd] CRC MISMATCH: host 0x{host_crc:04X} != dev 0x{dev_crc:04X}", file=sys.stderr)
            return 1
        print(f"[ok] CRC match 0x{host_crc:04X}!")

        if not no_reset:
            print("[pyocd] resetting target and running...")
            target.reset()
            target.resume()
        return 0
    finally:
        session.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Flash a GD32F150 image over SWD or OTA.")
    ap.add_argument("image", nargs="?", help="raw image at 0x08000000 "
                    "(default: our firmware build)")
    ap.add_argument("--factory", action="store_true",
                    help="flash the factory dump (tools/swd/gd32_flash.bin)")
    ap.add_argument("--port", default="/dev/ttyACM0", help="UART bridge port (for SWD)")
    ap.add_argument("--ota", help="IP or hostname of ESP32 for OTA flashing (e.g. htram.local)")
    ap.add_argument("--swd-mem", action="store_true",
                    help="flash via SWD memory mailbox (no UART required)")
    ap.add_argument("--no-reset", action="store_true",
                    help="do not reset+run the target after flashing (SWD only)")
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
        
    img = img_path.read_bytes()
    # PADDING NOT REQUIRED FOR OTA (ESPHome handles it), but harmless. We'll pad for both
    if len(img) & 1:
        img += b"\xFF"
    host_crc = crc16_ccitt(img)
    print(f"[img] {img_path.name}: {len(img)} bytes, host CRC=0x{host_crc:04X}")

    if args.ota:
        url = f"http://{args.ota}/gd32_ota"
        print(f"[ota] POSTing image to {url} ... (this will take 5-10 seconds)")
        try:
            resp = requests.post(url, files={'file': ('firmware.bin', img)}, timeout=30)
        except requests.RequestException as e:
            print(f"[ota] Request failed: {e}", file=sys.stderr)
            return 1
            
        if resp.status_code != 200:
            print(f"[ota] HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
            return 1
            
        try:
            res_json = resp.json()
            print(f"[ota] Response: {res_json}")
            if res_json.get("result") != "ok":
                print(f"[ota] Flashing failed: {res_json.get('reason', 'unknown')}", file=sys.stderr)
                return 1
            print(f"[ok] OTA successful! Bytes written: {res_json.get('bytes_written')}")
            return 0
        except json.JSONDecodeError:
            print(f"[ota] Invalid JSON response: {resp.text}", file=sys.stderr)
            return 1

    if args.swd_mem:
        return flash_via_swd_mem(img, args.no_reset, host_crc)

    # Legacy SWD + UART path below
    binf, pc = build_writer()

    print("[pyocd] reset halt")
    pyocd("reset halt")
    time.sleep(0.5)
    print("[pyocd] load writer to 0x20000000 and run")
    pyocd("halt", f"loadmem 0x20000000 {binf}", "wreg sp 0x20002000",
          f"wreg pc {pc}", "wreg xpsr 0x01000000", "c")

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
    ser.write(struct.pack("<H", 0))
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
