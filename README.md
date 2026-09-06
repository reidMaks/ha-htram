# Honeywell Transmission Risk Air Monitor (HTRAM)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integration for the **Honeywell Transmission Risk Air Monitor**,
reverse-engineered from the vendor's Android app after Honeywell shut the cloud
service down. Everything here is local: nothing talks to Honeywell, because
there is no longer anything to talk to.

The device speaks two protocols and the integration uses both.

| | Bluetooth | MQTT |
| --- | --- | --- |
| CO2, temperature, humidity | yes, polled | yes, pushed every 30 s |
| Battery, charging | yes | — |
| Buzzer, thresholds, screen, units | yes, the only way | — |
| Setup needed | none | your own broker |

Bluetooth alone is a complete, working setup. MQTT is an optional upgrade
that trades some setup for readings every 30 seconds and far fewer Bluetooth
connections — which matters, because it is the polling that tends to disturb
the pairing.

## Requirements

- Home Assistant 2026.8 or newer
- A Bluetooth adapter on the Home Assistant host, or a Bluetooth proxy

## Installation

**HACS** — add this repository as a custom repository of category
*Integration*, download it, restart Home Assistant.

**Manually** — copy `custom_components/htram` into your `config/custom_components`
directory and restart.

## Setting it up

The monitor advertises over Bluetooth **only in short windows** — for about a
minute after boot, and after it loses a connection. Outside those windows it is
invisible and unconnectable. Press its button before anything else; if a step
reports that the device did not answer, press the button and retry rather than
starting over.

1. **Settings → Devices & Services → Add Integration → HTRAM.**
   Pick your monitor from the list. That is the whole setup: the sensors work
   from here on.

2. **Optional, on the integration card → Configure.** Two things live there:

   - **Provision device** — sends a WiFi network, and optionally a broker
     address, to the monitor over Bluetooth.
   - **Data source** — switches the readings from Bluetooth polling to MQTT.

## The MQTT path

This is worth being honest about: it is real work, and it is optional.

The device was built to publish to Honeywell's cloud and its firmware is not
configurable. It will only speak **MQTT over WebSocket Secure on port 443**,
and it authenticates with a username that changes on every connection
(`1617:V1.00 :<random>`) whose password is the SHA-256 of that same string. No
managed broker will accept that, including Home Assistant's own Mosquitto
add-on — its auth plugin cannot be bypassed from the customize folder.

So the MQTT path needs a broker you control, with an anonymous WebSocket
listener on port 443. [tools/README.md](tools/README.md) documents the protocol
and a working setup in full, including why each constraint exists and how it
was established.

Once such a broker exists:

1. **Configure → Provision device.** Give the WiFi network and the broker
   address. The address needs a six-character scheme prefix such as `tcp://`,
   which the device strips without reading; a hostname works as well as an IP,
   and is worth preferring since changing it later needs no button press.
2. **Configure → Data source.** Enable MQTT and enter the serial number — it is
   the part after the dash in the advertised name, `HTRAM-RM1221412257`.

Nothing new appears. The same CO2, temperature and humidity entities keep their
ids and history and simply change what feeds them. A diagnostic sensor reports
which transport is live.

If telemetry stops for five minutes those three sensors go unavailable. They do
not silently fall back to Bluetooth polling: that is the thing MQTT was adopted
to avoid, and resuming it unannounced is how a working setup quietly degrades.

## Entities

| Entity | Source |
| --- | --- |
| `sensor.<device>_co2` | MQTT when enabled, otherwise Bluetooth |
| `sensor.<device>_temperature` | " |
| `sensor.<device>_humidity` | " |
| `sensor.<device>_battery_level` | " |
| `sensor.<device>_data_source` | diagnostic: `bluetooth` or `mqtt` |
| `binary_sensor.<device>_charging` | MQTT when enabled, otherwise Bluetooth |
| `switch.<device>_mute` | on means silent (MQTT downlink or Bluetooth) |
| `select.<device>_temperature_unit` | display panel only; telemetry stays Celsius (MQTT downlink or Bluetooth) |
| `select.<device>_screen_off_timer` | MQTT downlink or Bluetooth |
| `number.<device>_co2_alarm_low` | yellow threshold (MQTT downlink or Bluetooth) |
| `number.<device>_co2_alarm_high` | red threshold (MQTT downlink or Bluetooth) |
| `button.<device>_sync_time` | sets the device clock, UTC (Bluetooth only) |
| `button.<device>_bluetooth_session` | opens a 5-minute Bluetooth window for manual diagnostics |

There is also a `htram.configure_device` service, which provisions a targeted
monitor without going through the options dialog.

## Known limitations

**Battery is reported in bars**, 0 to 4, and shown as 0/25/50/75/100 %.

**The first couple of minutes after a power-up are discarded.** Until the NDIR
sensor warms up the device reports CO2 65534, temperature 0x81 and humidity
0xFE -- which decode to 65534 ppm, -127 C and 254 % if taken at face value. All
three are dropped and the sensors read unavailable until real numbers arrive.

**Time synchronization requires Bluetooth.** While telemetry and settings controls
work fully over MQTT downlink (`D/<serial>`), the hardware clock sync command
is only accepted over BLE.

## Development

```bash
uv sync
uv run pytest
```

The suite covers the wire protocol against the vendor app's own byte literals
and 465 telemetry payloads captured from a device, and runs the integration
inside a real Home Assistant through
`pytest-homeassistant-custom-component`. The harness version is pinned to the
Home Assistant version it targets; they must be bumped together.

`tools/` holds the console programs the protocol was worked out with: a BLE
client, a WiFi provisioner, a UART sniffer, a standalone MQTT-over-WSS broker,
downlink command testing, SWD flash extractors, and a telemetry decoder. They are independent of
Home Assistant and are the right place to start when something needs debugging at the protocol level.

## Legal, Research & Safety Disclaimer

### 1. Interoperability & Scientific Research Purpose
This project is an independent, non-commercial scientific and engineering research effort aimed at achieving software interoperability, device longevity, and electronic waste reduction for abandoned hardware following the vendor's cloud service termination.

Reverse engineering of the communication protocols and firmware interfaces was conducted solely for interoperability and security analysis, in accordance with:
* **Directive 2009/24/EC** of the European Parliament and of the Council (specifically Articles 5(3) and 6 regarding reverse engineering and decompilation for interoperability).
* **17 U.S.C. § 1201(f)** of the United States Code (Digital Millennium Copyright Act Reverse Engineering / Interoperability Exception).
* Applicable **Right to Repair** and fair use principles regarding consumer-owned hardware.

### 2. Trademark & Non-Affiliation Notice
This project is completely unofficial and is **not** endorsed, sponsored, certified, or affiliated with **Honeywell International Inc.**, **GigaDevice Semiconductor**, **Espressif Systems**, or any of their subsidiaries or affiliates. All product names, trademarks, logos, and registered trademarks referenced herein are the property of their respective owners and are used solely for descriptive and identification purposes.

### 3. Clean-Room & Content Distribution
This repository contains original open-source integration code, clean-room protocol specifications, and hardware documentation. It **does not** host, redistribute, or license any proprietary vendor firmware binaries, copyrighted graphic assets, or private cryptographic keys.

Third-party material that *is* included is limited to fonts, each with its own licence alongside it in [`esphome/fonts/`](esphome/fonts/): Noto Sans under the SIL Open Font License 1.1, and DejaVu Sans ExtraLight (plus an oblique this project derived from it) under the Bitstream Vera licence. The MIT licence at the root covers this project's own code and documentation, not those fonts.

### 4. Hardware Safety & Limitation of Liability
All software, scripts, hardware pinouts, and instructions are provided **"AS IS"**, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE. 

Physical hardware modifications, test-point soldering, SWD programming, or custom firmware flashing carry inherent risks of electrical shock, component damage, short circuits, or battery hazards. The authors and contributors assume **no liability whatsoever** for damaged hardware, bricked devices, personal injury, voided warranties, or data loss resulting from the use of this project. Proceed at your own risk.

## Credit

The initial BLE integration was reverse-engineered and written by **Antigravity (Google
DeepMind)** with human supervision; the protocol work, MQTT telemetry, and initial HA modernisation were developed with **Claude**; the complete downlink command specification, SWD hardware exploitation (RDP1 bypass), flash analysis, dual MQTT/BLE control architecture, and state persistence were reverse-engineered and completed by **Antigravity (Google DeepMind)**.
