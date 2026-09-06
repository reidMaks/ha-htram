---
name: htram-bench
description: Set up and drive the hardware-in-the-loop bench - Raspberry Pi Pico debugprobe over SWD plus a UART bridge to the GD32 - including the mandatory power-on order and SRAM peripheral probes. Trigger terms (uk) - стенд, пробник, підключити піко, SWD підключення, UART міст, проба периферії, SRAM проба, помацати ногу, дослідити пін.
---

# The hardware bench

## Power-on order is mandatory, not advisory

**Never leave the Pico connected to an unpowered board.**

With the board dead and the probe attached, SWCLK/SWDIO hold 3.3 V on GD32
inputs and current leaks through the ESD diodes into the chip's internal supply
rail. The board half-wakes: not enough to run, enough to make the `PB0` buzzer
screech. The Pico's own LDO feeds all of this, its rail sags, and the host USB
port sources abnormal current. This has already killed a host USB controller
(`xhci_hcd: HC died`) at the moment board power was applied.

**Up:** board power first (battery, or micro-USB into a charger -- *not* into
this same PC), confirm it runs, then the Pico's USB.
**Down:** reverse order.

**Tell-tale:** buzzer screeching while the Pico's LED is dark means the board is
unpowered and being fed parasitically. Unplug the Pico immediately.

If the controller does die, find the address with `lspci | grep -i usb` and
reset the driver:

```bash
echo -n "0000:03:00.4" | sudo tee /sys/bus/pci/drivers/xhci_hcd/unbind
```

A reboot does not clear it; only a full power-down resets the port's
self-resetting fuse.

## Wiring

SWD: Pico `GP2`→`TP16` (PA14, SWCLK), `GP3`→`TP17` (PA13, SWDIO), `GND`→`GND`.

UART bridge -- the Pico impersonates the ESP on GD32 USART1, so it wires up
*as the ESP would*, uncrossed on the ESP side:

| Pico | dir | ESP pad | GD32 node |
| --- | --- | --- | --- |
| GP4 (TX) | ──► | GPIO17 (pad 28) | `PA3` USART1 RX |
| GP5 (RX) | ◄── | GPIO16 (pad 27) | `PA2` USART1 TX |
| GND | — | GND | — |

The crossover lives on the GD32 side. ESPHome leaves GPIO16/17 as inputs
(high-Z), so the ESP does not fight the Pico. The bridge appears as its own
USB-CDC port, typically `/dev/ttyACM1`, 115200 for the SRAM probes.

## Working the bench

```bash
openocd -f interface/cmsis-dap.cfg -c "transport select swd" \
        -c "adapter speed 1000" -f target/stm32f1x.cfg \
        -c "init" -c "reset halt" -c "mdw 0x08000000 4" -c "exit"
```

SRAM probes are the main investigative tool: a tiny program is loaded to
`0x20000000` and run, leaving flash untouched, and reports over UART.

```bash
./tools/bench.py run tools/swd/uart_test.c   # compile, load to SRAM, stream UART
./tools/bench.py leds                        # LED and power-rail probe
./tools/bench.py button                      # button EXTI probe
./tools/bench.py uart                        # listen on the inter-chip UART
./tools/bench.py inspect                     # pin/peripheral analysis of a factory dump
```

Verified GD32F150 register offsets: `BSRR` +0x18 (set), `BRR` +0x28 (clear),
USART `BAUD` +0x0C, `STAT` +0x1C, `TDATA` +0x28.
