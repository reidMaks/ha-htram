# HTRAM console tools

Standalone BLE tooling for the Honeywell HTRAM air monitor, independent of the
Home Assistant integration in `custom_components/htram/`. Nothing here imports
Home Assistant and nothing here modifies the integration.

Everything below was established against one physical unit:
`HTRAM-RM1221412257`, BLE `94:E6:86:94:36:B2`, firmware `V1.00`, SKU `1617`.
The protocol facts come from the decompiled Android app
(`com.honeywell.sps.airmonitor` 2.9.1), which is the authoritative source —
the two published reverse-engineering projects both get details wrong.

---

## 1. Quick start

```bash
uv sync

.venv/bin/python tools/htram_wifi.py selftest              # codec check, no hardware
.venv/bin/python tools/htram_wifi.py scan
.venv/bin/python tools/htram_wifi.py gatt      <MAC>       # GATT + Device Information
.venv/bin/python tools/htram_wifi.py wifi      <MAC> --ssid S --password P \
                                               --mqtt-server tcp://IP:1883
.venv/bin/python tools/htram_wifi.py status    <MAC>       # link status, read-only
.venv/bin/python tools/htram_wifi.py setup     <MAC>       # probe sweep, read-only
.venv/bin/python tools/htram_wifi.py mqttfmt   <MAC> --broker IP:1883
.venv/bin/python tools/htram_wifi.py altchar   <MAC>       # probe 90178a02
.venv/bin/python tools/htram_wifi.py raw       <MAC> 7b41...7d

.venv/bin/python tools/mqtt_listener.py --port 1883        # evidence collector
```

`tools/htram_ble.py` is the codec and command set; `tools/htram_wifi.py` is the
CLI; `tools/mqtt_listener.py` is a minimal MQTT broker used only to observe
whether the device ever connects.

---

## 2. THE WORKING SEQUENCE — WiFi provisioning

This is verified working. The device joined the network, took a DHCP lease,
answered ping, and **kept the configuration across a full power cycle**.

```bash
.venv/bin/python tools/htram_wifi.py wifi 94:E6:86:94:36:B2 \
  --ssid 'YOUR_SSID' --password 'YOUR_PASSWORD' \
  --mqtt-server 'tcp://192.168.0.56:1883'
```

Which sends, in order:

| # | Step | Frame |
| --- | --- | --- |
| 1 | `fetchFirmwareVersion` | `7b41000620230134717d` |
| 2 | `fetchSku` | `7b410006202101b8727d` |
| 3 | `syncTimeToDevice` | `7b41000c2242 01 YY MM DD HH MM SS <crc> 7d` |
| 4 | **`changeBleMode(false)` = setWifiMode** | `7b41000874580101012b587d` |
| 5 | `submitAESKey` | `7b4100..20b0 01 <klen><key><ivlen><iv><ulen><url> <crc> 7d` |
| 6 | `submitSSID` | `7b41009f7460 …163 bytes… 7d` |
| 7 | *(disconnect BLE)* | — |

### The four things that all had to be right

Each was individually necessary. Missing any one produces the same symptom:
no error, a cheerful acknowledgement, and nothing happening.

1. **Step 4, `changeBleMode(false)` — body `01 01 01`.** The app calls this
   from a runnable literally named `setWifiModeRunnable`. Both published
   projects send only `changeBleMode(true)` (`01 01 00`), which is the *BLE*
   half of the same switch. Provisioning frames sent without this arrive at a
   device that was never switched over.
2. **CRC-16 polynomial 0x8005**, not 0x1021 — see §4.
3. **Dropping the BLE link at the end.** The device brings WiFi up only after
   BLE goes away; `ConnectToWifiActivity.sendSsidFinish` calls `disConnectBle`
   for exactly this reason. A device returning to advertising after
   provisioning is the success signal, not a fault.
4. **Not re-sending `changeBleMode(true)` afterwards** — see §7.1.

### Verification, and why it is causal rather than correlational

```
11:47  ARP sweep: 24 hosts, no 94:e6:86 present
12:04  provisioning run  -> 192.168.0.78 / 94:e6:86:94:36:b0 appears
12:04..12:08  present in 5/5 sweeps, ping 100%
12:09  device powered OFF -> ARP INCOMPLETE, 100% packet loss
12:10  device powered ON  -> same IP, same MAC, back in ~20 s, no BLE contact
12:10..12:18  present in 10/10 checks
```

No BLE interaction took place during the power-cycle test, so the result
cannot be an artefact of the tooling. The device rejoined unaided, which
proves the credentials are in non-volatile storage.

Independent confirmation from the router's own client list:

```
2.4 GHz │ espressif │ 192.168.0.78 │ DHCP │ 94:E6:86:94:36:B0 │ Tx 58.5 / Rx 6
```

The WiFi MAC is the BLE MAC minus 2 — the standard ESP32 base/BT offset — and
the DHCP hostname `espressif` is the ESP-IDF default.

---

## 3. Host prerequisites

### BLE must be enabled in bluetoothd

This laptop shipped with `/etc/bluetooth/main.conf` containing:

```
ControllerMode = bredr
```

That restricts every controller to BR/EDR, so LE scanning and LE connections
are impossible regardless of hardware — scans return zero devices and
`SetDiscoveryFilter Transport=le` has no effect. Fix:

```bash
sudo sed -i 's/^ControllerMode = bredr/ControllerMode = dual/' /etc/bluetooth/main.conf
sudo systemctl restart bluetooth
```

### Pairing needs a PIN, and a GUI agent to enter it

The device displays a PIN and expects passkey pairing (the Honeywell guide
says so explicitly). `bluetoothctl pair` run non-interactively cannot supply
it and falls through to Just Works, producing a weak bond the device may later
refuse with `org.bluez.Error.Failed le-connection-abort-by-local`.

On this desktop **blueman-applet owns the BlueZ agent**, so `bluetoothctl`
answers `Failed to register agent object` and every PIN prompt goes to
blueman's dialog. Pair through the tray icon and type the code from the
device screen.

A BlueZ record of `Paired: yes, Bonded: yes` says only that the *host* kept
the key. It says nothing about whether the device still honours it.

---

## 4. Protocol

```
7B 41 00 <len> <cmd BE u16> <body...> <crc16 BE u16> 7D
```

`len` = 2 (cmd) + len(body) + 3 (crc + 0x7D), so the total frame is `4 + len`.
Responses use the request opcode **plus 0x0100**: 0x4044 → 0x4144,
0x7460 → 0x7560, 0x2021 → 0x2121.

### CRC-16 is polynomial 0x8005, init 0, MSB-first

`selftest` checks the codec against thirteen frames taken verbatim from the
app and from `const.py`.

| polynomial | frames reproduced |
| --- | --- |
| **0x8005** (CRC-16/IBM) | **12 / 13** |
| 0x1021 (CCITT) | 0 / 13 |

The single outlier is `CMD_GET_SETTINGS` in `custom_components/htram/const.py`,
whose embedded CRC (`ef17`) no polynomial reproduces — a transcription typo in
that constant, not a different checksum.

> **Open bug in the integration.** `custom_components/htram/utils.py` uses
> 0x1021. Its hardcoded constants are unaffected because they are literals,
> but every packet it builds at runtime — including `construct_submit_ssid` —
> carries a CRC the device rejects. Left unfixed here by request.

### Complete command set

Every BLE command the app is capable of sending, from `CMBLERequest.java`.
There are no others; in particular there is **no read-back for the stored
SSID, password, MQTT server or AES key**.

| Command | Opcode | Frame / body |
| --- | --- | --- |
| `getRealTimeRequestData` | `40 44` | `7b41000740440200fc3e7d` |
| `fetchAlertValueAndScreenOff` | `40 43` | thresholds + screen timer |
| `fetchAlarmSoundStatus` | `26 23` | `7b4100072623010009c07d` |
| `fetchTemperatureUnit` | `20 6E` | `7b410007206e02067e307d` |
| `fetchSn` | `20 20` | `7b410006202003be7e7d` |
| `fetchSku` | `20 21` | `7b410006202101b8727d` |
| `fetchFirmwareVersion` | `20 23` | `7b41000620230134717d` |
| `fetchDataLog` | `20 93` | `01` + 4-byte address |
| `getDeviceLinkNetStatus` | `74 00` | `7b410006740002fa6b7d` |
| `heartBeat` | `24 01` | `7b410006240101 78227d` |
| `syncTimeToDevice` | `22 42` | `01 YY MM DD HH MM SS`, plain ints, UTC |
| `changeBleMode(true)` — BLE mode | `74 58` | `7b4100087458010100ab5d7d` |
| `changeBleMode(false)` — WiFi mode | `74 58` | `7b41000874580101012b587d` |
| `submitSSID` | `74 60` | see below |
| `submitAESKey` (MOV1) | `20 B0` | see below |
| `submitAESKeyForMVO2` (MOV2) | `20 B0` | chunked, 128-byte pieces |
| `submitAlertValue` | `42 43` | low, high, screen-off |
| `submitScreenOffTime` | `42 43` | magic `20 00` |
| `submitTemperatureUnitC/F` | `22 32` | `7b4100082232020600a9e37d` / `…01 29e67d` |
| `submitAlarmSoundStatusOn/Off` | `26 43` | `…2b667d` / `…ab637d` |

`submitSSID` body (154 bytes → length byte `0x9F` → 163-byte frame):

```
01              flag
00 × 22         reserved
<len>           password length, 1 byte
<password>      zero-padded to 64
<ssid>          zero-padded to 33
00 × 33         reserved
```

`submitAESKey` body:

```
01              flag
<len> <key>     AES key, Base64-DECODED to raw bytes before sending
<len> <iv>      AES IV, sent as raw ASCII bytes of the string (not decoded)
<len> <url>     MQTT URL, raw string bytes
```

That key/IV asymmetry is in the original, not a porting mistake.

### Response parsing

| Response | Meaning |
| --- | --- |
| `21 21`, bytes [7:9] | SKU as hex digits: `0651` → "1617", `0653` → "1619" |
| `21 23`, bytes [7:13] | firmware version, ASCII |
| `41 44`, bytes [7:13] | CO2 BE16, temp i8, humidity u8, battery 0-4, charging |
| `75 00`, byte [7] | link status: 2/3 connected, 4 failed, 0 not configured |

### SKU 1617 vs 1619 — not "BLE-only vs WiFi"

`Constants.java` defines MOV1 = `1617`, MOV2 = `1619`. A third-party README
calls 1617 the "BLE Device"; **the app contradicts that.**
`WifiListPrecenter` runs WiFi provisioning for both, and
`ConnectToWifiActivity` is launched from four places, none of which check the
SKU. They differ only in that MOV2 chunks the AES key and gets its link status
polled, while MOV1 sends the key in one packet and is provisioned blind — the
app shows "enrolled" without verifying anything.

---

## 5. The single most important trap: acknowledgements mean nothing

**The firmware acknowledges any well-formed frame**, replying with
`<request opcode + 0x0100>` and a body that echoes the first request body
byte. It does this for opcodes that cannot possibly exist:

| sent | received |
| --- | --- |
| `0x74FE` body `01` | `0x75FE` body `01` |
| `0x74AA` body `01` | `0x75AA` body `01` |
| `0x6BCD` body `01` | `0x6CCD` body `01` |
| `0x1234` body `01` | `0x1334` body `01` |
| `0x74FE` body `02` | `0x75FE` body `02` |

So an ACK proves nothing about whether a command was understood or took
effect. Two consequences, both learned the hard way:

* **Silence does not mean "unimplemented".** An empty body is ignored by
  *every* opcode, including ones that work. Probing with empty bodies and
  reading the silence as "not supported" is invalid. Always send a body.
* **An echo does not mean "unimplemented" either.** `0x7460` replies with
  nothing but the echo and *demonstrably works* — the device joined WiFi.

The only trustworthy signals are a reply carrying a **data body longer than
the echo**, and **external observation** (the network, the broker, ARP).

### Which commands are provably implemented

Established by data-carrying replies, with body-matched bogus controls:

| Implemented (returns data) | Indistinguishable from unimplemented (echo only) |
| --- | --- |
| `40 44` realtime | `74 58` changeBleMode |
| `40 43` thresholds | `74 60` submitSSID *(but proven to work)* |
| `26 23` sound | `74 61`, `74 63`, `74 65` |
| `20 6E` temp unit | `20 B0` submitAESKey |
| `20 21` SKU | `20 A3` NB-IoT, `27 08`/`27 0A` Zigbee |
| `20 23` firmware | |
| `74 00` link status | |

`0x7400` earns its place: its reply `0x7500 02 00` is one byte longer than the
echo, and the control (`0x74FE` with the same `02` body) returns the echo
alone.

---

## 6. MQTT: what is known, ruled out, and still open

**Status: not working.** The device joins WiFi and then does nothing at all.

### Established

* **Zero DNS queries.** AdGuard Home is the network resolver; the client
  `192.168.0.78` shows an empty request count while neighbours show tens of
  thousands. The device is not reaching for any hostname — so it is not
  falling back to a Honeywell endpoint baked into firmware either.
* **Zero TCP connections** to a broker on the address we supplied.
* **Link status stays 0** ("not configured"), read while the device was
  verifiably on WiFi. It never reaches 4 ("tried and failed").
* **The URL format is not the cause.** `mqttfmt` sent 0x20B0 five times in one
  session — `tcp://h:p`, `h:p`, `mqtt://h:p`, `ssl://h:8883`, bare `h` —
  reading status between each. No change.
* **Honeywell's cloud is gone.** `airmonitoring.honeywell.com` now 301s to a
  marketing page; the staging and China hosts in the app do not respond. Real
  enrollment credentials cannot be obtained.

### What the keys actually are

From `EnrollResponse.EnrollDeviceInfo`, the cloud returned `deviceId`,
`aesKey`, `aesIv`, `mqttUrl` per device at enrollment. The phone — not the
device — is the authenticated party (it carries an `Authorization` header);
it enrolls the device on the user's behalf and injects the resulting secrets
over BLE. BLE is the out-of-band provisioning channel; the keys *are* the
device's identity, minted rather than negotiated. The device has no account
and no way to obtain them itself.

Key + IV together imply a block cipher with an explicit IV — payload
encryption, not transport security and not MQTT login credentials. The app
contains **no MQTT client and no crypto code whatsoever**: it reads already
decrypted telemetry from the REST API. So the AES layer is device↔cloud, with
the cloud decrypting using the key it issued.

Note that `submitAESKeyForMVO2` chunks the key in **128-byte** pieces, which
only makes sense if the key can be far longer than 16 bytes — closer to a
certificate or long token than an AES-128 key.

### Still open — and observationally identical from outside

| Hypothesis | Predicted observation |
| --- | --- |
| keys rejected by validation | silence |
| handled exception in the crypto/startup path | silence |
| endpoint never stored by 0x20B0 | silence |
| MQTT gated on a binding that never happened | silence |

All four predict exactly what we see. Black-box observation is exhausted:
there is no read-back command, the acknowledgement carries no information, and
the network shows nothing because nothing is sent.

### Contradictory evidence worth resolving

The router's client list shows `Access time` for this device resetting in
under a minute, which would mean constant re-association. Continuous ping over
five minutes recorded **zero** losses, not even one second, and ARP stayed
`REACHABLE` throughout. These two disagree. Either the TP-Link counter tracks
something other than association uptime (power-save transitions, perhaps), or
re-association is fast enough to not drop ICMP. Unresolved.

A crash loop would explain much. Twenty minutes of continuous 1 Hz ping
recorded **no loss at all**, not even a single second, while ARP stayed
`REACHABLE` throughout — so there is no reboot cycle at that timescale. That
rules out an *unhandled* ESP32 panic, which reboots the chip. It does **not**
rule out a rarer cycle, and it says nothing at all about a **handled**
exception that silently aborts MQTT startup without touching the radio — which
remains fully consistent with every measurement taken.

The contradiction with the router therefore stands unresolved and, if
anything, sharpens: `Access time` resets in under a minute while ICMP is
uninterrupted for twenty.

> Note on that experiment's design: it printed a line only when a gap
> occurred, so an empty log is equally consistent with "perfectly stable" and
> "unreachable the whole time" — the same ambiguity §5 warns about. It is only
> interpretable because ARP was independently sampled during the window and
> the device answered immediately afterwards. A monitor should always emit a
> periodic liveness line, not just exceptions.

### The cheap way forward: read the UART console

Dumping the firmware is not the next step — **reading its console output is.**
ESP-IDF prints boot logs, WiFi events, error strings and panic traces on
UART0 at 115200. Two wires (device TX, GND), a USB-UART adapter, read-only,
nothing written, no decompilation needed. It would show directly whether the
MQTT client starts, what it does with the keys, and where it stops.

---

## 7. Behaviour of the device, and mistakes this tool used to make

### 7.1 `0x7458` is a mode switch, not a handshake

Body `01 01 00` = BLE mode, `01 01 01` = WiFi mode. Both published projects
open every session with the BLE form and this tool copied them, which
**silently took a provisioned device off WiFi on every single connect** and
produced a long trail of "WiFi keeps dropping after you touch it".

Corrected: `_init_session` no longer touches the radio mode. It talks to the
device as-is (heartbeat + realtime poll), which works fine, and falls back to
the mode switch only if the device will not answer at all — with a warning,
because WiFi is the price. Commands that do switch to WiFi mode restore it
before disconnecting (`--restore-wifi`, on by default).

### 7.2 BLE and WiFi are NOT mutually exclusive

Measured directly: BLE `Connected: yes` and WiFi ping 4/4 at the same instant.
An earlier claim here that they exclude each other was a bad generalisation
from sessions that all happened to contain the mode-switch command.

### 7.3 Never wrap `BleakClient.connect()` in a retry loop

bleak's timeout fires on the client side while BlueZ's `Connect()` is still in
flight, so a connection that lands a moment later gets torn down by the next
iteration. The symptom is indistinguishable from "the script disconnects a
working device and then cannot get it back".

Corrected: `_connect` never calls connect in a loop. The device is bonded and
trusted, so BlueZ reconnects it by itself when it advertises; the tool watches
the `Connected` property and nudges with at most one `Connect()` per minute.

### 7.4 Scanning is unnecessary and costly

The device is known to BlueZ, so its object path can be used directly. Scans
happen only before a session exists, never while one is live.

### 7.5 Explicit `Disconnect()` disarms auto-reconnect

BlueZ drops the device from the kernel LE auto-connect list on an explicit
disconnect, so it stops reconnecting on its own and needs a button press.
`--keep` now defaults to true. Note that BlueZ still drops the link when the
owning D-Bus client exits, which no flag can prevent.

### 7.6 The keepalive pollutes request windows

The heartbeat ACK (`0x2501`) arrives asynchronously every 8 s and was being
counted as a reply to whatever request was in flight, making unrelated opcodes
look answered. `HtramClient.IGNORED_CMDS` filters it.

### 7.7 The device only advertises after a button press

Honeywell's manual states the button must be pushed to enable Bluetooth, that
the icon blinks for one minute, and that Bluetooth shuts down if no connection
is found. Pairing stores keys; it does not make a peripheral advertise. A
dropped link therefore cannot be recovered in software — this is also why
auto-discovery does not work for the integration.

---

## 8. Hardware notes

* **The radio is an ESP32-WROOM-32E.** Stated on the case label
  (`../docs/device-label.jpeg`):

  ```
  Model: HTRAM-V1-W
  Contains FCC ID: 2AC7Z-ESP32WROOM32E
  Contains IC:  21098-ESPWROOM32E
  SN: RM1221412257
  ```

  `2AC7Z-ESP32WROOM32E` is Espressif's ESP32-WROOM-32E: a full dual-core
  ESP32-D0WD-V3 with WiFi and BT/BLE and 4 MB of flash on the module. The FCC
  grant covers 802.11 b/g/n, so WiFi is a declared, certified capability of
  this exact SKU — not an incidental leftover. The label serial matches the
  BLE device name `HTRAM-RM1221412257` exactly.

  Independently corroborated before the label was available: the BLE MAC
  `94:E6:86:…` is an Espressif OUI, the WiFi MAC is the BLE MAC minus 2 (the
  standard ESP32 base/BT offset), and the Device Information Service returns
  un-terminated strings padded with leaked heap full of `0x3ffe…`/`0x3fff…`
  pointers — the ESP32 DRAM range.

  Practical consequence: the UART console plan in §6 needs no guesswork.
  UART0 is GPIO1 (TX) / GPIO3 (RX) at 115200 on standard WROOM castellations,
  and a full flash dump is the ordinary `esptool.py read_flash 0 0x400000
  dump.bin` with GPIO0 pulled low — no SWD, no proprietary tooling.
* **DIS contents are placeholders**: manufacturer `Honeywell`, model
  `IAQ CO2`, serial `FN##########`, System ID `ABCD-EFG`, PnP `ABCD-EF`,
  hardware `V1.00`, firmware `V1.0`.
* **A third vendor characteristic exists**, `90178a02-5d4a-11e6-8b77-86f30ca893d3`
  `[read,write]`, used by no known project. Reading it returns uninitialised
  heap containing the string `Binding-Status`. Probe it with `altchar`.
* **No standard provisioning service** (BluFi, protocomm, DPP) is exposed.
* **Board photos** (`../docs/mainboard-front.jpeg` front, `../docs/mainboard-back.jpeg` back) confirm
  the architecture directly. The back silkscreen reads:

  ```
  Storm Shadow Main Board   REV3  20210610  B309
  FASTPCB  E300750  DS194V-0
  ```

  "Storm Shadow" is the same codename the app uses for OTA images
  (`StormShadow.RFP`), and the board revision matches the teardown in
  `noname122021/honeywell-htram-v1w-ble-monitor` exactly.

  | Side | Parts |
  | --- | --- |
  | front | ESP32-WROOM-32E, NDIR CO2 sensor, buzzer `LS1`, micro-USB, battery JST |
  | back | `U8` LQFP48 (the GD32F150C8T6), `U4` SOIC-8 (the Winbond 25Q32), display FPC `J3`, `LED03`–`LED07` |

  So: GD32 is the application MCU driving sensor, display and the SPI flash
  log; ESP32-WROOM-32E is the radio subsystem. Two controllers, exactly as the
  teardown reports.

* **Roughly thirty labelled test points**, `TP1`–`TP31`, are broken out on the
  back — deliberate factory test pads, not stray vias. Likely to include the
  inter-chip link and possibly UART.

## 8b. Firmware dump — what the ESP32 actually runs

`esptool read-flash 0 ALL` produced a 4 MB image
(`esp32-dump.bin`, md5 `edef2ac93a4016cb64ea5c14c1984fcb`). The chip reports
ESP32-D0WD-V3 rev 3.0, 40 MHz crystal, MAC `94:e6:86:94:36:b0` — read straight
out of eFuse, and identical to the WiFi MAC seen in ARP and in the router's
client list. No flash encryption, no secure boot, so the image is plaintext.

### It is stock ESP-AT, not Honeywell firmware

Build path in the binary: `/builds/application/esp-at/esp-idf/...`. Partition
table:

| Partition | Offset | Size |
| --- | --- | --- |
| phy_init | `0x00f000` | 4 KB |
| otadata | `0x010000` | 8 KB |
| nvs | `0x012000` | 56 KB |
| at_customize | `0x020000` | 896 KB |
| ota_0 | `0x100000` | 1.5 MB |
| ota_1 | `0x280000` | 1.5 MB |

All eight certificates in `at_customize` are Espressif's shipped samples
(`ESP Root CA S1/S2/C1/C2`, `ESPRESSIF AT Root CA`) — nothing from Honeywell.
The only hostnames anywhere in the image are ESP-AT defaults
(`iot.espressif.cn`, `ntp.sjtu.edu.cn`). **There is no baked-in endpoint**,
which is why the device never resolves anything: the modem has nowhere of its
own to go and takes its instructions from the GD32 at runtime.

This settles the architecture. The ESP32 is a dumb AT modem. Every application
decision — the vendor protocol, MQTT, AES, binding — lives in the GD32.
So the reason telemetry never starts is in the GD32's firmware, not here.

MQTT *capability* is present in the modem (`+MQTTCONN`, `+MQTTCONNCFG`,
`+MQTTCLIENTID`, `+MQTTCLEAN`, the `esp-mqtt` component, `mqtt_ca`/`mqtt_cert`
slots), so nothing on the ESP32 side prevents it.

### NVS proves the WiFi provisioning stuck

The NVS partition contains `TP-Link_B55B` and `3151831518` — four occurrences
each, alongside the standard `sta.ssid` / `sta.pswd` / `sta.apinfo` keys. The
credentials really are in non-volatile storage, which is why the device
rejoins unaided after a power cycle.

The MQTT broker address we supplied (`192.168.0.56`) is **not** in NVS. That
is not evidence that 0x20B0 failed: ESP-AT does not persist MQTT settings,
they are runtime-only AT parameters. Whatever the GD32 did with that address
is in the GD32's own storage.

### The inter-chip link: UART1, not UART0

`factory_param` at `0x30000` decodes as:

```
fcfc 02 01 4e 01 01 0d "CN" .. 115200 .. 11 10 0f 0e
                                        │  │  │  └ RTS = GPIO14
                                        │  │  └ CTS = GPIO15
                                        │  └ RX  = GPIO16
                                        └ TX  = GPIO17
```

So the AT interface is **UART1 at 115200**, TX `GPIO17` (module pin 28), RX
`GPIO16` (module pin 27). Tapping GPIO16 shows what the GD32 *commands*; that
is where to look for `AT+CWJAP` (WiFi) and whether `AT+MQTTUSERCFG` /
`AT+MQTTCONN` are ever issued at all.

**And this explains the total silence on UART0.** CTS is GPIO15, which the
GD32 holds — and a low GPIO15 at boot suppresses the ESP32 ROM bootloader
messages. Combined with application logging disabled in the build, UART0 emits
nothing, ever. The earlier soldering to TXD0 was correct for the signal and
simply aimed at the wrong UART.

## 8c. The AT conversation — why MQTT never reached our broker

Tapping `GPIO16` (module pin 27, ESP32's UART1 RX) at 115200 captures exactly
what the GD32 commands the modem to do. The boot sequence:

```
ATE0
AT+BLEINIT=2
AT+BLEGATTSSRVCRE
AT+BLEGATTSSRVSTART
AT+BLEADVDATA="0201061309485452414D2D524D31323231343132323537"
AT+BLENAME="HTRAM-RM1221412257"
AT+BLESECPARAM=1,0,16,3,3
AT+BLEADVSTART
AT+CWMODE=1,0
AT+CWAUTOCONN=0
AT+CWJAP="TP-Link_B55B","3151831518"
AT+CIFSR
AT+MQTTCLEAN=0
AT+MQTTUSERCFG=0,7,"RM1221412257","1617:V1.00 :32315","bbdf…6659",0,0,
AT+MQTTCONN=0,"8.0.56",443,0
```

This kills the "the device never even tries" hypothesis outright: it issues a
full MQTT sequence on every boot, right after joining WiFi. Two separate
reasons it never reached us:

### 1. It speaks MQTT over WebSocket Secure, not plain MQTT

`AT+MQTTUSERCFG` scheme **7** is MQTT over WSS in ESP-AT, and the port is
**443**. Our listener was a plain MQTT broker on 1883, so it was never going
to see this connection regardless of the address. We were watching the wrong
protocol on the wrong port.

### 2. The GD32 mangles the broker address

`192.168.0.56` arrived at the modem as `8.0.56` — the **first six characters
stripped**, which is exactly the length of a scheme prefix like `tcp://`,
`ssl://` or `wss://`. The GD32 removes a fixed six-character prefix without
checking that one is present. The value sent in that run was a bare IP with no
scheme (the last entry of the `mqttfmt` sweep), so it ate the address instead.

Fix, **confirmed on hardware**: pass the URL **with** a six-character scheme so
the strip lands on the prefix. Sending `tcp://192.168.0.56` produced

```
AT+MQTTCONN=0,"192.168.0.56",443,0
```

with the address intact. Note the port stayed 443 even though no port was
given — 443 is hardcoded in the GD32 and any port in the URL is ignored.

### The username carries a per-boot nonce

The third field of the username changes on every boot, and the password
follows it:

```
1617:V1.00 :32315 -> bbdf8516…
1617:V1.00 :45167 -> fb8f6c7b…
1617:V1.00 :16743 -> ba758af5…
1617:V1.00 :45179 -> 82f4d1bb…
```

`sha256(username)` reproduces all four. The nonce is fresh per session, but
since the username travels in clear next to the hash, it authenticates
nothing.

### BLE advertising is exactly 60 seconds after boot

The AT tap settles this precisely — `AT+BLEADVSTART` at boot, then
`AT+BLEADVSTOP` 59–60 s later, every time. **The button is not required**: a
power cycle starts advertising on its own, which matters when the display is
disconnected and the blinking icon cannot be seen. `_connect` retries every
15 s so that each window gets several attempts.

### MQTT credentials are derivable, not secret

| Field | Value |
| --- | --- |
| client id | `RM1221412257` — the serial number |
| username | `1617:V1.00 :32315` — SKU, firmware, build |
| password | `sha256(username)` |

Verified: `sha256("1617:V1.00 :32315")` reproduces the observed password
exactly. The password is a hash of the username transmitted alongside it in
clear, so it authenticates nothing.

Note also that the AES key and IV from `0x20B0` play **no part** in the
connection — they are not the MQTT credentials. They must be for payload
encryption, as the key+IV pair suggested all along.

### This finally gives a feedback channel

Until now every write went in blind: acknowledgements carry no information
(§5) and there is no read-back command. The AT tap changes that — send a
`0x20B0`, watch which `AT+MQTTCONN` comes out. That turns guesswork into an
ordinary debugging loop.

## 8d. Talking to the device: `mqtt_wss.py`

The device needs **MQTT over WebSocket Secure on port 443** — not plain MQTT.
`tools/mqtt_wss.py` is a stdlib-only server that provides exactly that: TLS,
the RFC 6455 upgrade with subprotocol `mqtt`, and MQTT framing inside, logging
everything it receives.

```bash
openssl req -x509 -newkey rsa:2048 -keyout htram-key.pem -out htram-cert.pem \
  -days 3650 -nodes -subj "/CN=<your-ip>" -addext "subjectAltName=IP:<your-ip>"

sudo .venv/bin/python -u tools/mqtt_wss.py --cert htram-cert.pem --key htram-key.pem
```

Port 443 is privileged, hence `sudo`. Scheme 7 does not verify the server
certificate, so self-signed is fine. The TLS floor is lowered to TLS 1.0 and
`SECLEVEL=1` because ESP-AT's stack is old.

Provision the device to point at it — the scheme prefix is mandatory:

```bash
.venv/bin/python tools/htram_wifi.py wifi <MAC> \
  --ssid S --password P --mqtt-server tcp://<your-ip>
```

then power-cycle. The connection attempt follows about five seconds after
boot.

## 8e. IT WORKS — the device publishes telemetry to a self-hosted broker

End to end, with Honeywell's cloud long dead:

```
192.168.0.78:54545  TCP connected
                    TLS up (TLSv1.2, ECDHE-RSA-AES256-GCM-SHA384)
                    GET / HTTP/1.1   User-Agent: ESP32 Websocket Client
                    WebSocket established, subprotocol mqtt
                    CONNECT client_id='RM1221412257'
                            username='1617:V1.00 :15831'
                            sha256(username) MATCHES
                    -> CONNACK accepted
                    SUBSCRIBE [('D/RM1221412257', 0)]
                    PUBLISH  topic='C/RM1221412257' qos=0 len=27
```

The predicted password formula held live on the hardware. TLS 1.2 with a
modern cipher — the worry about ESP-AT's stack being too old was unfounded.

### Topics

| Topic | Direction |
| --- | --- |
| `C/<serial>` | device → server, telemetry, every **30 s** |
| `D/<serial>` | server → device, the device subscribes to it |

### Telemetry payload — 27 bytes, and **not encrypted**

```
44 43 | 00 02 00 01 | tttttttt | 51 06 00 0c 00 00 xx xx xx xx | cccc | 00 | TT | HH | ????
 "DC"   constant      time LE                                    CO2 LE      °C   %    tail
```

| Offset | Field |
| --- | --- |
| `[0:2]` | magic `"DC"` |
| `[2:6]` | constant `00 02 00 01` |
| `[6:10]` | Unix timestamp, little-endian, UTC |
| `[10:20]` | mostly constant `51 06 00 0c 00 00`, then four varying bytes |
| `[20:22]` | **CO2 ppm**, little-endian |
| `[22]` | `00` |
| `[23]` | **temperature °C** |
| `[24]` | **humidity %** |
| `[25:27]` | CRC-16/0x8005 over `[16:25]`, little-endian |

Worked example:

```
444300020001 b5d2966a 5106000c0000 0100a102 f401 00 19 38 83b4
             ↑2026-09-01 13:27:17Z            ↑500ppm  ↑25°C ↑56%
```

Values match what BLE reports for the same moment, and the timestamp matches
the broker's own clock. **The AES key and IV from `0x20B0` are not used to
encrypt this** — the readings are in the clear. What that key is actually for
remains unknown.

### Warm-up packets must be discarded

The first publish after every boot carries sentinels, not readings:

```
CO2 0xFFFE   temperature 0x81 (-127)   humidity 0xFE (254)
```

The NDIR sensor has not stabilised 30 s after power-up. Feed these to Home
Assistant unfiltered and you get 65534 ppm spikes and false alarms.
`tools/decode_telemetry.py` filters them; any integration must do the same.

### A real capture

```
time      gap    readings
16:43:47    -   CO2 ---- ppm   T --- C   RH --- %   <warm-up, discard>
16:44:17   30s   CO2  650 ppm   T  26 C   RH  51 %
16:44:46   29s   CO2  850 ppm   T  26 C   RH  53 %
16:45:16   30s   CO2  950 ppm   T  25 C   RH  54 %
16:45:46   30s   CO2  850 ppm   T  25 C   RH  52 %
16:46:16   30s   CO2  500 ppm   T  24 C   RH  55 %
16:46:46   30s   CO2  450 ppm   T  25 C   RH  55 %
16:47:15   29s   CO2  450 ppm   T  23 C   RH  57 %
```

CO2 rises with someone next to the device and falls when they step away, and
humidity mirrors it — the decode is physically coherent. The interval holds at
29–30 s with no drift.

### `mid` and `tail` are unidentified

```
mid 0104260e  tail 8d79      mid 01001d08  tail 66a9
mid 01000508  tail e33a      mid 0100ce08  tail 586b
mid 01003d0a  tail 89d7      mid 01000a06  tail 847c
```

`[16]` is always `01`; `[17]` is almost always `00`. `[18:20]` is a **signed**
little-endian word — values `e8ff` and `f1ff` decode as −24 and −15 — and over
a longer capture it decays toward zero as CO2 settles:

```
mid[18:20]:  3622  2053  2621  2077  2254  1546  1017  152  -24  -15  822
CO2 ppm:      650   850   950   850   500   450   450  400  400  700  650
```

So it is a derived quantity that relaxes to zero at the 400 ppm baseline —
a rate of change or a filter residual rather than a raw sensor count. Eleven
points are not enough to pin the formula.

### The tail is a CRC — solved

```
CRC-16, polynomial 0x8005, init 0, MSB-first, no reflection, no xorout,
computed over bytes [16:25] and stored LITTLE-endian.
```

Verified on 73/73 captured packets. Same polynomial as the BLE protocol, but
stored little-endian there where BLE stores it big-endian.

Two things made this findable after an earlier 1008-combination sweep had
failed. First, a longer capture produced two pairs of packets whose tails were
identical while their timestamps differed — they differed in byte `[6]` alone.
A checksum covering the timestamp could not do that, so the covered range had
to exclude `[6:10]`, which cut the search space sharply. Second, checking
against all 73 packets at once makes a false positive impossible.

So the payload is fully accounted for except `mid`:

```
44 43              magic "DC"
00 02 00 01        constant
tt tt tt tt        timestamp, little-endian, UTC      <- outside the CRC
51 06 00 0c 00 00  constant                           <- outside the CRC
01 xx xx xx        mid: [16] always 01, [18:20] signed, decays to zero
cc cc  00  TT  HH  CO2 little-endian, pad, temp C, humidity %
kk kk              CRC-16/0x8005 of [16:25], little-endian
```

### Complete recipe

```bash
# 1. certificate for the broker
openssl req -x509 -newkey rsa:2048 -keyout htram-key.pem -out htram-cert.pem \
  -days 3650 -nodes -subj "/CN=<ip>" -addext "subjectAltName=IP:<ip>"

# 2. broker on 443 (privileged)
sudo .venv/bin/python -u tools/mqtt_wss.py --cert htram-cert.pem --key htram-key.pem

# 3. point the device at it -- the tcp:// prefix is MANDATORY.
#    A hostname works as well as an address: the device hands whatever it
#    stored to AT+MQTTCONN, which resolves names. A name is worth preferring,
#    because changing it later needs no button press.
.venv/bin/python tools/htram_wifi.py wifi <MAC> \
  --ssid <SSID> --password <PSK> --mqtt-server tcp://<host>

# 4. power-cycle the device; it connects ~5 s after boot
```

## 8f. Downlink on `D/<serial>` — channel confirmed, format not cracked

The device subscribes to `D/<serial>`, and Honeywell's own guide documents what
that channel was for:

> An alarm setting change in any device will apply to all other devices in same
> room
> Device settings change will not apply to all devices in same room (**Mute
> Device**, Turn Off Display, Temperature Unit)

The web portal changed settings on devices remotely, and the portal has no
Bluetooth — so remote control went over this topic. The capability is real and
matches the BLE command set exactly.

### Delivery is proven; the payload format is not

With the `--outbox` flag `mqtt_wss.py` publishes to `D/<serial>`, and the GPIO17
tap shows the modem handing each message to the GD32:

```
+MQTTSUBRECV:0,"D/RM1221412257",11,{A @D  >}
+MQTTSUBRECV:0,"D/RM1221412257",13,{A  &C   c}
+MQTTSUBRECV:0,"D/RM1221412257",19,{A BC @   x  }
```

Eighteen candidates were delivered intact and **none had any effect**. The
first batch varied the framing:

| Candidate | Result |
| --- | --- |
| raw BLE frame, `0x4044` realtime read | delivered, no reply |
| raw BLE frame, `0x2643` mute | delivered, no change |
| raw BLE frame, `0x2232` temperature unit | delivered, telemetry stayed °C |
| AES-128-CBC(key, iv) of a BLE frame, PKCS7 | delivered, no change |
| AES-128-CBC(key, iv) of a BLE frame, zero pad | delivered, no change |
| AES-128-ECB(key) of a BLE frame, zero pad | delivered, no change |

The second batch varied the cipher mode, with the provisioned key
(`0123456789abcdef`, the Base64 default from `--aes-key`) and IV
(`0123456789abcdef`, sent as raw ASCII — so key and IV happen to be equal):

| Candidate | Result |
| --- | --- |
| CBC with a zero IV | delivered, no change |
| CBC over opcode + body only, no `7B`/`7D` | delivered, no change |
| ECB over opcode + body only | delivered, no change |
| CTR | delivered, no change |
| CFB | delivered, no change |
| `DC` envelope wrapping the encrypted frame | delivered, no change |

The third batch tried the encodings a text-oriented cloud would plausibly use:

| Candidate | Result |
| --- | --- |
| Base64 of a BLE frame | delivered, no change |
| hex-ASCII of a BLE frame, lower and upper case | delivered, no change |
| Base64 of AES-128-CBC ciphertext | delivered, no change |
| IV prepended to the ciphertext | delivered, no change |
| serial number prepended to a BLE frame | delivered, no change |

Every probe used `submitTemperatureUnit(F)` — the cheapest command to judge,
because the front panel shows the unit and the answer needs no BLE session.
The panel stayed on °C throughout, and the telemetry temperature field never
moved off its °C value either.

So the GD32 receives downlink messages and discards them — the wire format is
neither a bare vendor frame nor that frame encrypted with the provisioned key
under the obvious modes.

### Why the search stopped here

The UART tap proves delivery but says nothing about *why* the GD32 rejects a
payload, so every further guess would be a lottery with an uninformative
negative. The parser lives in the GD32, and cracking the format properly means
dumping **that** chip over SWD — a separate and harder job than the ESP32 was.

The practical loss is small: every function the portal could drive remotely is
already reachable over BLE. Downlink would be a convenience, not a capability.

**Still unknown after all this: what the provisioned AES key is actually for.**
It does not encrypt telemetry, it is not the MQTT credential, it does not
authenticate the payload tail, and it does not decrypt downlink under CBC or
ECB with the obvious paddings.

### Getting at the ESP32 console

ESP32-WROOM-32E has a standard pinout, so nothing needs to be guessed:

| Module pin | Signal |
| --- | --- |
| **35** | **TXD0 (GPIO1)** — what the console prints on |
| 34 | RXD0 (GPIO3) |
| 1, 15, 38 | GND |
| 3 | EN (reset) |
| 25 | GPIO0 — pull low for download mode |

To *read* the console: solder to pin 35 and any GND, attach a USB-UART at
115200. Read-only, nothing written, no power needed from the adapter.

To *dump* the flash: pull GPIO0 low, toggle EN, then
`esptool.py read_flash 0 0x400000 dump.bin`.

Caveat: if the ESP32 talks to the GD32 over UART0, that line carries
inter-chip traffic as well as console output. ESP-IDF boot messages still go
there first at 115200, so startup and error strings will be visible either
way.

---

## 8g. Untried: reading device memory over BLE with `0x2093`

**Not yet attempted.** Written down so it is not lost.

`CMBLERequest.fetchDataLog` builds a frame the app never calls:

```java
byte[] bArr = {123, 65, 0, 10, 32, -109, 1, -1, -1, -1, -1, 0, 0, 125};
bArr2[7] = bArr[0]; ... bArr2[10] = bArr[3];   // a 32-bit address
```

Opcode `0x2093`, body `01 <A0 A1 A2 A3>`, answer opcode `0x2193`. The app's
default address is `FF FF FF FF`, and **no code in the app calls it** -- it is
a leftover in the SDK, which is exactly the kind of thing that tends to be
least guarded.

If the address is a raw memory address rather than an index into the 90-day
measurement log, this reads the GD32's flash over Bluetooth, and the firmware
can be dumped without SWD. That firmware is the only remaining source for the
downlink format: the vendor cloud is gone, the portal survives only as a
JavaScript shell in the Wayback Machine, and the app never speaks to the
device except during provisioning.

Frames to send with `htram_wifi.py raw <MAC> <hex>`:

| Address | Frame |
| --- | --- |
| `FF FF FF FF`, the app's default | `7b41000a209301ffffffffd6647d` |
| `0x00000000` | `7b41000a20930100000000564d7d` |
| `0x08000000` big-endian | `7b41000a20930108000000764e7d` |
| `0x08000000` little-endian | `7b41000a20930100000008d67e7d` |

`0x08000000` is the GD32F1 flash base. A Cortex-M image starts with its vector
table: four bytes of initial stack pointer, which for this part means
`0x2000xxxx` since SRAM starts at `0x20000000`, then four bytes of reset
handler at `0x0800xxxx`. Seeing that pair in the answer means the read is
unbounded and the dump is on.

The device must be within Bluetooth range of the machine running the tool.
Through a distant ESPHome proxy at -96 dBm it will not connect.

## 9. Status summary

| Item | State |
| --- | --- |
| WiFi provisioning over BLE | **working**, survives power cycle |
| Credentials in non-volatile storage | **confirmed** by power-cycle test |
| Reading device settings back | impossible — no such command exists |
| MQTT telemetry | **working** — WSS on 443, plaintext payload decoded |
| CRC bug in `custom_components/htram/utils.py` | open, unfixed by request |

### Sources

* Decompiled `com.honeywell.sps.airmonitor` 2.9.1 — authoritative.
* [noname122021/honeywell-htram-v1w-ble-monitor](https://github.com/noname122021/honeywell-htram-v1w-ble-monitor)
  — correct CRC and framing; its `PROTOCOL.md` opcode table comes from a
  smali command list covering hardware variants this unit does not have, and
  its SKU interpretation is not supported by the app.
* [Wyox/honeywell-htram-ble](https://github.com/Wyox/honeywell-htram-ble) — a
  clean codec implementation.
* Honeywell's own step-by-step software guide, which documents the app flow:
  register → log in → BLE pair with PIN → connect WiFi network.
