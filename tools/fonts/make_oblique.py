#!/usr/bin/env python3
"""Bake a synthetic oblique into a TTF.

ESPHome renders fonts from the TTF at build time and LVGL has no shear, so a
slanted clock needs a slanted font file. The shear is taken about the middle of
the cap height rather than the baseline, so digits keep their optical centre
instead of leaning off it — matching what tools/uidesign previews.

Glyphs are then nudged right until no ink sits left of its pen position: LVGL
clips a label to the sum of its advance widths, and a negative bearing on the
first glyph would shave a pixel off it.

    .venv/bin/python tools/fonts/make_oblique.py in.ttf out.ttf --angle 12
"""
import argparse
import math

from fontTools.ttLib import TTFont


def shear_font(src, dst, angle, tight=False):
    font = TTFont(src)
    glyf, hmtx, head = font["glyf"], font["hmtx"], font["head"]
    upem = head.unitsPerEm
    cap = getattr(font["OS/2"], "sCapHeight", None) or round(0.7 * upem)
    k = math.tan(math.radians(angle))
    pivot = cap / 2.0

    def shift(dx):
        for name in glyf.keys():
            g = glyf[name]
            if g.numberOfContours == 0:
                continue
            if g.isComposite():
                for c in g.components:
                    c.x += dx
            else:
                g.coordinates.translate((dx, 0))
            g.recalcBounds(glyf)

    for name in glyf.keys():
        g = glyf[name]
        if g.numberOfContours == 0:
            continue
        if g.isComposite():
            # a shear is linear, so shearing the components' offsets on top of
            # the already-sheared base glyphs gives the right result
            for c in g.components:
                c.x += round(k * c.y)
        else:
            coords = g.coordinates
            for i, (x, y) in enumerate(coords):
                coords[i] = (round(x + k * (y - pivot)), y)
        g.recalcBounds(glyf)

    # How far left of its pen the worst *digit* now reaches. Only the digits
    # matter here — the face is used for the clock and nothing else — and
    # measuring across the whole font would drag in accents that legitimately
    # sit at negative x and pad every glyph by most of an em.
    cmap = font.getBestCmap()
    digits = [cmap[ord(c)] for c in "0123456789" if ord(c) in cmap]
    worst = min(glyf[n].xMin for n in digits if glyf[n].numberOfContours != 0)

    pad = max(0, -worst)
    if pad:
        shift(pad)
        for name in hmtx.metrics:
            adv, lsb = hmtx[name]
            hmtx[name] = (adv + pad if adv else adv, lsb + pad)

    for name in glyf.keys():
        g = glyf[name]
        if g.numberOfContours != 0:
            hmtx[name] = (hmtx[name][0], g.xMin)

    head.xMin = min((glyf[n].xMin for n in glyf.keys()
                     if glyf[n].numberOfContours != 0), default=head.xMin)
    head.xMax = max((glyf[n].xMax for n in glyf.keys()
                     if glyf[n].numberOfContours != 0), default=head.xMax)

    for rec in font["name"].names:
        try:
            val = rec.toUnicode()
        except Exception:
            continue
        if rec.nameID in (1, 3, 4, 6, 16):
            font["name"].setName(val.replace("ExtraLight", "ExtraLight Oblique")
                                 if "Oblique" not in val else val,
                                 rec.nameID, rec.platformID, rec.platEncID, rec.langID)

    if tight:
        # The face renders digits and nothing else, so the line box may hug them.
        # DejaVu's ascender/descender describe a full text face (1.164 em) and
        # LVGL sizes labels from those metrics — at 88 px that is a 102 px box
        # around a 63 px digit, big enough that stacked rows overlap and LVGL
        # merges their invalidations into one repaint.
        ys = [(glyf[n].yMin, glyf[n].yMax) for n in digits
              if glyf[n].numberOfContours != 0]
        top, bot = max(y for _, y in ys), min(y for y, _ in ys)
        font["hhea"].ascent, font["hhea"].descent = top, bot
        font["OS/2"].usWinAscent, font["OS/2"].usWinDescent = top, abs(bot)
        font["OS/2"].sTypoAscender, font["OS/2"].sTypoDescender = top, bot
        font["OS/2"].sTypoLineGap = 0
        print(f"  tight metrics: ascent={top} descent={bot} "
              f"line={(top - bot) / upem:.4f} em")

    font.save(dst)
    widths = {hmtx[n][0] for n in digits}
    print(f"{dst}: angle={angle}° upem={upem} cap={cap} pivot={pivot:.0f} "
          f"pad={pad} ({pad / upem * 88:.1f} px @88) digit_advances={widths}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--angle", type=float, default=12.0)
    ap.add_argument("--tight", action="store_true",
                    help="clamp vertical metrics to the digits")
    a = ap.parse_args()
    shear_font(a.src, a.dst, a.angle, a.tight)
