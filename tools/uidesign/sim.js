/* HTRAM display design studio — renders a layouts.json spec onto a 240x240
   canvas at true physical scale. Nothing here is firmware; it exists so the
   layout can be argued about before anyone touches htram.yaml. */

const PX_PER_MM = 240 / 27;      // 8.889 — the whole reason this tool exists
const W = 240, H = 240;

// Metrics read straight out of each TTF (see fonts/metrics.json), so a font
// size here means the same thing it will mean once ESPHome compiles that same
// file into the firmware. All six carry Cyrillic, so the slot line works in any.
const FONTS = {
  noto: { asc: 1.069, line: 1.362, cap: 0.714, name: 'Noto Sans' },
  notodisplay: { asc: 1.069, line: 1.362, cap: 0.714, name: 'Noto Sans Display' },
  notomono: { asc: 1.069, line: 1.362, cap: 0.714, name: 'Noto Sans Mono' },
  notoserif: { asc: 1.069, line: 1.362, cap: 0.714, name: 'Noto Serif Display' },
  dejavulight: { asc: 0.9282, line: 1.1641, cap: 0.6997, name: 'DejaVu Sans ExtraLight' },
  dejavucond: { asc: 0.9282, line: 1.1641, cap: 0.6997, name: 'DejaVu Sans Condensed' },
  dejavuobl: { asc: 0.9282, line: 1.1641, cap: 0.6997, name: 'DejaVu Sans ExtraLight Oblique' }
};
const DEFAULT_FAMILY = 'noto';
const fontOf = w => FONTS[w && w.family] || FONTS[DEFAULT_FAMILY];

const state = {
  co2: 700, temp: 23.3, hum: 54, batt_pct: 62, batt_mv: 3810,
  outdoor: 12, seconds: 41, slot: 0,
  link: true, weather: 'clear',
  usb: true, charging: false, lang: 'ua', clock: null,
};

let spec = null, variant = null;
let overlays = { grid: false, boxes: false, cross: false, circle: false };

// The content circle. The panel is square, but the eye's frame is the 65 mm
// black glass around it — anything reaching into the square's corners announces
// that there is a rectangle there and breaks the floating illusion.
const SAFE_D = 206;
let boxes = [];   // bounding boxes collected during the last render, for the cost table

/* ---------- derived values ---------- */

function co2Band() {
  const bands = spec.palette.co2;
  return bands.find(b => state.co2 < b.max) || bands[bands.length - 1];
}
function co2Pct() {
  const v = Math.max(400, Math.min(2000, state.co2));
  return (v - 400) / 1600;
}
function nowParts() {
  const d = state.clock ? new Date(state.clock) : new Date();
  const p = n => String(n).padStart(2, '0');
  const days = ['Нд', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
  const mon = ['січня','лютого','березня','квітня','травня','червня',
               'липня','серпня','вересня','жовтня','листопада','грудня'];
  // The full month does not fit: "Пн, 22 листопада" is 138 px at 15 px, and the
  // content circle only offers 117 px at the slot's baseline. Three letters.
  const monS = ['січ','лют','бер','кві','тра','чер',
                'лип','сер','вер','жов','лис','гру'];
  return {
    hh: p(d.getHours()),
    mm: p(d.getMinutes()),
    clock: `${p(d.getHours())}:${p(d.getMinutes())}`,
    date: `${days[d.getDay()]}, ${d.getDate()} ${monS[d.getMonth()]}`,
    date_full: `${days[d.getDay()]}, ${d.getDate()} ${mon[d.getMonth()]}`,
  };
}

// Widgets can be conditional. The face is meant to be empty when everything is
// fine, so most indicators carry a `when` and simply are not drawn otherwise.
const PREDICATES = {
  link:    () => state.link,
  usb:     () => state.usb,
  battlow: () => state.batt_pct < 20,
  weather: () => state.weather !== 'clear',
  slot0:   () => state.slot === 0,
  slot1:   () => state.slot === 1,
  slot2:   () => state.slot === 2,
  // the outdoor slot while there is actually weather worth a glyph
  wx1:     () => state.weather !== 'clear' && state.slot === 1,
};

function visible(wdg) {
  if (!wdg.when) return true;
  return wdg.when.split('&&').every(term => {
    term = term.trim();
    const neg = term.startsWith('!');
    const fn = PREDICATES[neg ? term.slice(1) : term];
    if (!fn) return true;
    return neg ? !fn() : fn();
  });
}

function resolveColor(c) {
  if (typeof c !== 'string') return '#ffffff';
  if (!c.startsWith('$')) return c;
  const key = c.slice(1);
  if (key === 'co2') return co2Band().color;
  if (key === 'battcolor') return state.batt_pct < 20 ? spec.palette.warn : spec.palette.dim;
  return spec.palette[key] || '#ffffff';
}

function resolveText(t) {
  const b = co2Band(), tm = nowParts();
  const power = state.usb ? (state.charging ? '[CHRG]' : '[USB]') : '';
  const map = {
    hh: tm.hh,
    mm: tm.mm,
    ss: String(state.seconds).padStart(2, '0'),
    out: state.outdoor.toFixed(0),
    wxword: { clear: 'надворі', rain: 'дощ', snow: 'сніг', wind: 'вітер' }[state.weather],
    out1: state.outdoor.toFixed(1),
    co2: String(Math.round(state.co2)),
    status: state.lang === 'ua' ? b.ua : b.en,
    status_en: b.en,
    status_ua: b.ua,
    temp: state.temp.toFixed(1),
    temp0: Math.round(state.temp),
    hum: String(Math.round(state.hum)),
    batt_pct: String(Math.round(state.batt_pct)),
    batt_v: (state.batt_mv / 1000).toFixed(2),
    power,
    clock: tm.clock,
    date: tm.date,
    date_full: tm.date_full,
  };
  return String(t).replace(/\{(\w+)\}/g, (_, k) => (k in map ? map[k] : `{${k}}`));
}

function resolveNum(v) {
  if (typeof v === 'number') return v;
  if (v === '$co2pct') return co2Pct();
  if (v === '$battpct') return state.batt_pct / 100;
  if (v === '$secpct') return state.seconds / 60;
  return 0;
}

/* ---------- layout ---------- */

const ANCHORS = {
  top_left: [0, 0], top_mid: [.5, 0], top_right: [1, 0],
  left_mid: [0, .5], center: [.5, .5], right_mid: [1, .5],
  bottom_left: [0, 1], bottom_mid: [.5, 1], bottom_right: [1, 1],
};

// Mirrors lv_obj_align(): the anchor positions the widget box inside the
// 240x240 parent, then x/y nudge it.
function place(align, w, h, x = 0, y = 0) {
  const a = ANCHORS[align || 'top_left'];
  return [Math.round(a[0] * (W - w) + x), Math.round(a[1] * (H - h) + y)];
}

/* ---------- drawing ---------- */

function font(ctx, wdg, size) {
  ctx.font = `${size}px ${(wdg && wdg.family) || DEFAULT_FAMILY}, sans-serif`;
  ctx.letterSpacing = `${(wdg && wdg.spacing) || 0}px`;
}

function drawLabel(ctx, wdg) {
  const size = wdg.font || 16;
  const f = fontOf(wdg);
  font(ctx, wdg, size);
  const text = resolveText(wdg.cycle ? wdg.cycle[state.slot % wdg.cycle.length] : (wdg.text ?? ''));
  const w = Math.ceil(ctx.measureText(text).width);
  const h = Math.round(size * f.line);
  // `ink: true` means y addresses the top of the glyphs rather than the top of
  // the line box — the only way to compare fonts with different metrics in the
  // same slot without every one of them sitting at a different height.
  const dy = wdg.ink ? -Math.round((f.asc - f.cap) * size) : 0;
  const [x, y] = place(wdg.align, w, h, wdg.x || 0, (wdg.y || 0) + dy);
  ctx.globalAlpha = wdg.opa ?? 1;
  ctx.fillStyle = resolveColor(wdg.color || '$fg');
  ctx.textBaseline = 'alphabetic';
  const baseY = y + size * f.asc;
  if (wdg.slant) {
    // Synthetic oblique, sheared about the middle of the cap height so the
    // block keeps its optical centre instead of leaning off it.
    const t = Math.tan(wdg.slant * Math.PI / 180);
    const pivot = baseY - (f.cap * size) / 2;
    ctx.save();
    ctx.transform(1, 0, -t, 1, t * pivot, 0);
    ctx.fillText(text, x, baseY);
    ctx.restore();
    const grow = Math.ceil(t * f.cap * size / 2);
    ctx.globalAlpha = 1;
    return [x - grow, y, w + 2 * grow, h];
  }
  ctx.fillText(text, x, baseY);
  ctx.globalAlpha = 1;
  return [x, y, w, h];
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r || 0, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawRect(ctx, wdg) {
  const w = wdg.w, h = wdg.h;
  const [x, y] = place(wdg.align, w, h, wdg.x || 0, wdg.y || 0);
  ctx.globalAlpha = wdg.opa ?? 1;
  if (wdg.color) { ctx.fillStyle = resolveColor(wdg.color); roundRect(ctx, x, y, w, h, wdg.radius); ctx.fill(); }
  if (wdg.border) {
    ctx.strokeStyle = resolveColor(wdg.border.color);
    ctx.lineWidth = wdg.border.w || 1;
    roundRect(ctx, x + .5, y + .5, w - 1, h - 1, wdg.radius);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  return [x, y, w, h];
}

// LVGL arc angles: 0 deg is 3 o'clock, growing clockwise — same convention
// canvas uses, so the numbers here transfer to the YAML unchanged.
function drawArc(ctx, wdg) {
  const d = wdg.d, r = (d - wdg.width) / 2;
  const [x, y] = place(wdg.align, d, d, wdg.x || 0, wdg.y || 0);
  const cx = x + d / 2, cy = y + d / 2;
  let s = wdg.start, e = wdg.end;
  if (e <= s) e += 360;
  const rad = a => a * Math.PI / 180;

  ctx.lineWidth = wdg.width;
  ctx.lineCap = wdg.rounded === false ? 'butt' : 'round';
  ctx.globalAlpha = wdg.opa ?? 1;
  if (wdg.track) {
    ctx.strokeStyle = resolveColor(wdg.track);
    ctx.beginPath(); ctx.arc(cx, cy, r, rad(s), rad(e)); ctx.stroke();
  }
  const frac = Math.max(0, Math.min(1, resolveNum(wdg.value ?? 1)));
  if (frac > 0.001) {
    ctx.strokeStyle = resolveColor(wdg.color);
    ctx.beginPath(); ctx.arc(cx, cy, r, rad(s), rad(s + (e - s) * frac)); ctx.stroke();
  }
  ctx.lineCap = 'butt';
  ctx.globalAlpha = 1;
  return [x, y, d, d];
}

// Deterministic fake history so the trend line does not jitter between renders.
function series(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const wobble = Math.sin(i * 1.7) * 60 + Math.sin(i * 0.6) * 110;
    out.push(state.co2 - (1 - t) * 220 + wobble * (0.3 + 0.7 * t));
  }
  out[n - 1] = state.co2;
  return out;
}

function drawLine(ctx, wdg) {
  let pts = wdg.points;
  const [bx, by, bw, bh] = wdg.box || [0, 0, W, H];
  if (wdg.series) {
    const n = wdg.n || 17, vals = series(n);
    pts = vals.map((v, i) => {
      const c = Math.max(400, Math.min(2000, v));
      return [bx + (i / (n - 1)) * bw, by + bh - ((c - 400) / 1600) * bh];
    });
  }
  if (!pts || pts.length < 2) return null;
  const col = resolveColor(wdg.color);
  if (wdg.fill) {
    ctx.globalAlpha = wdg.fill_opa ?? 0.15;
    ctx.fillStyle = col;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.lineTo(pts[pts.length - 1][0], by + bh);
    ctx.lineTo(pts[0][0], by + bh);
    ctx.closePath();
    ctx.fill();
  }
  ctx.globalAlpha = wdg.opa ?? 1;
  ctx.strokeStyle = col;
  ctx.lineWidth = wdg.width || 2;
  ctx.lineJoin = ctx.lineCap = 'round';
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.stroke();
  ctx.globalAlpha = 1;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), pad = (wdg.width || 2);
  return [Math.min(...xs) - pad, Math.min(...ys) - pad,
          Math.max(...xs) - Math.min(...xs) + 2 * pad, Math.max(...ys) - Math.min(...ys) + 2 * pad];
}

// Stand-ins for the LV_SYMBOL_* glyphs that ship inside the montserrat fonts.
function drawIcon(ctx, wdg) {
  const s = wdg.size || 16;
  const name = wdg.name === '$weather' ? state.weather : wdg.name;
  const [x, y] = place(wdg.align, s, s, wdg.x || 0, wdg.y || 0);
  const c = resolveColor(wdg.color || '$dim');
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = ctx.strokeStyle = c;
  ctx.lineWidth = Math.max(1, s / 10);
  ctx.lineCap = 'round';

  if (name === 'wifi_off') {
    for (let i = 0; i < 2; i++) {
      ctx.beginPath();
      ctx.arc(s / 2, s * .82, s * (.18 + i * .22), Math.PI * 1.3, Math.PI * 1.7);
      ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(s / 2, s * .8, s * .09, 0, 7); ctx.fill();
    const keep = ctx.strokeStyle;
    ctx.strokeStyle = '#000'; ctx.lineWidth = Math.max(3, s / 4);
    ctx.beginPath(); ctx.moveTo(s * .1, s * .95); ctx.lineTo(s * .9, s * .08); ctx.stroke();
    ctx.strokeStyle = keep; ctx.lineWidth = Math.max(1.5, s / 8);
    ctx.beginPath(); ctx.moveTo(s * .14, s * .9); ctx.lineTo(s * .86, s * .13); ctx.stroke();
  } else if (name === 'rain') {
    ctx.lineWidth = Math.max(1.5, s / 8);
    for (let i = 0; i < 3; i++) {
      const x = s * (.2 + i * .3);
      ctx.beginPath(); ctx.moveTo(x + s * .16, s * .12); ctx.lineTo(x - s * .04, s * .88); ctx.stroke();
    }
  } else if (name === 'snow') {
    ctx.lineWidth = Math.max(1.5, s / 9);
    for (let i = 0; i < 3; i++) {
      const a = (i * 60) * Math.PI / 180, r = s * .42;
      ctx.beginPath();
      ctx.moveTo(s / 2 - Math.cos(a) * r, s / 2 - Math.sin(a) * r);
      ctx.lineTo(s / 2 + Math.cos(a) * r, s / 2 + Math.sin(a) * r);
      ctx.stroke();
    }
  } else if (name === 'wind') {
    ctx.lineWidth = Math.max(1.5, s / 8);
    [[.26, .78], [.5, .94], [.74, .6]].forEach(([y, w]) => {
      ctx.beginPath();
      ctx.moveTo(s * .06, s * y); ctx.lineTo(s * (.06 + w), s * y);
      ctx.stroke();
    });
  } else if (name === 'wifi') {
    for (let i = 0; i < 3; i++) {
      ctx.beginPath();
      ctx.arc(s / 2, s * .82, s * (.18 + i * .22), Math.PI * 1.25, Math.PI * 1.75);
      ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(s / 2, s * .8, s * .07, 0, 7); ctx.fill();
  } else if (name === 'batt') {
    const bw = s, bh = s * .55, ty = (s - bh) / 2;
    ctx.strokeRect(.5, ty + .5, bw - s * .12 - 1, bh - 1);
    ctx.fillRect(bw - s * .1, ty + bh * .3, s * .1, bh * .4);
    const fill = (bw - s * .12 - 3) * Math.max(0, Math.min(1, state.batt_pct / 100));
    ctx.fillRect(1.5, ty + 1.5, fill, bh - 3);
    if (state.charging) {
      ctx.fillStyle = '#000';
      ctx.beginPath();
      ctx.moveTo(s * .52, ty + 1); ctx.lineTo(s * .34, ty + bh * .58);
      ctx.lineTo(s * .46, ty + bh * .58); ctx.lineTo(s * .36, ty + bh - 1);
      ctx.lineTo(s * .60, ty + bh * .42); ctx.lineTo(s * .46, ty + bh * .42);
      ctx.closePath(); ctx.fill();
    }
  } else if (name === 'bt') {
    ctx.beginPath();
    ctx.moveTo(s * .3, s * .3); ctx.lineTo(s * .7, s * .7);
    ctx.lineTo(s * .5, s * .88); ctx.lineTo(s * .5, s * .12);
    ctx.lineTo(s * .7, s * .3); ctx.lineTo(s * .3, s * .7);
    ctx.stroke();
  } else if (name === 'drop') {
    ctx.beginPath();
    ctx.moveTo(s / 2, s * .1);
    ctx.bezierCurveTo(s * .95, s * .55, s * .8, s * .95, s / 2, s * .95);
    ctx.bezierCurveTo(s * .2, s * .95, s * .05, s * .55, s / 2, s * .1);
    ctx.fill();
  } else if (name === 'thermo') {
    ctx.beginPath(); ctx.arc(s / 2, s * .78, s * .2, 0, 7); ctx.fill();
    ctx.lineWidth = s * .16;
    ctx.beginPath(); ctx.moveTo(s / 2, s * .15); ctx.lineTo(s / 2, s * .7); ctx.stroke();
  }
  ctx.restore();
  return [x, y, s, s];
}

const DRAW = { label: drawLabel, rect: drawRect, arc: drawArc, line: drawLine, icon: drawIcon };

function render(ctx) {
  boxes = [];
  ctx.fillStyle = variant.bg || '#000000';
  ctx.fillRect(0, 0, W, H);

  for (const wdg of variant.widgets) {
    const fn = DRAW[wdg.type];
    if (!fn) continue;
    if (wdg.blink && state.seconds % 2) continue;
    if (!visible(wdg)) continue;
    const box = fn(ctx, wdg);
    if (box) boxes.push({ wdg, box });
  }

  if (overlays.grid) {
    ctx.strokeStyle = 'rgba(0,180,255,.22)'; ctx.lineWidth = 1;
    for (let i = 20; i < W; i += 20) {
      ctx.beginPath(); ctx.moveTo(i + .5, 0); ctx.lineTo(i + .5, H); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, i + .5); ctx.lineTo(W, i + .5); ctx.stroke();
    }
  }
  if (overlays.boxes) {
    ctx.strokeStyle = 'rgba(255,64,129,.85)'; ctx.lineWidth = 1;
    for (const b of boxes) ctx.strokeRect(b.box[0] + .5, b.box[1] + .5, b.box[2] - 1, b.box[3] - 1);
  }
  if (overlays.circle) {
    ctx.strokeStyle = 'rgba(255,193,7,.5)'; ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.arc(120, 120, SAFE_D / 2, 0, 7); ctx.stroke();
    ctx.setLineDash([]);
  }
  if (overlays.cross) {
    ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(120.5, 0); ctx.lineTo(120.5, H); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, 120.5); ctx.lineTo(W, 120.5); ctx.stroke();
  }
}

/* ---------- UART cost ----------
   Every widget that changes has to travel to the GD32 as a DRAW_RECT frame:
   11 bytes of header/CRC plus 2 bytes per pixel, at 115200 8N1 = 11520 B/s. */

const HEADER = 11, BAUD_BPS = 11520;
function cost(w, h) {
  const bytes = w * h * 2 + HEADER;
  return { bytes, ms: (bytes / BAUD_BPS) * 1000 };
}

// A widget's bounding box is the pessimistic bound. Where LVGL invalidates
// something much smaller — a seconds arc creeping one sector at a time — the
// spec can declare the real per-update box as `cost: {w, h, note}`.
function widgetCost(wdg, box) {
  const c = wdg.cost;
  return c ? { ...cost(c.w, c.h), note: c.note } : cost(box[2], box[3]);
}
