---
name: htram-link-protocol
description: The binary UART protocol between the GD32 and the ESP32 - framing, CRC, packet types, and the software flow control that makes 921600 baud survivable. Use when adding or changing any packet. Trigger terms (uk) - протокол, кадр, пакет, CRC, телеметрія формат, додати команду, flow control, потік даних, зв'язок між чипами.
---

# The GD32 ↔ ESP32 link

Definitions live in `firmware/gd32/inc/protocol.h`; the ESP side parses them in
`esphome/custom_components/htram_gd32/htram_gd32.cpp`.

## Framing

Every packet starts `0xAA 0x55`, then a type byte. Every packet except
`DRAW_RECT` ends with CRC-16-CCITT: polynomial `0x1021`, **init `0x0000`**,
MSB-first. The init value is the detail people get wrong; the usual CCITT
variant starts at `0xFFFF` and will not validate here.

Uplink (GD32 → ESP): `0x01` telemetry, 14 bytes; `0x02` hello, 17;
`0x03` button, 8; `0x04` flow control, 6.

Downlink (ESP → GD32): `0x10` draw rect, `0x11` backlight, `0x12` LEDs,
`0x13` beep, `0x14` melody, `0x1F` enter bootloader (guarded by the key
`0xDEADBEEF`).

Telemetry status bits: charging `1<<0`, USB present `1<<1`, warm-up `1<<2`,
sensor error `1<<3`, button `1<<4`, LEDs green/yellow/red `1<<5..7`.

## Changing a packet means changing two places

Lengths are hardcoded on both sides. `head_packet_len_()` in the ESP component
must agree with the struct in `protocol.h`, or frames desynchronise silently --
you get no error, just a link that quietly stops making sense. Change both, in
the same commit.

## Why flow control exists here

There are no RTS/CTS wires between the chips. XON/XOFF cannot ride the pixel
stream either, because `0x11` and `0x13` occur inside RGB565 data. But the
reverse channel already carries framed packets, so the hold-off travels there:
the GD32 sends `0x04` with `resume=0` before it blocks for longer than its 2 KB
RX ring can absorb -- 22 ms at 921600 -- and `resume=1` afterwards.

The ESP sends pixels in **512-byte chunks** so that an in-flight chunk always
fits the remaining ring space. While paused, the ESP must not re-enter LVGL;
doing so is what caused an earlier stall-then-catch-up stutter on screen.
