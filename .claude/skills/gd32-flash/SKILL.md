---
name: gd32-flash
description: Build and flash the GD32F150 firmware, over OTA through the ESP32 or over SWD, and verify the result by CRC. Use whenever firmware/gd32/ changes and has to reach the device. Trigger terms (uk) - залий прошивку, заливай, прошити GD32, перепрошити, OTA прошивки, збери прошивку, flash gd32, зібрати і залити.
---

# Flashing the GD32

## The rule that comes before everything else

**Never put a timeout on a flash-write operation. Run it backgrounded instead.**

A `timeout 180` once killed a write mid-flight. The GD32 was left with a
half-written image, stopped raising `PB3`/`PF7`, and so cut power to the ESP32 --
which is the only wireless path back in. Recovering it needed tweezers on the
reset pin. A write that takes longer than you expected is not a hang; let it
finish.

In practice: `Bash` with `run_in_background: true`, then read the task output.

## Build

```bash
cd firmware/gd32 && make
```

Produces `build/gd32_firmware.bin` and prints the section sizes. The build
stamps `BUILD_EPOCH`, the short commit hash and a dirty-tree flag into the HELLO
packet, so a rebuild always changes the binary even when the sources did not.

Compare the byte count against the last known-good one (9940 bytes as of
2026-09-06). A sudden jump means something was linked in that you did not
intend.

## Flash: two paths

**OTA through the ESP32** -- the normal path, no wires:

```bash
.venv/bin/python tools/swd/flash.py --ota 192.168.0.78
```

The ESP stages the image, checks it, and drives the GD32's bootloader. Requires
a working ESP, which requires a working GD32 to power it -- see the warning
above about why that pairing matters.

**SWD** -- when OTA cannot be used, needs the Pico debugprobe on the bench:

```bash
.venv/bin/python tools/swd/flash.py
```

**Factory image restore** (`tools/swd/gd32_flash.bin`, gitignored, never commit
it):

```bash
.venv/bin/python tools/swd/flash.py --factory
```

## Verify, do not assume

The tool prints the host-side CRC before sending and the device's own
`staged_crc` after. They must match, and `bytes_written` must equal the image
size. A successful HTTP response alone proves nothing about what landed.

Then confirm the device came back: the GD32 sends a HELLO packet carrying its
build epoch and git hash, visible in the ESP logs. If HELLO does not arrive,
the image is running badly even though the write reported success.
