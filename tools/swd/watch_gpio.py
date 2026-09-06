#!/usr/bin/env python3
"""Attach to whatever firmware is running and trace what it drives.

Plain attach is unreliable on this board, but a session established through
reset survives: the hard part is the handshake, not keeping it. So connect with
the target held in reset, let it go, and then watch the pins live.

Two ways in, tried in order:
  under-reset  -- needs TP18 wired to the probe's reset pin (GP6 on a Pico
                  running debugprobe); fully automatic
  attach       -- hold TP18 to GND by hand until it says otherwise
"""
import time

from pyocd.core.helpers import ConnectHelper

PORTS = (("GPIOA", 0x48000000), ("GPIOB", 0x48000400),
         ("GPIOC", 0x48000800), ("GPIOF", 0x48001400))
MODE = {0: "in", 1: "OUT", 2: "af", 3: "an"}
DEMCR, VC_CORERESET = 0xE000EDFC, 1


def snapshot(t):
    return {n: (t.read32(b), t.read32(b + 0x14), t.read32(b + 0x10)) for n, b in PORTS}


def outputs(snap):
    """Only the pins actually driven, which is what we are comparing."""
    out = {}
    for n, (ctl, octl, _) in snap.items():
        for p in range(16):
            if (ctl >> (2 * p)) & 3 == 1:
                out[f"P{n[-1]}{p}"] = (octl >> p) & 1
    return out


def connect():
    for mode, freq in (("under-reset", 100000), ("under-reset", 10000),
                       ("attach", 100000), ("attach", 10000)):
        try:
            s = ConnectHelper.session_with_chosen_probe(
                target_override="cortex_m",
                options={"connect_mode": mode, "frequency": freq,
                         "resume_on_disconnect": False})
            s.open()
            print(f"[+] {mode} @{freq} Hz", flush=True)
            return s, mode
        except Exception as e:
            print(f"[-] {mode} @{freq}: {str(e)[:50]}", flush=True)
    return None, None


s, mode = connect()
if not s:
    raise SystemExit("не вдалося під'єднатись")

t = s.board.target
if mode == "attach":
    t.write32(DEMCR, t.read32(DEMCR) | VC_CORERESET)
    print(">>> ВІДПУСКАЙ ПІНЦЕТ <<<", flush=True)
    for _ in range(240):
        if str(t.get_state()).endswith("HALTED"):
            break
        time.sleep(0.5)
    t.write32(DEMCR, t.read32(DEMCR) & ~VC_CORERESET)

t.resume()
print("ядро запущено, стежу за пінами (Ctrl-C щоб спинити)\n", flush=True)

prev = None
for i in range(600):
    try:
        cur = outputs(snapshot(t))
    except Exception as e:
        print(f"зрив звʼязку: {str(e)[:50]}", flush=True)
        break
    if cur != prev:
        stamp = time.strftime("%H:%M:%S")
        if prev is None:
            print(f"{stamp}  драйвиться: " +
                  ", ".join(f"{k}={v}" for k, v in sorted(cur.items())), flush=True)
        else:
            for k in sorted(set(cur) | set(prev)):
                a, b = prev.get(k), cur.get(k)
                if a != b:
                    print(f"{stamp}  {k}: {a} -> {b}", flush=True)
        prev = cur
    time.sleep(2)

s.close()
