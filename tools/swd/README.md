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

> ⚠️ **OpenOCD does not work on this GD32 and pyocd's built-in flash algorithms
> don't fit.** openocd fails target examination over SWD-only (no NRST wired; it
> reads DBGMCU IDCODE which is blocked under RDP), and pyocd's `stm32f103rc`
> flash algorithm overflows this chip's 8 KB SRAM. The working path is a custom
> SRAM-resident programmer driven by `flash.py`. pyocd is used **only** with the
> generic `cortex_m` target for `loadmem` / `reset halt`. See
> [../../docs/GD32_HARDWARE_MAP.md](../../docs/GD32_HARDWARE_MAP.md) §6.8.

### 4.1. Flash dump (without erasing) — first extraction
The stock firmware has Readout Protection Level 1 (RDP1) active. To extract the
flash without triggering a mass erase (PT SWARM GigaVulnerability #2):
* Run `./run_flash_dump.sh` — it loads an SRAM stub at `0x20000000`, clears
  CoreSight DP register `0x4` (`CDBGPWRUPREQ`) so SRAM-executed code can read
  flash under RDP, and dumps the full 64 KB to `gd32_flash.bin` over UART.
* Keep `gd32_flash.bin` — it is the factory restore image.

### 4.2. Remove RDP (one-time, enables writing)
```bash
.venv/bin/pyocd cmd -t cortex_m -f 100k -c "reset halt"   # regain control if needed
# load + run the RDP unlock stub from SRAM (erases option bytes -> RDP=0xA5):
#   see rdp_unlock.c; applied on the next reset, which mass-erases main flash.
```
`rdp_unlock.c` unlocks FMC+OB, erases the option bytes to the factory default
(software watchdog, no write-protect) and programs `RDP=0xA5`. On reset the
hardware performs the mass erase and drops protection (`OBSTAT 0x…02 -> 0x…00`).

### 4.3. Flash an image — `flash.py`
Streaming programmer: builds `flash_writer.c` for SRAM, loads it via pyocd,
streams the image over the UART bridge in ACKed 256-byte chunks, and verifies a
CRC-16/CCITT of the whole image against the device.

```bash
.venv/bin/python tools/swd/flash.py            # our firmware (firmware/gd32/build/gd32_firmware.bin)
.venv/bin/python tools/swd/flash.py --factory  # restore the factory dump (gd32_flash.bin)
.venv/bin/python tools/swd/flash.py img.bin     # arbitrary raw image at 0x08000000
```

It issues `reset halt` before loading the writer (a plain halt of a sleeping
firmware — e.g. factory standby WFI — can leave the writer unable to emit
`READY`), then `reset; go` after, unless `--no-reset` is given. A CRC mismatch
returns non-zero.

### 4.4. Reading live GPIO / registers (diagnostics)
```bash
.venv/bin/pyocd cmd -t cortex_m -f 100k -c "halt" -c "read32 0x48001414" -c "go"
```
Reading the factory's live GPIO state (flash `gd32_flash.bin`, power on with the
button, `halt`, read `GPIOA/B/C/F` CTL+OCTL) is the ground-truth method for pin
roles — see docs/GD32_HARDWARE_MAP.md §6.9.

---

## 5. Research & Hardware Safety Disclaimer

This hardware reverse engineering, SWD debugging, and memory dump documentation is published strictly for non-commercial educational, interoperability, and scientific research purposes (under EU Directive 2009/24/EC and 17 U.S.C. § 1201(f) fair-use / interoperability exemptions) to preserve functionality of abandoned consumer hardware.

All procedures involve physical hardware interaction. Soldering, test-pad probing, and flashing carry risks of short circuits, permanent hardware damage, or lithium-ion battery hazards. All information is provided **"AS IS"** without warranties; users assume all risk and liability.

