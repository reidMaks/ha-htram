---
name: esp-firmware
description: Build, upload and observe the ESPHome firmware on the ESP32 half of the device, including how to read live sensor state and capture logs without losing output to buffering. Trigger terms (uk) - залий на esp, перезбери esphome, збери прошивку esp, покажи логи, подивись логи, що показує пристрій, стан пристрою, перевір телеметрію.
---

# The ESP32 side

## esphome is not on PATH

It lives in the project venv. Forgetting this is the most common first failure:

```bash
source .venv/bin/activate && esphome run esphome/htram.yaml --device 192.168.0.78 --no-logs
```

Run it backgrounded -- a full build plus OTA takes minutes. Do not pipe it
through `tail` if you want to watch progress, because `tail` shows nothing until
the pipeline ends; redirect to a file and read that instead.

The device is at `192.168.0.78`, and resolves as `htram-9436b0.lan` /
`htram-9436b0.local` -- `name_add_mac_suffix` puts the MAC suffix in the node
name, so a plain `htram.local` never resolves. A successful OTA reports around
1.18 MB uploaded in roughly 5.5 s.

## Reading live state without a log session

The web server exposes a server-sent-event stream that dumps every entity's
current state on connect. This is the fastest way to answer "what does the
device think right now":

```bash
curl -s --max-time 8 -N --digest -u "$(sed -n 's/^web_username: *//p' esphome/secrets.yaml | tr -d '\"')":"$(sed -n 's/^web_password: *//p' esphome/secrets.yaml | tr -d '\"')" http://192.168.0.78/events > /tmp/ev.txt; grep -a "Battery\|CO2\|Charging" /tmp/ev.txt
```

**The credentials are not optional.** `web_server` runs with digest auth, and
`WebServerBase::add_handler()` wraps every handler -- including our own
`/gd32_ota` -- in `AuthMiddlewareHandler` as soon as credentials exist. Without
them the request comes back `401` and the file is empty. They live in
`esphome/secrets.yaml` as `web_username` / `web_password`; the OTA password for
`esphome run` is a different secret and is unaffected.

Note it writes to a file first, then greps. That is not an accident -- see
below.

## Log capture, and the trap in it

**`grep` block-buffers when its stdout is a pipe, and `timeout` kills the
pipeline with SIGTERM before the buffer is ever flushed.** So this silently
produces nothing, no matter how much matching output scrolled past:

```bash
timeout 30 esphome logs esphome/htram.yaml | grep -i error | head   # WRONG, prints nothing
```

Capture to a file, then search the file:

```bash
source .venv/bin/activate && timeout 30 esphome logs esphome/htram.yaml > /tmp/log.txt 2>&1; grep -iE "warn|error" /tmp/log.txt
```

When grepping for warnings, remember our own debug lines can contain the word
"warn" and produce false positives.

## What to check after a flash

`Rotations: 0 °` in the display setup (rotation belongs to the GD32's MADCTL,
not to LVGL), flow-control PAUSE/RESUME cycling normally, and telemetry with
plausible values. Sensors that publish 0 on boot are suppressed on purpose --
zeros pollute Home Assistant history -- so their absence early on is correct.

The face also has states other than a clock, and seeing one is not a fault:
`refresh_face` shows the fallback AP's QR and credentials while the captive
portal is up, and a `немає часу` / `немає мережі` message whenever the clock is
unset. LEDs stay dark until the first real CO2 reading rather than showing the
GD32's boot green.
