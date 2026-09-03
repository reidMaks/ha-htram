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
echo "=== Loading into SRAM via SWD ==="
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
    -c "exit"

echo ""
echo "=== Code is running! ==="
echo "Listen on UART:  picocom -b 115200 /dev/ttyACM0"
echo "Or:              stty -F /dev/ttyACM0 115200 raw -echo && cat /dev/ttyACM0"
