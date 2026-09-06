# Display design studio

A browser mock of the HTRAM screen, so the 240×240 UI can be argued about
**before** anything is written into [`esphome/htram.yaml`](../../esphome/htram.yaml).
Nothing here ships to the device.

```bash
python3 tools/uidesign/serve.py 8099
```

Then open <http://localhost:8099>.

## The iteration loop

`layouts.json` is the only file you edit. The page polls it once a second, so
saving in your editor re-renders the mock immediately — no reload, no rebuild,
no flashing. The same spec can also be edited in the box on the right of the
page and applied with **apply** / saved with **download**.

## What the mock gets right

* **True physical scale.** The device is drawn at 80 mm with a 65 mm black
  glass and the real 27 mm panel in the middle. **Calibrate 1:1** starts from
  the browser's own millimetre (CSS `mm` is pinned to 96 dpi, so it lands within
  ~20% on most screens) and shows an ISO/IEC 7810 ID-1 outline — 85.60 ×
  53.98 mm, the size of every bank card. Hold a card against the screen, nudge
  until they match, and the setting sticks. Only then is **1:1** genuinely 1:1,
  which is the only honest way to judge whether a 12 px label is readable.

  Expect the panel to look tiny: at 96 dpi, 27 mm is about 102 screen pixels.
  That is the point — it is a 27 mm panel.
* **Real font metrics.** Text is rendered with the very same
  `NotoSans-Regular.ttf` that ESPHome compiles into the firmware, at the same
  pixel sizes, using the ascender/line-height read out of the TTF. A width
  measured here is the width you get on the panel, ±1 px.
* **LVGL semantics.** `align` + `x`/`y` behave like `lv_obj_align()`, and arc
  angles use the LVGL convention (0° = 3 o'clock, growing clockwise), so the
  numbers in `layouts.json` transfer into the YAML unchanged.
* **UART cost.** Every widget's bounding box is priced as a `CMD_DRAW_RECT`
  frame at 115200 baud. This is the pessimistic bound — LVGL invalidates only
  the part of a widget that actually changed — but it is the number that tells
  you when a design is too expensive to redraw.

**reference** puts a photo of the factory screen beside the mock, scaled to the
same 80 mm body width, so the two are directly comparable. The photo is not in
the repository -- the vendor's product shot is theirs, not ours -- so drop your
own into `reference.webp` next to this file; it is gitignored. Without it the
button just reports that the file is missing. **contact sheet** renders every variant at 2× into `shots/`.

## Spec reference

Top level: `meta`, `palette` (`co2` bands with `color`/`en`/`ua`, plus named
colours), and `variants`.

Each widget takes `align` (`top_left` … `bottom_right`, default `top_left`),
`x`, `y`, and `opa`:

| type | fields |
| --- | --- |
| `label` | `font` (px), `color`, `text`, `spacing` |
| `rect` | `w`, `h`, `radius`, `color`, `border: {w, color}` |
| `arc` | `d`, `width`, `start`, `end`, `color`, `track`, `value`, `rounded` |
| `line` | `points: [[x,y],…]` **or** `series: "co2"` + `box: [x,y,w,h]`, `width`, `color` |
| `icon` | `name`: `wifi` `bt` `batt` `drop` `thermo`, `size`, `color` |

Colours are hex, or `$name` into `palette` — `$co2` resolves through the
current CO₂ band. Numbers accept `$co2pct` / `$battpct` for arc values.

Text placeholders: `{co2}` `{status}` `{status_en}` `{status_ua}` `{temp}`
`{temp0}` `{hum}` `{batt_pct}` `{batt_v}` `{power}` `{clock}` `{date}`.

## Files

* `index.html` — the studio: device mock, live state, overlays, cost table
* `sim.js` — spec → canvas renderer
* `layouts.json` — **the design**, the file you iterate on
* `serve.py` — static server plus `POST /save`, which backs the
  *contact sheet* button (writes into `shots/`)
* `font.css` — `NotoSans-Regular.ttf` inlined so the page needs no network
