#!/usr/bin/env bash
# Build and load UART test into GD32F150 SRAM
# Usage: ./run_uart_test.sh
set -euo pipefail
cd "$(dirname "$0")"

SRC=uart_test.c
LD=sram.ld
ELF=uart_test.elf
BIN=uart_test.bin

echo "=== Building ==="
arm-none-eabi-gcc \
    -mcpu=cortex-m3 -mthumb -Os \
    -nostdlib -nostartfiles -ffreestanding \
    -T ${LD} -Wl,--entry=main \
    -o ${ELF} ${SRC}

arm-none-eabi-objcopy -O binary ${ELF} ${BIN}
arm-none-eabi-size ${ELF}

ENTRY=$(arm-none-eabi-nm ${ELF} | grep ' T main' | awk '{print $1}')
# Thumb bit (bit 0) must be set for Cortex-M execution
ENTRY_THUMB=$(printf "0x%08X" $(( 0x${ENTRY} | 1 )))
echo "Entry point (main): 0x${ENTRY} → PC=${ENTRY_THUMB}"
SIZE=$(stat -c%s ${BIN})
echo "Binary size: ${SIZE} bytes"

echo ""
PYOCD="../../.venv/bin/pyocd"
if [ ! -f "${PYOCD}" ]; then
    PYOCD=pyocd
fi

echo ""
echo "=== Loading into SRAM via SWD (pyocd @ 100k) ==="
"${PYOCD}" cmd -t cortex_m -f 100k \
    -c "halt; loadmem 0x20000000 ${BIN}; wreg sp 0x20002000; wreg pc ${ENTRY_THUMB}; wreg xpsr 0x01000000; c"

echo ""
echo "=== Code is running! Listening on /dev/ttyACM0 @ 115200 ==="
VENV_PYTHON="../../.venv/bin/python3"
if [ ! -f "${VENV_PYTHON}" ]; then
    VENV_PYTHON=python3
fi

"${VENV_PYTHON}" -u -c "
import serial, sys, time
try:
    ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
    print('[Ready] Connected to /dev/ttyACM0. Press Ctrl+C to stop.\n', flush=True)
    while True:
        line = ser.readline()
        if line:
            sys.stdout.write(line.decode('utf-8', errors='replace'))
            sys.stdout.flush()
except KeyboardInterrupt:
    print('\n[Stopped]')
except Exception as e:
    print(f'Serial error: {e}')
"

