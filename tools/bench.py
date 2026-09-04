#!/home/max/pet/ha-htram/.venv/bin/python3
"""
HTRAM Hardware Bench Tool — Single CLI for GD32F150 research.

Usage:
    ./tools/bench.py inspect            # Analyze factory firmware dump for pins & periphs
    ./tools/bench.py uart               # Listen on inter-chip UART (/dev/ttyACM0 @ 115200)
    ./tools/bench.py run <probe.c>      # Compile C file, load into SRAM via SWD, and stream UART
    ./tools/bench.py leds               # Run SRAM LED & power probe
    ./tools/bench.py button             # Run SRAM button EXTI probe
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
SWD_DIR = TOOLS_DIR / "swd"
DUMP_FILE = SWD_DIR / "gd32_flash.bin"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"
PYOCD = REPO_ROOT / ".venv" / "bin" / "pyocd"
SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200


def get_pyocd() -> str:
    if PYOCD.exists():
        return str(PYOCD)
    return "pyocd"


def cmd_uart(args: argparse.Namespace) -> None:
    """Listen to inter-chip UART stream on /dev/ttyACM0."""
    try:
        import serial
    except ImportError:
        print("Error: pyserial not installed in .venv", file=sys.stderr)
        sys.exit(1)

    port = args.port or SERIAL_PORT
    baud = args.baud or BAUD_RATE
    duration = getattr(args, "duration", 0) or 0
    dur_str = f"for {duration}s" if duration > 0 else "Ctrl+C to stop"
    print(f"[UART] Opening {port} @ {baud} baud ({dur_str})...")
    start_t = time.time()
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
        ser.reset_input_buffer()
        while True:
            if duration > 0 and (time.time() - start_t) >= duration:
                break
            line = ser.readline()
            if line:
                sys.stdout.write(line.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n[UART] Stopped.")
    except Exception as e:
        print(f"[UART] Error: {e}", file=sys.stderr)
        sys.exit(1)


def compile_and_load_sram(src_path: Path) -> None:
    """Compile a C file for Cortex-M3 SRAM execution and load it via pyocd."""
    ld_script = SWD_DIR / "sram.ld"
    base_name = src_path.stem
    elf_path = SWD_DIR / f"{base_name}.elf"
    bin_path = SWD_DIR / f"{base_name}.bin"

    print(f"[BUILD] Compiling {src_path.name} -> {bin_path.name}...")
    build_cmd = [
        "arm-none-eabi-gcc",
        "-mcpu=cortex-m3",
        "-mthumb",
        "-Os",
        "-nostdlib",
        "-nostartfiles",
        "-ffreestanding",
        "-T", str(ld_script),
        "-Wl,--entry=main",
        "-o", str(elf_path),
        str(src_path),
    ]
    res = subprocess.run(build_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[BUILD ERROR]\n{res.stderr}", file=sys.stderr)
        sys.exit(1)

    objcopy_cmd = ["arm-none-eabi-objcopy", "-O", "binary", str(elf_path), str(bin_path)]
    subprocess.run(objcopy_cmd, check=True)

    nm_cmd = ["arm-none-eabi-nm", str(elf_path)]
    nm_out = subprocess.check_output(nm_cmd, text=True)
    entry_addr = None
    for line in nm_out.splitlines():
        if " T main" in line:
            entry_addr = int(line.split()[0], 16)
            break

    if entry_addr is None:
        print("Error: 'main' symbol not found in ELF", file=sys.stderr)
        sys.exit(1)

    entry_thumb = entry_addr | 1
    size = bin_path.stat().st_size
    print(f"[BUILD] OK: size={size} bytes, main=0x{entry_addr:08X} (Thumb PC=0x{entry_thumb:08X})")

    # Load via pyocd
    pyocd_bin = get_pyocd()
    print("[SWD] Loading into SRAM via pyocd @ 100k clock...")
    pyocd_cmd = [
        pyocd_bin,
        "cmd",
        "-t", "cortex_m",
        "-f", "100k",
        "-c", f"halt; loadmem 0x20000000 {bin_path}; wreg sp 0x20002000; wreg pc 0x{entry_thumb:08X}; wreg xpsr 0x01000000; c",
    ]
    res = subprocess.run(pyocd_cmd, capture_output=True, text=True)
    if res.returncode != 0 or "Error" in res.stdout:
        print(f"[SWD ERROR]\n{res.stdout}\n{res.stderr}", file=sys.stderr)
        sys.exit(1)

    print("[SWD] Target running from SRAM!")


def cmd_run(args: argparse.Namespace) -> None:
    """Compile C source, flash into SRAM, and stream UART."""
    src = Path(args.source).resolve()
    if not src.exists():
        print(f"Error: Source file {src} does not exist", file=sys.stderr)
        sys.exit(1)

    compile_and_load_sram(src)
    cmd_uart(args)


def cmd_inspect(args: argparse.Namespace) -> None:
    """Statically inspect factory firmware dump for pinout mappings."""
    if not DUMP_FILE.exists():
        print(f"Error: Flash dump not found at {DUMP_FILE}", file=sys.stderr)
        sys.exit(1)

    try:
        import capstone
    except ImportError:
        print("Error: capstone not installed in .venv", file=sys.stderr)
        sys.exit(1)

    with open(DUMP_FILE, "rb") as f:
        code = f.read()

    print("==========================================================")
    print("   HTRAM Factory Firmware Inspection (GD32F150C8T6)       ")
    print("==========================================================")

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    # 1. Inspect LED function at 0x080006D6
    print("\n--- [1] LED / Front Panel Bitbang Routine (0x080006D6 - 0x08000850) ---")
    off = 0x080006D6 - 0x08000000
    for i in md.disasm(code[off:off+350], 0x080006D6):
        target = ""
        if "[pc" in i.op_str:
            import re
            m = re.search(r"\[pc, #?(0x[0-9a-fA-F]+|[0-9]+)\]", i.op_str)
            if m:
                val = int(m.group(1), 0)
                pc_base = (i.address + 4) & ~3
                target_addr = pc_base + val
                t_off = target_addr - 0x08000000
                if 0 <= t_off <= len(code) - 4:
                    word = struct.unpack("<I", code[t_off:t_off+4])[0]
                    target = f" -> [0x{target_addr:08X}] = 0x{word:08X}"
        print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str:30s}{target}")

    print("\n--- [1c] Callers of 0x080006D6, 0x0800071E, 0x0800074C ---")
    for off in range(0, len(code)-4, 2):
        addr = 0x08000000 + off
        insns = list(md.disasm(code[off:off+4], addr))
        if insns:
            i = insns[0]
            if i.mnemonic in ("bl", "b") and any(x in i.op_str for x in ["#0x80006d6", "#0x800071e", "#0x800074c"]):
                print(f"  Caller at 0x{addr:08X}: {i.mnemonic} {i.op_str}")

    print("\n--- [1b] GPIO Library Functions (0x080009B8, 0x080009E0, 0x08000A44) ---")
    for addr in [0x080009B8, 0x080009E0, 0x08000A44]:
        off = addr - 0x08000000
        print(f"  Function at 0x{addr:08X}:")
        for i in md.disasm(code[off:off+50], addr):
            print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    # 2. Inspect EXTI Table Pointer
    print("\n--- [2] Button EXTI Configuration ---")
    exti_ptr_off = 0x0800E34C - 0x08000000
    if exti_ptr_off + 4 <= len(code):
        ptr = struct.unpack("<I", code[exti_ptr_off:exti_ptr_off+4])[0]
        print(f"  EXTI Configuration Table RAM Pointer: 0x{ptr:08X}")

    # 3. Inspect calls to syscfg_exti_line_config
    print("  Callers of syscfg_exti_line_config:")
    off = 0x0800E0A0 - 0x08000000
    for i in md.disasm(code[off:off+80], 0x0800E0A0):
        print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    # 4. Search for GPIOA, GPIOB, GPIOC BOP usage
    print("\n--- [3] Pin Set/Reset Register References ---")
    for base, name in [(0x48000000, "GPIOA"), (0x48000400, "GPIOB"), (0x48000800, "GPIOC")]:
        bop = base + 0x18
        bc = base + 0x28
        bop_matches = []
        bc_matches = []
        for o in range(0, len(code)-4, 4):
            val = struct.unpack("<I", code[o:o+4])[0]
            if val == bop:
                bop_matches.append(f"0x{0x08000000+o:08X}")
            elif val == bc:
                bc_matches.append(f"0x{0x08000000+o:08X}")
        print(f"  {name} BOP (+0x18, Set):   {', '.join(bop_matches)}")
        print(f"  {name} BC  (+0x28, Clear): {', '.join(bc_matches)}")

    # 5. Inspect power switch / enable pins around 0x08008210
    print("\n--- [4] Power Control / Periph Enable (0x08008210 - 0x08008280) ---")
    off = 0x08008218 - 0x08000000
    for i in md.disasm(code[off:off+90], 0x08008218):
        print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    print("\n--- [5] Functions 0x080066B0 (gpio_mode_set) & 0x08006714 (gpio_output_options_set) ---")
    for fn, name in [(0x080066B0, "gpio_mode_set"), (0x08006714, "gpio_output_options_set"), (0x08007514, "fn_7514"), (0x08007554, "fn_7554")]:
        print(f"  {name} at 0x{fn:08X}:")
        off = fn - 0x08000000
        for i in md.disasm(code[off:off+50], fn):
            print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    print("\n--- [5c] Functions 0x08005400 - 0x08005700 (Bitbang routines) ---")
    off = 0x08005400 - 0x08000000
    for i in md.disasm(code[off:off+400], 0x08005400):
        target = ""
        if "[pc" in i.op_str:
            import re
            m = re.search(r"\[pc, #?(0x[0-9a-fA-F]+|[0-9]+)\]", i.op_str)
            if m:
                val = int(m.group(1), 0)
                pc_b = (i.address + 4) & ~3
                t_addr = pc_b + val
                t_off = t_addr - 0x08000000
                if 0 <= t_off <= len(code) - 4:
                    word = struct.unpack("<I", code[t_off:t_off+4])[0]
                    target = f" -> [0x{t_addr:08X}] = 0x{word:08X}"
        print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str:30s}{target}")


    print("\n--- [6] All references to 0x08007514 and 0x08007554 ---")
    # Search for BL instructions targeting 0x08007514 and 0x08007554
    for off in range(0, len(code)-4, 2):
        addr = 0x08000000 + off
        insns = list(md.disasm(code[off:off+4], addr))
        if insns:
            i = insns[0]
            if i.mnemonic in ("bl", "b") and ("#0x8007514" in i.op_str or "#0x8007554" in i.op_str):
                print(f"  Caller at 0x{addr:08X}: {i.mnemonic} {i.op_str}")

    print("\n--- [7] Disassembly of 0x08007606 to 0x08007700 ---")
    off = 0x08007606 - 0x08000000
    for i in md.disasm(code[off:off+220], 0x08007606):
        print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    print("\n--- [29] I2C Read function at 0x08005672 ---")
    off = 0x08005672 - 0x08000000
    for i in md.disasm(code[off:off+100], 0x08005672):
        print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str}")

    print("\n--- [21b] Timer Base Register References in Flash ---")
    timers = {
        0x40012C00: "TIMER0",
        0x40000000: "TIMER1",
        0x40000400: "TIMER2",
        0x40001000: "TIMER5",
        0x40001C00: "TIMER13",
        0x40014000: "TIMER14",
        0x40014400: "TIMER15",
        0x40014800: "TIMER16",
    }
    for t_addr, t_name in timers.items():
        matches = []
        for o in range(0, len(code)-4, 4):
            val = struct.unpack("<I", code[o:o+4])[0]
            if val == t_addr:
                matches.append(f"0x{0x08000000+o:08X}")
        if matches:
            print(f"  {t_name:7s} (0x{t_addr:08X}): {', '.join(matches)}")

    port_names = {0x48000000: "GPIOA", 0x48000400: "GPIOB", 0x48000800: "GPIOC", 0x48001400: "GPIOF"}
    print("\n--- [16] GPIO Table at 0x08001388 (copied to 0x20000000) ---")
    data_off = 0x08001388 - 0x08000000
    for idx in range(6):
        entry_off = data_off + idx * 16
        words = struct.unpack("<4I", code[entry_off:entry_off+16])
        port = port_names.get(words[0], f"0x{words[0]:08X}")
        pin_mask = words[1]
        active_pins = [f"P{port[4] if 'GPIO' in port else '?'}{b}" for b in range(16) if (pin_mask & (1 << b))]
        pins_str = f"0x{pin_mask:04X} ({', '.join(active_pins)})"
        rcu_clk = words[2]
        extra = words[3]
        print(f"  Entry #{idx}: Port={port:5s} | PinMask={pins_str} | RCU=0x{rcu_clk:04X} | Extra=0x{extra:08X}")

    print("\n--- [18] Functions referencing 0x20000000 at 0x08007400 and 0x0800F130 ---")
    for target_ref in [0x08007404, 0x0800F134]:
        start = target_ref - 100
        off = start - 0x08000000
        print(f"\n  Near 0x{target_ref:08X}:")
        for i in md.disasm(code[off:off+120], start):
            target = ""
            if "[pc" in i.op_str:
                import re
                m = re.search(r"\[pc, #?(0x[0-9a-fA-F]+|[0-9]+)\]", i.op_str)
                if m:
                    val = int(m.group(1), 0)
                    pc_b = (i.address + 4) & ~3
                    t_addr = pc_b + val
                    t_o = t_addr - 0x08000000
                    if 0 <= t_o <= len(code) - 4:
                        word = struct.unpack("<I", code[t_o:t_o+4])[0]
                        target = f" -> [0x{t_addr:08X}] = 0x{word:08X}"
            print(f"    0x{i.address:08X}: {i.mnemonic:8s} {i.op_str:25s}{target}")
    for o in range(0, len(code)-4, 4):
        val = struct.unpack("<I", code[o:o+4])[0]
        if val == 0x20000000:
            print(f"  At 0x{0x08000000+o:08X}")

    print("\n[INSPECT] Completed.")
    # gpio_mode_set is at 0x080009E0 or wrapped
    print("\n==========================================================")
    print("   ALL GPIO INITIALIZATIONS IN FACTORY FIRMWARE            ")
    print("==========================================================")
    port_names = {0x48000000: "GPIOA", 0x48000400: "GPIOB", 0x48000800: "GPIOC", 0x48001400: "GPIOF"}
    mode_names = {0: "INPUT", 1: "OUTPUT", 2: "AF", 3: "ANALOG"}
    pull_names = {0: "NONE", 1: "PULLUP", 2: "PULLDOWN"}

    # Search for all BL to gpio_mode_set (0x080066B0 and 0x080009E0)
    for off in range(0, len(code)-4, 2):
        addr = 0x08000000 + off
        insns = list(md.disasm(code[off:off+4], addr))
        if not insns:
            continue
        i = insns[0]
        if i.mnemonic in ("bl", "b") and ("#0x80009e0" in i.op_str or "#0x80066b0" in i.op_str):
            # Look backwards up to 30 instructions and track r0, r1, r2, r3 values
            regs = {}
            back_start = max(0, off - 60)
            back_insns = list(md.disasm(code[back_start:off], 0x08000000 + back_start))
            for bi in back_insns:
                # Handle MOV / MOVS / MOV.W / MOVW
                if bi.mnemonic.startswith("mov"):
                    parts = [p.strip() for p in bi.op_str.split(",")]
                    if len(parts) == 2 and parts[0].startswith("r") and parts[1].startswith("#"):
                        try:
                            regs[parts[0]] = int(parts[1][1:], 0)
                        except ValueError:
                            pass
                # Handle LDR
                elif bi.mnemonic == "ldr" or bi.mnemonic == "ldr.w":
                    parts = [p.strip() for p in bi.op_str.split(",")]
                    if len(parts) == 2 and parts[0].startswith("r") and "[pc" in parts[1]:
                        m = re.search(r"\[pc, #?(0x[0-9a-fA-F]+|[0-9]+)\]", parts[1])
                        if m:
                            v = int(m.group(1), 0)
                            pc_b = (bi.address + 4) & ~3
                            t_addr = pc_b + v
                            t_o = t_addr - 0x08000000
                            if 0 <= t_o <= len(code) - 4:
                                regs[parts[0]] = struct.unpack("<I", code[t_o:t_o+4])[0]

            r0 = regs.get("r0")
            r1 = regs.get("r1")
            r2 = regs.get("r2")
            r3 = regs.get("r3")

            p_name = port_names.get(r0, f"0x{r0:08X}" if r0 else "??")
            m_name = mode_names.get(r1, str(r1))
            pu_name = pull_names.get(r2, str(r2))
            
            # format pins
            pins_str = ""
            if r3 is not None:
                active_pins = [f"P{p_name[4] if 'GPIO' in p_name else '?'}{b}" for b in range(16) if (r3 & (1 << b))]
                pins_str = f"0x{r3:04X} ({', '.join(active_pins)})"
            else:
                pins_str = "??"

            print(f"  At 0x{addr:08X}: {p_name:5s} | Mode: {m_name:6s} | Pull: {pu_name:8s} | Pins: {pins_str}")



def cmd_disasm(args: argparse.Namespace) -> None:
    """Disassemble code from flash dump at given address."""
    if not DUMP_FILE.exists():
        print(f"Error: Flash dump not found at {DUMP_FILE}", file=sys.stderr)
        sys.exit(1)

    try:
        import capstone
    except ImportError:
        print("Error: capstone not installed in .venv", file=sys.stderr)
        sys.exit(1)

    with open(DUMP_FILE, "rb") as f:
        code = f.read()

    addr = int(args.address, 0)
    if getattr(args, "callers", False):
        print(f"Searching for callers of 0x{addr:08X}:")
        for o in range(0, len(code)-4, 2):
            w1, w2 = struct.unpack("<HH", code[o:o+4])
            if (w1 & 0xF800) == 0xF000 and (w2 & 0xD000) == 0xD000:
                s = (w1 >> 10) & 1
                j1 = (w2 >> 13) & 1
                j2 = (w2 >> 11) & 1
                imm10 = w1 & 0x3FF
                imm11 = w2 & 0x7FF
                i1 = ~(j1 ^ s) & 1
                i2 = ~(j2 ^ s) & 1
                imm32 = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
                if s:
                    imm32 -= (1 << 25)
                cur_addr = 0x08000000 + o
                dest = cur_addr + 4 + imm32
                if dest == addr:
                    print(f"  Caller at 0x{cur_addr:08X}")
        return

    off = addr - 0x08000000
    if off < 0 or off >= len(code):
        print(f"Error: Address 0x{addr:08X} is out of flash bounds (0x08000000..0x{0x08000000+len(code):08X})", file=sys.stderr)
        sys.exit(1)

    n_bytes = args.bytes
    chunk = code[off:off + n_bytes]
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    print(f"Disassembly at 0x{addr:08X} ({n_bytes} bytes):")
    for i in md.disasm(chunk, addr):
        target = ""
        if "[pc" in i.op_str:
            import re
            m = re.search(r"\[pc, #?(0x[0-9a-fA-F]+|[0-9]+)\]", i.op_str)
            if m:
                val = int(m.group(1), 0)
                pc_base = (i.address + 4) & ~3
                target_addr = pc_base + val
                t_off = target_addr - 0x08000000
                if 0 <= t_off <= len(code) - 4:
                    word = struct.unpack("<I", code[t_off:t_off+4])[0]
                    target = f" -> [0x{target_addr:08X}] = 0x{word:08X}"
        print(f"  0x{i.address:08X}: {i.mnemonic:8s} {i.op_str:30s}{target}")

def cmd_search(args: argparse.Namespace) -> None:
    """Search for a hex string or 32-bit word in flash dump."""
    if not DUMP_FILE.exists():
        print(f"Error: Flash dump not found at {DUMP_FILE}", file=sys.stderr)
        sys.exit(1)

    with open(DUMP_FILE, "rb") as f:
        code = f.read()

    pattern = args.pattern.replace(" ", "").replace("0x", "")
    data = bytes.fromhex(pattern)
    print(f"Searching for hex pattern {data.hex()} ({len(data)} bytes) in flash dump:")
    matches = []
    idx = 0
    while True:
        pos = code.find(data, idx)
        if pos == -1:
            break
        addr = 0x08000000 + pos
        matches.append(f"0x{addr:08X}")
        idx = pos + 1

    if matches:
        print(f"  Found {len(matches)} match(es): {', '.join(matches)}")
def cmd_dump(args: argparse.Namespace) -> None:
    """Hexdump bytes from flash dump."""
    if not DUMP_FILE.exists():
        print(f"Error: Flash dump not found at {DUMP_FILE}", file=sys.stderr)
        sys.exit(1)

    with open(DUMP_FILE, "rb") as f:
        code = f.read()

    addr = int(args.address, 0)
    off = addr - 0x08000000
    if off < 0 or off >= len(code):
        print(f"Error: Address 0x{addr:08X} out of range", file=sys.stderr)
        sys.exit(1)

    length = args.length
    chunk = code[off:off + length]
    for i in range(0, len(chunk), 16):
        row = chunk[i:i+16]
        hex_s = " ".join(f"{b:02X}" for b in row)
        asc_s = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  0x{addr + i:08X}:  {hex_s:<48}  |{asc_s}|")


def main() -> None:
    parser = argparse.ArgumentParser(description="HTRAM Hardware Bench Tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # dump
    p_dmp = sub.add_parser("dump", help="Hexdump bytes from flash dump")
    p_dmp.add_argument("address", help="Hex address (e.g. 0x0800E790)")
    p_dmp.add_argument("--length", type=int, default=64, help="Number of bytes to dump")

    # uart
    p_uart = sub.add_parser("uart", help="Monitor UART on /dev/ttyACM0")
    p_uart.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_uart.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_uart.add_argument("--duration", type=float, default=0, help="Duration in seconds (0 = forever)")

    # inspect
    sub.add_parser("inspect", help="Inspect factory firmware dump")

    # run
    p_run = sub.add_parser("run", help="Compile and load C probe into SRAM")
    p_run.add_argument("source", help="C source file to compile and load")
    p_run.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_run.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_run.add_argument("--duration", type=float, default=0, help="Duration in seconds (0 = forever)")

    # leds
    p_led = sub.add_parser("leds", help="Run LED & button probe")
    p_led.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_led.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_led.add_argument("--duration", type=float, default=0, help="Duration in seconds (0 = forever)")

    # buzzer
    p_buz = sub.add_parser("buzzer", help="Run buzzer (PB0) & button probe")
    p_buz.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_buz.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_buz.add_argument("--duration", type=float, default=0, help="Duration in seconds (0 = forever)")

    # temp
    p_temp = sub.add_parser("temp", help="Read SHT30 temperature and humidity over I2C")
    p_temp.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_temp.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_temp.add_argument("--duration", type=float, default=15, help="Duration in seconds (0 = forever)")

    # co2
    p_co2 = sub.add_parser("co2", help="Read Honeywell CRIR M1 CO2 sensor over USART0")
    p_co2.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_co2.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_co2.add_argument("--duration", type=float, default=20, help="Duration in seconds (0 = forever)")

    # display
    p_disp = sub.add_parser("display", help="Run ST7789 display test patterns")
    p_disp.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    p_disp.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    p_disp.add_argument("--duration", type=float, default=25, help="Duration in seconds (0 = forever)")

    # disasm
    p_dis = sub.add_parser("disasm", help="Disassemble address in flash dump")
    p_dis.add_argument("address", help="Hex address (e.g. 0x08005400)")
    p_dis.add_argument("--bytes", type=int, default=128, help="Number of bytes to disassemble")
    p_dis.add_argument("--callers", action="store_true", help="Search for all callers of address")

    # search
    p_sea = sub.add_parser("search", help="Search hex pattern in flash dump")
    p_sea.add_argument("pattern", help="Hex string to search (e.g. fe040003)")

    args = parser.parse_args()

    if args.cmd == "dump":
        cmd_dump(args)
    elif args.cmd == "uart":
        cmd_uart(args)
    elif args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "disasm":
        cmd_disasm(args)
    elif args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "leds":
        args.source = str(SWD_DIR / "uart_test.c")
        cmd_run(args)
    elif args.cmd == "buzzer":
        args.source = str(SWD_DIR / "uart_test.c")
        cmd_run(args)
    elif args.cmd == "temp":
        args.source = str(SWD_DIR / "uart_test.c")
        cmd_run(args)
    elif args.cmd == "co2":
        args.source = str(SWD_DIR / "uart_test.c")
        cmd_run(args)
    elif args.cmd == "display":
        args.source = str(SWD_DIR / "display_test.c")
        cmd_run(args)


if __name__ == "__main__":
    main()
