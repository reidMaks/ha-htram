#!/usr/bin/env python3
"""Populate tools/uidesign/fonts/ and regenerate metrics.json + fonts.css.

The candidate faces are system fonts and the repo's generated oblique, so they
are not vendored — run this once after a fresh checkout:

    .venv/bin/python tools/uidesign/fonts.py

Metrics are read out of each TTF rather than guessed: the sandbox positions
text by real ascender/cap-height, which is the only reason a size in the mock
means the same thing once ESPHome compiles the same file into the firmware.
"""
import json
import os
import shutil
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "fonts")

CANDIDATES = {
    "noto": (f"{ROOT}/esphome/fonts/NotoSans-Regular.ttf", "Noto Sans"),
    "notodisplay": ("/usr/share/fonts/truetype/noto/NotoSansDisplay-Regular.ttf", "Noto Sans Display"),
    "notomono": ("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf", "Noto Sans Mono"),
    "notoserif": ("/usr/share/fonts/truetype/noto/NotoSerifDisplay-Regular.ttf", "Noto Serif Display"),
    "dejavulight": (f"{ROOT}/esphome/fonts/DejaVuSans-ExtraLight.ttf", "DejaVu Sans ExtraLight"),
    "dejavucond": ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf", "DejaVu Sans Condensed"),
    "dejavuobl": (f"{ROOT}/esphome/fonts/DejaVuSans-ExtraLight-Oblique.ttf",
                  "DejaVu Sans ExtraLight Oblique"),
}

# The clock face carries metrics clamped to its digits (see tools/fonts/
# make_oblique.py --tight), so it must not inherit the upright face's numbers.
TIGHT = {"dejavuobl"}


def read_metrics(path):
    d = open(path, "rb").read()
    n = struct.unpack(">H", d[4:6])[0]
    tabs = {}
    for i in range(n):
        o = 12 + 16 * i
        tabs[d[o:o + 4].decode("latin1")] = struct.unpack(">II", d[o + 8:o + 16])
    ho, _ = tabs["head"]
    upem = struct.unpack(">H", d[ho + 18:ho + 20])[0]
    hh, _ = tabs["hhea"]
    asc, desc = struct.unpack(">hh", d[hh + 4:hh + 8])
    oo, _ = tabs["OS/2"]
    ver = struct.unpack(">H", d[oo:oo + 2])[0]
    # DejaVu's OS/2 is version 1 and carries no sCapHeight
    cap = struct.unpack(">h", d[oo + 88:oo + 90])[0] if ver >= 2 else 0
    if cap <= 0:
        cap = round(0.70 * upem)
    return {"asc": round(asc / upem, 4), "line": round((asc - desc) / upem, 4),
            "cap": round(cap / upem, 4)}


def main():
    os.makedirs(OUT, exist_ok=True)
    metrics = {}
    for key, (src, family) in CANDIDATES.items():
        if not os.path.exists(src):
            print(f"  skip {key}: {src} not found")
            continue
        dst = os.path.join(OUT, f"{key}.ttf")
        shutil.copy(src, dst)
        m = read_metrics(dst)
        if key in TIGHT:
            # the tight face keeps the upright cap height, its own line box
            m["cap"] = read_metrics(CANDIDATES["dejavulight"][0])["cap"]
        metrics[key] = {"family": family, **m,
                        "kb": round(os.path.getsize(dst) / 1024)}
        print(f"  {key:12} asc={m['asc']} line={m['line']} cap={m['cap']}  {family}")

    json.dump(metrics, open(os.path.join(OUT, "metrics.json"), "w"), indent=2)
    css = "\n".join(f'@font-face{{font-family:"{k}";font-style:normal;font-weight:400;'
                    f'src:url(fonts/{k}.ttf) format("truetype");}}' for k in metrics)
    open(os.path.join(HERE, "fonts.css"), "w").write(css + "\n")
    print(f"\nwrote fonts.css and fonts/metrics.json ({len(metrics)} faces)")
    print("If FONTS in sim.js drifts from metrics.json, copy the values across.")


if __name__ == "__main__":
    main()
