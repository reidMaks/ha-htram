---
name: gd32-rescue
description: Recover a GD32 whose SWD port answers intermittently or not at all, usually after an interrupted flash write, using the NRST test point and rescue_under_reset.py. Trigger terms (uk) - не бачить SWD, SWD відвалюється, No ACK, цеглина, окирпичив, GD32 не відповідає, підняти після обірваної заливки, врятувати.
---

# Rescuing a GD32 over SWD

## Check this first

**Common ground.** The Pico and the board must share a ground wire. Missing
ground presents exactly as flaky SWD, and once cost two days of chasing the
wrong cause. Ring it out before theorising about anything else.

**Power-on order.** The board must already be powered before the Pico's USB
goes in -- see the `htram-bench` skill. A parasitically powered board also
gives unreliable SWD, along with a screeching buzzer.

## Symptom and cause

`pyocd` drops the connection on `No ACK`; sometimes it gets as far as reading
the AHB-AP ROM table and dies there. Lowering the clock does not help.

After an interrupted flash the GD32 executes garbage from the incomplete image,
and that garbage reconfigures `PA13`/`PA14` from SWD into GPIO. The debug port
is therefore alive for only a few milliseconds after reset -- which is exactly
the window this procedure exploits.

## The procedure

`NRST` is pin 7 of the LQFP48, exposed on the board as **`TP18`**. The stock
debugprobe firmware on a bare Pico does not bring the reset line out, so the
pad is held down by hand.

```bash
.venv/bin/python tools/swd/rescue_under_reset.py [image]
```

1. Hold `TP18` against `GND` -- tweezers or a jumper.
2. Start the script. It retries the connection while the target sits in reset.
3. On `>>> ВІДПУСКАЙ ПІНЦЕТ <<<`, lift the contact. The script has already armed
   `VC_CORERESET`, so the core halts on the reset vector without executing a
   single instruction, and SWD stays alive.
4. The write runs **without a timeout**. Verified on hardware 2026-09-06:
   9892 bytes in 17.9 s, CRC matched.

Do not interrupt step 4 for any reason. An interrupted rescue write puts you
back at the start, with less battery than you had.
