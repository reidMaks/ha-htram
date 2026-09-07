#!/usr/bin/env python3
"""Витягти силует зі знімка екрана й перетворити на SVG.

Знімок — кольорова фігура на строкатому тлі (текст, картки повідомлень). Тло
неоднорідне, тож поріг по яскравості не годиться: беремо піксель, що
достатньо насичений і близький до кольору самої фігури, лишаємо найбільшу
зв'язну область і затягуємо в ній дірки — інакше тонкі внутрішні лінії
розрізають знак на смуги.

Контур обводиться marching squares зі scikit-image і спрощується
Рамером–Дугласом–Пекером: на 40 px від тисячі точок однаково лишиться
десяток, а дрібні сходинки лише роздувають файл.

    .venv/bin/python tools/images/trace_mask.py esphome/images/src/каб.png \
        esphome/images/threat_kab.svg
    .venv/bin/python tools/images/trace_mask.py esphome/images/src/балістика.png \
        esphome/images/threat_ballistic.svg --rotate 45

Знімки-джерела лежать у esphome/images/src/ і не комітяться: це чужа графіка.
Обведені SVG — комітяться.
"""
import argparse

import numpy as np
from PIL import Image
from skimage import measure, morphology
from scipy import ndimage


def figure_mask(im: Image.Image) -> np.ndarray:
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    sat = a.max(2) - a.min(2)
    cand = sat > 40
    if cand.sum() < 50:
        cand = sat > 20
    ref = np.median(a[cand], axis=0)
    m = (np.abs(a - ref).sum(2) < 140) & (sat > 20)

    # найбільша зв'язна область — це знак; решта то текст і рамки навколо
    lab, n = ndimage.label(m)
    if n:
        m = lab == (np.bincount(lab.ravel())[1:].argmax() + 1)
    # зімкнути розриви й залити внутрішні дірки: у вихідних знаках є тонкі
    # світлі лінії, через які силует розпадався на горизонтальні смуги
    m = morphology.closing(m, morphology.disk(2))
    return ndimage.binary_fill_holes(m)


def to_path(m: np.ndarray, grid: int = 100, pad: float = 4.0, eps: float = 0.8) -> str:
    ys, xs = np.nonzero(m)
    crop = m[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = crop.shape
    scale = (grid - 2 * pad) / max(h, w)
    ox = (grid - w * scale) / 2
    oy = (grid - h * scale) / 2

    parts = []
    for c in measure.find_contours(np.pad(crop, 1).astype(float), 0.5):
        c = measure.approximate_polygon(c, tolerance=eps / scale)
        if len(c) < 3:
            continue
        pts = [f"{(x - 1) * scale + ox:.1f} {(y - 1) * scale + oy:.1f}" for y, x in c]
        parts.append("M" + " L".join(pts) + " Z")
    return " ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--rotate", type=float, default=0.0,
                    help="повернути знак на N градусів проти годинникової")
    ap.add_argument("--eps", type=float, default=0.8,
                    help="спрощення контуру в одиницях viewBox")
    args = ap.parse_args()

    m = figure_mask(Image.open(args.src))
    if args.rotate:
        # Довгі вузькі знаки, вписані по висоті, лишають собі кілька пікселів
        # упоперек, і конус неминуче йде сходинками. Поворот дає тій самій
        # формі більше пікселів на ширину — так у вихідному паку намальована
        # крилата, і з тієї ж причини.
        m = np.asarray(
            Image.fromarray((m * 255).astype(np.uint8))
            .rotate(args.rotate, resample=Image.BICUBIC, expand=True)
        ) > 127
    d = to_path(m, eps=args.eps)
    open(args.dst, "w", encoding="utf-8").write(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'width="100" height="100">\n  <path d="' + d + '" fill="#000000"/>\n</svg>\n'
    )
    print(f"{args.dst}: {m.sum()} px фігури, {d.count('M')} контур(и), {len(d)} Б шляху")


if __name__ == "__main__":
    main()
