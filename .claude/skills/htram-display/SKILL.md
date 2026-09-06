---
name: htram-display
description: The ST7789 panel and the on-screen UI - bit-banged SPI pins, hardware rotation via MADCTL, the cost of repaints over the UART link, and the browser sandbox for iterating layouts without touching firmware. Trigger terms (uk) - екран, дисплей, малює, перевернутий, орієнтація, годинник, шрифт, макет, дизайн екрана, пісочниця, підсвітка, гальмує екран.
---

# The display

## Panel and pins

ST7789, 240x240, driven from the GD32 by bit-banged 3-wire 9-bit SPI (the D/C
bit is the 9th bit, there is no separate D/C line):

`PB12` RES, `PB13` SCK, `PB14` CS, `PB15` SDA, `PB8` backlight
(TIMER15_CH0, AF2, PWM brightness, HIGH = on).

## Rotation lives in hardware

The panel is mounted upside down. The 180-degree turn is done once in the
ST7789's `MADCTL` register -- `LCD_MADCTL 0xC0` (MY|MX) in
`firmware/gd32/src/display.c` -- not in LVGL. That way the GD32's own drawing
(boot screen, standby charge indicator) comes out upright too, and the ESP
stops rotating every flushed rectangle in software.

**`MY` reverses the row counter**, so a 240x240 panel bonded to the top of the
controller's 320-row RAM moves to the far end of it: every row address needs
`LCD_ROW_OFFSET = 80` (320 − 240) added. Column offset is 0. If a future panel
turns out to be a true 240x240 controller, the offset is 0 and the picture is
merely shifted -- that is the one thing to check on a first flash.

There is deliberately **no `rotation:` key** in the `lvgl:` block of
`esphome/htram.yaml`. Do not add one back; you would rotate twice.

## Who owns the panel, and when

The GD32 draws first and hands over: `g_external_display_active` is set by the
first `CMD_DRAW_RECT` and the local UI stops. Two rules keep that handover from
leaving debris:

- **The GD32 stays off the panel for its first 3 seconds**, drawing only a
  clearing black fill. Its bit-banged SPI blocks the main loop for far longer
  than the 22 ms its RX ring buys at 921600, so drawing while the ESP streams
  its first full frame overruns the ring and eats a band of that frame -- and
  the band above `y=44` is one LVGL never invalidates again, so whatever was
  there stays for good. This is what used to strand the GD32's boot banner
  across the top after every wake from standby.
- **The banner only exists in the fallback.** If no `CMD_DRAW_RECT` has arrived
  by then, the GD32 draws `WAITING FOR ESP32` plus live sensor lines -- the only
  evidence the device works when the ESP is dead. In normal operation nothing
  of the GD32's is ever shown, so nothing has to be hidden.

After a GD32 restart the ESP repaints everything from scratch; see
`HELLO_FLAG_BOOT` in `htram-link-protocol`. Expect one
`lvgl took a long time (~1400 ms)` warning per restart -- that is the full
115 KB frame, not a fault.

## Repaints cost UART bandwidth

Every invalidated region becomes a `CMD_DRAW_RECT` frame carrying `w*h*2` bytes
at 921600 baud. This is the whole performance model of the UI: a widget that
straddles others, or an animation whose region grows, gets expensive fast. A
seconds ring that clears at the top of the minute redraws almost the whole
screen. When something on screen visibly lags and then jumps to catch up, look
for an oversized invalidation before suspecting the link.

## Iterating the design

The sandbox renders the layout in a browser at true physical scale, so designs
can be judged without building firmware:

```bash
python3 tools/uidesign/serve.py    # then http://localhost:8099
```

Edit `tools/uidesign/layouts.json`; the numbers transfer to the YAML unchanged.
**Calibrate 1:1 with a bank card first** (Calibrate panel) or the mock lies
about physical size. Device body is 80x80 mm, display ~27x27 mm, the black
circle ~65 mm.

`reference.webp` is gitignored -- the vendor's product shot is not ours to
redistribute -- so drop your own photo of the factory screen next to
`index.html` to use the **reference** button.

## Fonts

ESPHome renders fonts from the TTF at build time and LVGL cannot shear text, so
a slanted clock needs a slanted file. `tools/fonts/make_oblique.py` bakes it,
shearing about the middle of the cap height so digits keep their optical centre.
Licences for all bundled fonts live in `esphome/fonts/`; keep them with the
files.
