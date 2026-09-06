#!/usr/bin/env python3
"""Recover a GD32 whose flash content kills the debug port.

Garbage in a half-written flash can reconfigure PA13/PA14 and switch SWD off,
leaving only a few milliseconds after reset in which the DAP answers. Holding
NRST (TP18) low removes the window entirely: the core executes nothing, so the
pins keep their debug function.

Procedure -- hold TP18 to GND, run this, release when told:
  1. connect while reset is held
  2. arm VC_CORERESET so the core halts on the reset vector
  3. you release NRST; the core comes out of reset and stops immediately
  4. flash the image, with no timeout anywhere
"""
import os
import subprocess
import sys
import time

from pyocd.core.helpers import ConnectHelper

DEMCR = 0xE000EDFC
VC_CORERESET = 1 << 0
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
IMAGE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "rescue_image.bin")
FREQS = (100000, 10000)


def try_connect():
    for f in FREQS:
        try:
            s = ConnectHelper.session_with_chosen_probe(
                target_override="cortex_m",
                options={"connect_mode": "attach", "frequency": f,
                         "resume_on_disconnect": False})
            s.open()
            return s, f
        except Exception:
            pass
    return None, None


print("тримай TP18 на землі...", flush=True)
session = None
deadline = time.time() + 120
while time.time() < deadline:
    session, freq = try_connect()
    if session:
        break
    time.sleep(1.5)

if not session:
    print("не вдалося під'єднатись за 120 с", flush=True)
    sys.exit(1)

t = session.board.target
print(f"DAP відповів @{freq} Hz, стан={t.get_state()}", flush=True)
t.write32(DEMCR, t.read32(DEMCR) | VC_CORERESET)
print(">>> ВІДПУСКАЙ ПІНЦЕТ <<<", flush=True)

deadline = time.time() + 120
halted = False
while time.time() < deadline:
    try:
        if str(t.get_state()).endswith("HALTED"):
            halted = True
            break
    except Exception:
        pass
    time.sleep(0.5)

print(f"ядро зупинено на векторі скидання: {halted}", flush=True)
session.close()

print("заливаю (без таймауту)...", flush=True)
r = subprocess.run([sys.executable, os.path.join(HERE, "flash.py"), IMAGE, "--swd-mem"],
                   cwd=ROOT, capture_output=True, text=True)
print(r.stdout[-1500:], flush=True)
print(r.stderr[-500:], flush=True)
