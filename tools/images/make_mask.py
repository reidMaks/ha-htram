#!/usr/bin/env python3
"""Turn an SVG into the 1-bit mask the firmware embeds.

ESPHome hands a `type: BINARY` image to LVGL as LV_COLOR_FORMAT_A1 — a
silhouette with no colour of its own, tinted at runtime by image_recolor. That
is what makes the emblem cost 2 KB of flash instead of the 32 KB the same
picture would take in RGB565, and it is why the colour lives in htram.yaml
rather than in the file.

Two details this script exists to record, because both cost an attempt:

  * In the source SVG the trident is the *dark* shape and the shield field is
    light. Keying on alpha therefore fills the whole shield — the mask has to
    come from luminance, inverted.
  * ESPHome's binary encoder converts to mode "1" by brightness unless the
    image is alpha-only, so the mask is written as plain greyscale: emblem
    white, everything else black.

    .venv/bin/python tools/images/make_mask.py \
        esphome/images/Lesser_Coat_of_Arms_of_Ukraine_(bw).svg \
        esphome/images/tryzub_mask.png --height 150
"""
import argparse
import io

import cairosvg
from PIL import Image


def build(src: str, height: int, supersample: int = 4) -> Image.Image:
    # Rendered large and reduced afterwards: thresholding a small render eats
    # the thin shield outline, which at 150 px is barely two pixels wide.
    png = cairosvg.svg2png(url=src, output_height=height * supersample)
    big = Image.open(io.BytesIO(png)).convert("RGBA")

    opaque = big.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    dark = big.convert("L").point(lambda v: 255 if v < 128 else 0)
    mask = Image.composite(dark, Image.new("L", big.size, 0), opaque)

    mask = mask.crop(mask.getbbox())
    width = round(mask.size[0] * height / mask.size[1])
    return mask.resize((width, height), Image.LANCZOS).point(
        lambda v: 255 if v > 110 else 0
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--height", type=int, default=150)
    args = ap.parse_args()

    mask = build(args.src, args.height)
    mask.save(args.dst)
    w, h = mask.size
    print(f"{args.dst}: {w}x{h}, {(w * h + 7) // 8} bytes as 1 bit")


if __name__ == "__main__":
    main()
