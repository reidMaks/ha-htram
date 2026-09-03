# GD32F150 SWD Pinout & Flashing Guide

This document describes the physical test points, pinout mapping, wiring to a Raspberry Pi Pico (CMSIS-DAP / Picoprobe), and OpenOCD commands for reading and flashing the application microcontroller (**GD32F150C8T6**) on the Honeywell HTRAM board.

---

## 1. Physical Pinout & Test Points on PCB

The back side of the HTRAM main board (`Storm Shadow Main Board REV3 20210610`) breaks out factory test points labelled `TP1` through `TP31`.

Tracing to the GD32F150C8T6 (`U8`, LQFP48 package) identifies the SWD debugging interface:

| Board Test Point | GD32F150 Pin | MCU Pin Name | SWD Function | Notes |
|---|---|---|---|---|
| **`TP16`** | Pin 37 | `PA14` | **`SWCLK`** | SWD Clock |
| **`TP17`** | Pin 34 | `PA13` | **`SWDIO`** | SWD Data Input / Output |
| **`GND`** | Pin 47 / Ground plane | `VSS` | **`GND`** | USB connector shield, negative pad of bulk capacitors, or metalized mounting hole |

Nearby test points: `TP15` and `TP18` sit adjacent to the SWD pair on the board layout. Any solid ground connection (such as the micro-USB metal shield) works reliably for `GND`.

---

## 2. Raspberry Pi Pico (CMSIS-DAP / Picoprobe) Wiring

Using a standard Raspberry Pi Pico running [picoprobe / debugprobe](https://github.com/raspberrypi/debugprobe) firmware:

| Raspberry Pi Pico Pin | Pico Function | HTRAM Target | HTRAM Description |
|---|---|---|---|
| **GP2** (Pin 4) | SWCLK | **`TP16`** | GD32 PA14 (SWCLK) |
| **GP3** (Pin 5) | SWDIO | **`TP17`** | GD32 PA13 (SWDIO) |
| **GND** (Pin 3, 8, etc.) | Ground | **`GND`** | USB shield / Ground pad |
| **GP5** (Pin 7, Optional) | UART RX | **ESP32 Pin 25 (`GPIO16` / `RXD2`)** | Inter-chip UART line (connected to GD32 `PA2` / USART1_TX). Used for capturing flash dump at 115200 baud. |


---

## 3. OpenOCD Connection

OpenOCD uses `interface/cmsis-dap.cfg` with `target/stm32f1x.cfg` (GD32F150 shares the standard ARM CoreSight SW-DP debug registers):

```bash
openocd \
    -f interface/cmsis-dap.cfg \
    -c "transport select swd" \
    -c "adapter speed 1000" \
    -f target/stm32f1x.cfg \
    -c "init" \
    -c "reset halt"
```

---

## 4. Firmware Backup & Flashing

### Flash Dump (Without Erasing)
The stock firmware has Readout Protection Level 1 (RDP1) active. To safely extract the flash without triggering flash mass erase:
* Run `./run_flash_dump.sh`.
* It loads an SRAM payload at `0x20000000`, clears CoreSight DP register `0x4` (`CDBGPWRUPREQ`), and dumps the full 64 KB image to `gd32_flash.bin` over UART.

### Unlocking / Flashing Custom Firmware
If you wish to flash custom firmware to the GD32:
1. Ensure you have backed up `gd32_flash.bin`.
2. Remove RDP protection via OpenOCD:
   ```bash
   openocd -f interface/cmsis-dap.cfg -f target/stm32f1x.cfg -c "init; reset halt; stm32f1x unlock 0; reset halt; exit"
   ```
   *(Note: `unlock` resets option bytes and erases the chip).*
3. Flash new or stock binary:
   ```bash
   openocd -f interface/cmsis-dap.cfg -f target/stm32f1x.cfg -c "init; reset halt; flash write_image erase gd32_flash.bin 0x08000000; reset run; exit"
   ```
