#!/usr/bin/env python3
"""Read the battery over SWD while the ESP32 rail is powered down.

The battery ADC lives on the GD32, but its value normally reaches us through
the ESP32 over Wi-Fi -- so cutting the ESP to measure its consumption would
also blind the measurement. SWD sidesteps that: we trigger the conversion and
read ADC_RDATA ourselves, with the firmware still running.

    .venv/bin/python tools/swd/power_probe.py --off-minutes 15

PF7 gates the ESP32 *and* the display panel, so the screen goes dark for the
duration. It is restored in a finally block.
"""
import argparse
import time

ADC_BASE = 0x40012400
ADC_STAT, ADC_CTL1, ADC_RSQ0, ADC_RSQ2, ADC_RDATA = (
    ADC_BASE + 0x00, ADC_BASE + 0x08, ADC_BASE + 0x2C, ADC_BASE + 0x34, ADC_BASE + 0x4C)
GPIOF_BASE = 0x48001400
GPIOF_BOP, GPIOF_BC = GPIOF_BASE + 0x18, GPIOF_BASE + 0x14
PF7 = 1 << 7


def read_mv(t):
    """Same sequence periph_read_battery() uses, driven from the debugger."""
    t.write32(ADC_STAT, 0)
    t.write32(ADC_RSQ0, 0)
    t.write32(ADC_RSQ2, 9)
    t.write32(ADC_CTL1, t.read32(ADC_CTL1) | (1 << 22))  # SWRCST
    for _ in range(200):
        if t.read32(ADC_STAT) & (1 << 1):  # EOC
            raw = t.read32(ADC_RDATA) & 0xFFFF
            return raw, (raw * 3275) >> 11
        time.sleep(0.005)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--off-minutes", type=float, default=15.0)
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--baseline", type=float, default=120.0)
    a = ap.parse_args()

    from pyocd.core.helpers import ConnectHelper
    s = ConnectHelper.session_with_chosen_probe(
        target_override="cortex_m",
        options={"connect_mode": "attach", "frequency": 1000000})
    s.open()
    t = s.board.target  # left running on purpose: we want normal board behaviour

    def sample(tag):
        raw, mv = read_mv(t)
        print(f"{time.strftime('%H:%M:%S')}  {tag:9} raw={raw} mv={mv}", flush=True)
        return mv

    try:
        print(f"# baseline {a.baseline:.0f}s, ESP running")
        end = time.time() + a.baseline
        base = []
        while time.time() < end:
            base.append(sample("baseline"))
            time.sleep(a.interval)

        print(f"\n# PF7 low -- ESP32 and panel off for {a.off_minutes:.0f} min")
        t.write32(GPIOF_BC, PF7)
        off = []
        end = time.time() + a.off_minutes * 60
        while time.time() < end:
            off.append(sample("esp-off"))
            time.sleep(a.interval)

        base = [v for v in base if v]
        off = [v for v in off if v]
        if base and off:
            print(f"\nbaseline mean {sum(base)/len(base):.0f} mV")
            print(f"esp-off  mean {sum(off)/len(off):.0f} mV  "
                  f"({off[0]} -> {off[-1]}, {off[-1]-off[0]:+d} over the window)")
            print(f"delta {sum(off)/len(off) - sum(base)/len(base):+.0f} mV")
    finally:
        t.write32(GPIOF_BOP, PF7)
        print("\n# PF7 restored")
        s.close()


if __name__ == "__main__":
    main()
