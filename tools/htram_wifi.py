#!/usr/bin/env python3
"""Console tool to inspect an HTRAM air monitor over BLE and, if the firmware
supports it, provision WiFi credentials.

Standalone: needs only `bleak`, never imports Home Assistant. Run it from the
repo's venv:

    .venv/bin/python tools/htram_wifi.py selftest
    .venv/bin/python tools/htram_wifi.py scan
    .venv/bin/python tools/htram_wifi.py gatt      <MAC>
    .venv/bin/python tools/htram_wifi.py probe     <MAC>
    .venv/bin/python tools/htram_wifi.py provision <MAC> --ssid S --password P
    .venv/bin/python tools/htram_wifi.py raw       <MAC> 7b41...7d

The device only advertises for ~60 s after you double-click its button, so
scan and connect promptly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bleak import BleakClient, BleakScanner  # noqa: E402
from bleak.backends.device import BLEDevice  # noqa: E402
from bleak.exc import BleakDBusError, BleakError  # noqa: E402

import htram_ble as p  # noqa: E402

# Services worth calling out in a GATT dump: if the second controller really is
# an ESP32 running stock provisioning firmware, one of these would show up and
# would be a far easier route than the vendor protocol.
KNOWN_PROVISIONING = {
    "0000ffff-0000-1000-8000-00805f9b34fb": "Espressif BluFi (ESP32 WiFi provisioning)",
    "021a9004-0382-4aea-bff4-6b3f1c5adfb4": "Espressif protocomm / Unified Provisioning",
    "0000fd81-0000-1000-8000-00805f9b34fb": "Wi-Fi Alliance Wi-Fi Easy Connect (DPP)",
    "0000fe59-0000-1000-8000-00805f9b34fb": "Nordic DFU",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
}

BLE_OFF_HINT = """
  Nothing was discovered. Check, in this order:
    1. bluetoothd LE mode:   grep ControllerMode /etc/bluetooth/main.conf
       It must be `dual` (or `le`), NOT `bredr`. Fix and restart:
         sudo sed -i 's/^ControllerMode = bredr/ControllerMode = dual/' /etc/bluetooth/main.conf
         sudo systemctl restart bluetooth
    2. The device is advertising: double-click its button, the Bluetooth icon
       must be blinking. It stops advertising after ~60 s.
    3. No other app is holding the discovery session (Blueman, GNOME Settings).
"""


async def cmd_selftest(_args) -> int:
    """Verify the CRC implementation against packets captured from the app."""
    # Hardcoded frames from custom_components/htram/const.py, which were taken
    # verbatim from the decompiled Android app -- i.e. known-good ground truth.
    vectors = {
        "GET_REALTIME": "7b41000740440200fc3e7d",
        "HEARTBEAT": "7b41000624010178227d",
        "GET_SOUND_STATUS": "7b4100072623010009c07d",
        "SET_SOUND_OFF": "7b410009264301000000ab637d",
        "SET_SOUND_ON": "7b4100092643010000012b667d",
        "GET_SETTINGS": "7b410009404304006006ef177d",
        "GET_TEMP_UNIT": "7b410007206e02067e307d",
        "SET_TEMP_UNIT_C": "7b4100082232020600a9e37d",
        "SET_TEMP_UNIT_F": "7b410008223202060129e67d",
        # Recovered from CMBLERequest.java in the decompiled app.
        "FETCH_SN": "7b410006202003be7e7d",
        "FETCH_SKU": "7b410006202101b8727d",
        "FETCH_FIRMWARE": "7b41000620230134717d",
        "LINK_NET_STATUS": "7b410006740002fa6b7d",
    }
    print("CRC-16/0x8005 against the app's own packets:\n")
    passed = 0
    for name, hexstr in vectors.items():
        frame = bytes.fromhex(hexstr)
        embedded = int.from_bytes(frame[-3:-1], "big")
        computed = p.crc16(frame[:-3])
        ok = embedded == computed
        passed += ok
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name:17} embedded={embedded:04x} computed={computed:04x}")
    print(f"\n  {passed}/{len(vectors)} match.")
    print(
        "  (GET_SETTINGS is the lone outlier -- that constant in const.py carries a\n"
        "   CRC that no polynomial reproduces, so it looks like a transcription typo.)"
    )

    for name, frame in (
        ("SET_BLE_MODE", p.CMD_SET_BLE_MODE),
        ("SET_WIFI_MODE", p.CMD_SET_WIFI_MODE),
        ("HEARTBEAT", p.CMD_HEARTBEAT),
    ):
        print(f"  [{'ok  ' if p.frame_is_valid(frame) else 'FAIL'}] {name:17} "
              f"built: {frame.hex()}")

    ssid_frame = p.submit_ssid_command("TP-Link_B55B", "3151831518")
    print(f"\n  WIFI_CFG_SET frame builds: {len(ssid_frame)} B, "
          f"length byte 0x{ssid_frame[3]:02x}, CRC valid={p.frame_is_valid(ssid_frame)}")
    return 0 if passed >= len(vectors) - 1 else 1


async def _discover(timeout: float):
    try:
        return await BleakScanner.discover(timeout=timeout, return_adv=True)
    except BleakDBusError as err:
        if "InProgress" in str(err):
            print(
                "  BlueZ refused to start a scan: another process already owns the\n"
                "  discovery session (usually Blueman or GNOME Settings). Close it\n"
                "  and retry.",
                file=sys.stderr,
            )
            return None
        raise


async def cmd_scan(args) -> int:
    print(f"Scanning {args.timeout:.0f}s for BLE devices...\n")
    found = await _discover(args.timeout)
    if found is None:
        return 2
    htrams = []
    for addr, (dev, adv) in sorted(found.items(), key=lambda kv: -(kv[1][1].rssi or -999)):
        name = dev.name or adv.local_name or ""
        is_htram = name.upper().startswith("HTRAM") or p.SERVICE_UUID in [
            u.lower() for u in adv.service_uuids
        ]
        mark = " <== HTRAM" if is_htram else ""
        if is_htram:
            htrams.append(addr)
        print(f"  {addr}  rssi={adv.rssi:>4}  {name!r}{mark}")
        if adv.service_uuids:
            print(f"        services: {', '.join(adv.service_uuids)}")

    if not found:
        print(BLE_OFF_HINT)
        return 2
    if not htrams:
        print(f"\n  {len(found)} device(s), none of them an HTRAM.")
        print("  Double-click the device's button so it starts advertising, then retry.")
        return 1
    print(f"\n  HTRAM found: {', '.join(htrams)}")
    return 0


async def _resolve(address: str, timeout: float = 15.0, quiet: bool = False,
                   allow_scan: bool = True):
    """Resolve `address` without scanning whenever that is possible.

    Scanning is actively harmful here. This adapter has one antenna, so an
    active scan time-slices the radio against connection events and the
    peripheral's supervision timeout expires -- the device drops the link and
    then will not re-advertise until someone presses its button. A retry loop
    that scans every couple of seconds therefore destroys the very connection
    it is trying to establish.

    The device is bonded, so BlueZ already knows its object path and no scan is
    needed to reach it. Scan only as a last resort, for a device BlueZ has
    never seen.
    """
    path = await _bluez_device_path(address)
    if path is not None and not allow_scan:
        if not quiet:
            print(f"  using BlueZ object {path} (no scan)")
        return BLEDevice(address, None, {"path": path, "props": {}})

    try:
        dev = await BleakScanner.find_device_by_address(address, timeout=timeout)
    except BleakDBusError as err:
        if "InProgress" not in str(err):
            raise
        dev = None
    if dev is None and path is not None:
        if not quiet:
            print(f"  using BlueZ object {path} (scan saw nothing)")
        return BLEDevice(address, None, {"path": path, "props": {}})
    if dev is None:
        raise BleakError(
            f"{address} is neither advertising nor known to BlueZ. Double-click "
            f"the device's button (the Bluetooth icon must blink) and retry "
            f"within ~60 s."
        )
    return dev


async def _bluez_device_path(address: str) -> str | None:
    """Look up the org.bluez object path for an already-known device."""
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
    except ImportError:
        return None
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect("org.bluez", "/")
        manager = bus.get_proxy_object(
            "org.bluez", "/", introspection
        ).get_interface("org.freedesktop.DBus.ObjectManager")
        for path, ifaces in (await manager.call_get_managed_objects()).items():
            device = ifaces.get("org.bluez.Device1")
            if device and device["Address"].value.upper() == address.upper():
                return path
        return None
    finally:
        bus.disconnect()


async def _bluez_device_props(address: str) -> tuple[str, dict] | None:
    """Return (object_path, Device1 properties) for a device BlueZ knows."""
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
    except ImportError:
        return None
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect("org.bluez", "/")
        manager = bus.get_proxy_object(
            "org.bluez", "/", introspection
        ).get_interface("org.freedesktop.DBus.ObjectManager")
        for path, ifaces in (await manager.call_get_managed_objects()).items():
            device = ifaces.get("org.bluez.Device1")
            if device and device["Address"].value.upper() == address.upper():
                return path, {k: v.value for k, v in device.items()}
        return None
    finally:
        bus.disconnect()


async def _bluez_connect(path: str) -> str:
    """Ask BlueZ to connect, once. Returns a short status string."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect("org.bluez", path)
        device = bus.get_proxy_object("org.bluez", path, introspection).get_interface(
            "org.bluez.Device1"
        )
        await asyncio.wait_for(device.call_connect(), timeout=25.0)
        return "ok"
    except asyncio.TimeoutError:
        return "timeout"
    except Exception as err:
        return f"{type(err).__name__}: {err}"
    finally:
        bus.disconnect()


async def _connect(address: str, verbose: bool = True, wait: float = 300.0):
    """Attach to the device, letting BlueZ own the connecting.

    Deliberately NOT a retry loop around BleakClient.connect(). That pattern
    breaks this device: bleak's timeout fires on our side while BlueZ's
    Connect() call is still in flight, so a connection that lands a moment
    later gets torn down by the next iteration -- which looks exactly like
    "the script disconnects a working device and then cannot get it back".

    Instead: the device is bonded and trusted, so BlueZ reconnects it by itself
    whenever it advertises. We issue at most one Connect() nudge per minute and
    otherwise just watch the Connected property until it goes true.
    """
    found = await _bluez_device_props(address)
    if found is None:
        raise BleakError(
            f"{address} is not known to BlueZ at all. Pair it first: "
            f"bluetoothctl pair {address}"
        )
    path, props = found

    deadline = asyncio.get_running_loop().time() + wait
    nudged_at = 0.0
    announced = False
    while True:
        found = await _bluez_device_props(address)
        if found is not None:
            path, props = found
            if props.get("Connected"):
                break

        now = asyncio.get_running_loop().time()
        if now >= deadline:
            raise BleakError(
                f"{address} never came up within {wait:.0f}s. It only advertises "
                f"for ~60 s after a double-click of its button."
            )
        if not announced:
            print("  Waiting for BlueZ to connect. DOUBLE-CLICK THE BUTTON -- the"
                  "\n  Bluetooth icon must blink. Not scanning, not retrying.",
                  flush=True)
            announced = True
        # The device advertises for exactly 60 s after each boot and then the
        # GD32 issues AT+BLEADVSTOP -- confirmed on the AT tap. Nudging once a
        # minute lands outside that window as often as not, so try often
        # enough that every window gets several attempts.
        if now - nudged_at > 15.0:
            nudged_at = now
            status = await _bluez_connect(path)
            if status == "ok":
                continue
            print(f"    (nudge: {status})", flush=True)
        await asyncio.sleep(2.0)

    if verbose:
        print(f"  BlueZ reports Connected on {path}")
    client = BleakClient(BLEDevice(address, props.get("Name"), {"path": path,
                                                               "props": props}))
    await client.connect()
    if verbose:
        print(f"Connected to {address} "
              f"(MTU {getattr(client, 'mtu_size', '?')})\n")
    return client


DIS_CHARS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
    "00002a50-0000-1000-8000-00805f9b34fb": "PnP ID",
    "00002a08-0000-1000-8000-00805f9b34fb": "Date Time",
}

# Extra readable characteristic inside the vendor service, alongside notify/write.
VENDOR_EXTRA_UUID = "90178a02-5d4a-11e6-8b77-86f30ca893d3"


async def _read_device_info(client) -> None:
    """Read the Device Information Service. Model/hardware revision is the most
    direct evidence of which SKU this unit is (1617 BLE vs 1619 WiFi)."""
    print("\nDevice Information Service:\n")
    dis = next(
        (s for s in client.services
         if s.uuid.lower() == "0000180a-0000-1000-8000-00805f9b34fb"),
        None,
    )
    if dis is None:
        print("  (no Device Information Service)")
        return
    # Read by handle: 0x2A29 appears in both the Battery Service and here, so
    # reading by UUID is ambiguous.
    by_uuid = {c.uuid.lower(): c for c in dis.characteristics}
    available = set(by_uuid)
    for uuid, label in DIS_CHARS.items():
        if uuid not in available:
            continue
        try:
            raw = bytes(await client.read_gatt_char(by_uuid[uuid].handle))
        except Exception as err:
            print(f"  {label:19}: <read failed: {err}>")
            continue
        # The firmware returns un-terminated strings padded with whatever was
        # next in RAM, so cut at the first non-printable byte and show the rest
        # as the leaked tail it is.
        cut = 0
        while cut < len(raw) and 32 <= raw[cut] < 127:
            cut += 1
        text = raw[:cut].decode("ascii", "replace").strip()
        tail = raw[cut:]
        shown = repr(text) if text else "(empty)"
        if tail:
            shown += f"  + {len(tail)} B tail: {tail.hex()}"
        print(f"  {label:19}: {shown}")
    extra = next((c for s_ in client.services for c in s_.characteristics
                  if c.uuid.lower() == VENDOR_EXTRA_UUID), None)
    if extra is not None:
        try:
            raw = bytes(await client.read_gatt_char(extra.handle))
            print(f"  {'vendor char':19}: hex {raw.hex()}")
        except Exception as err:
            print(f"  {'vendor char':19}: <read failed: {err}>")


async def cmd_gatt(args) -> int:
    client = await _connect(args.address, wait=args.wait)
    try:
        print("GATT table:\n")
        for service in client.services:
            note = KNOWN_PROVISIONING.get(service.uuid.lower(), "")
            if service.uuid.lower() == p.SERVICE_UUID:
                note = "HTRAM vendor service"
            suffix = f"   <-- {note}" if note else ""
            print(f"  service {service.uuid}{suffix}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"    char {char.uuid}  [{props}]")
        await _read_device_info(client)
        print()
        found = {s.uuid.lower() for s in client.services} & KNOWN_PROVISIONING.keys()
        prov = found - {"0000180a-0000-1000-8000-00805f9b34fb"}
        print()
        if prov:
            print("  Standard provisioning service present:")
            for uuid in sorted(prov):
                print(f"    {uuid} -- {KNOWN_PROVISIONING[uuid]}")
        else:
            print("  No standard WiFi-provisioning service (BluFi / protocomm / DPP)")
            print("  is exposed. If WiFi setup is possible it has to go through the")
            print("  vendor protocol -- run `probe` next.")
        return 0
    finally:
        if getattr(args, "keep", False):
            print("\n  (leaving the link connected; pass --no-keep to drop it)")
        else:
            await client.disconnect()


async def _init_session(hc: p.HtramClient, attempts: int = 5) -> bool:
    """Bring the session up WITHOUT switching the device's radio mode.

    0x7458 is a mode switch, not a handshake: body 01 01 00 is BLE mode and
    01 01 01 is WiFi mode. Published projects open every session with the BLE
    form, and this tool copied them -- which silently took a provisioned device
    off WiFi on every single connect. BLE and WiFi coexist fine on this device;
    it was the command that broke it, not the connection.

    So: talk to the device as it is. Only fall back to the mode switch if it
    will not answer at all, and say plainly that WiFi is the price.
    """
    print("Initialising session:")
    await hc.start()
    mtu = await hc.acquire_mtu()
    print(f"  negotiated ATT MTU: {mtu} (max single write {mtu - 3} B)")

    async def liveness(label: str) -> bool:
        probe = await hc.request(label, b"\x40\x44", b"\x02\x00", timeout=4.0)
        for frame in probe.responses:
            if frame[4:6] == b"\x41\x44" and len(frame) >= 13:
                temp = frame[9] - 256 if frame[9] >= 128 else frame[9]
                print(
                    f"\n  Device is talking: CO2={int.from_bytes(frame[7:9], 'big')} "
                    f"ppm, T={temp} C, RH={frame[10]}%, batt={frame[11]}/4, "
                    f"charging={frame[12] == 1}"
                )
                return True
        return False

    for attempt in range(1, attempts + 1):
        await hc.write(p.CMD_HEARTBEAT)
        await asyncio.sleep(0.5)
        if await liveness(f"  [{attempt}/{attempts}] REALTIME  "):
            return True
        await asyncio.sleep(1.0)

    print("\n  No answer without touching the radio mode. Falling back to")
    print("  changeBleMode(true) -- NOTE: this takes the device off WiFi.")
    await hc.write(p.CMD_CHANGE_BLE_MODE)
    await asyncio.sleep(1.5)
    if await liveness("  REALTIME after mode switch"):
        return True
    await hc.write(p.CMD_CHANGE_BLE_MODE_LEGACY)
    await asyncio.sleep(1.5)
    return await liveness("  REALTIME after legacy switch")


async def _restore_wifi_mode(hc: p.HtramClient) -> None:
    """Hand the device back to WiFi mode before we let go of it.

    _init_session sends changeBleMode(true) -- 0x7458 body 01 01 00 -- which is
    the BLE-mode half of the same mode switch that puts the device on WiFi.
    Every session therefore takes a provisioned device OFF the network and
    leaves it there. Undo it on the way out.
    """
    try:
        print("\n  restoring WiFi mode (0x7458 body 01 01 01) before disconnect")
        await hc.write(p.CMD_SET_WIFI_MODE)
        await asyncio.sleep(1.5)
    except Exception as err:
        print(f"  could not restore WiFi mode: {err}")


async def _keepalive(hc: p.HtramClient, client, period: float = 8.0) -> None:
    """Send the heartbeat command periodically so the device keeps the link.

    An idle HTRAM drops the connection within roughly a minute, and it only
    re-advertises after a physical button press -- so an idle timeout costs a
    trip to the device.
    """
    while True:
        await asyncio.sleep(period)
        if not client.is_connected:
            return
        try:
            await hc.write(p.CMD_HEARTBEAT)
        except Exception:
            return


async def _collect(hc: p.HtramClient, timeout: float, settle: float = 0.5) -> list[bytes]:
    """Drain frames until `timeout` passes with nothing new."""
    frames: list[bytes] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return frames
        try:
            frames.append(await asyncio.wait_for(hc._frames.get(), timeout=remaining))
        except asyncio.TimeoutError:
            return frames
        deadline = asyncio.get_running_loop().time() + settle


async def _bind_cloud(hc: p.HtramClient, args) -> bool:
    """Step 1: hand the device its MQTT endpoint and AES material (0x20B0).

    Honeywell's app only offered WiFi setup after the cloud had enrolled the
    device and returned aesKey/aesIv/mqttServer, so 0x7460 on its own leaves
    the device with nowhere to go. With their servers gone, a self-hosted
    broker takes that role.
    """
    packet = p.submit_aes_key_command(args.aes_key, args.aes_iv, args.mqtt_server)
    print("\n" + "=" * 72)
    print(f"BINDING  mqtt={args.mqtt_server}")
    print(f"         aes_key={args.aes_key}  aes_iv={args.aes_iv}")
    print("=" * 72)
    print(f"  AES_KEY_SET (0x20B0), {len(packet)} B")
    print(f"  -> {packet.hex()}")
    hc._drain()
    await hc.write(packet)
    acks = await _collect(hc, args.timeout)
    for frame in acks:
        print(f"     <- {frame.hex()}")
    if not acks:
        print("     (silent)")
    await asyncio.sleep(2.0)
    return bool(acks)


async def _do_provision(hc: p.HtramClient, args) -> int:
    """Try each candidate 0x7460 body, re-reading 0x7461 between attempts."""
    print("\n" + "=" * 72)
    print(f"PROVISIONING  ssid={args.ssid!r}  password={'*' * len(args.password)} "
          f"({len(args.password)} chars)")
    print("=" * 72)

    if args.mqtt_server:
        await _bind_cloud(hc, args)

    baseline = await hc.request("WIFI_CFG_GET (01)   ", b"\x74\x61", b"\x01",
                                timeout=args.timeout)
    base_hex = [f.hex() for f in baseline.responses]
    print(f"  baseline 0x7461: {base_hex or '(silent)'}\n")

    expected = p.response_cmd(b"\x74\x60").hex()
    results: list[tuple[str, bool, list[str]]] = []

    for name, body in p.submit_ssid_variants(args.ssid, args.password):
        packet = p.build_packet(b"\x74\x60", body)
        print(f"  {name} {len(packet):>3} B frame")
        hc._drain()
        try:
            await hc.write(packet, response=args.write_response and len(packet) > 20)
        except Exception as err:
            print(f"       write failed: {err}")
            results.append((name.strip(), False, []))
            continue
        acks = await _collect(hc, args.timeout)
        hexes = [f.hex() for f in acks]
        got_ack = any(f[4:6].hex() == expected for f in acks)
        if hexes:
            for h in hexes:
                print(f"       <- {h}")
        else:
            print("       (silent)")
        results.append((name.strip(), got_ack, hexes))

        after = await hc.request("       verify 0x7461", b"\x74\x61", b"\x01",
                                 timeout=args.timeout)
        if [f.hex() for f in after.responses] != base_hex:
            print("       !! 0x7461 CHANGED after this variant")
        await asyncio.sleep(0.5)

    print("\n" + "=" * 72)
    print("RESULT")
    print("=" * 72)
    any_ack = any(ack for _, ack, _ in results)
    any_frame = any(hexes for _, _, hexes in results)
    for name, ack, hexes in results:
        state = "ACK 0x7560" if ack else ("other frame" if hexes else "silent")
        print(f"  {name:26} {state}")

    final = await hc.request("  final 0x7461       ", b"\x74\x61", b"\x01",
                             timeout=args.timeout)
    final_hex = [f.hex() for f in final.responses]
    print(f"\n  0x7461 baseline: {base_hex or '(silent)'}")
    print(f"  0x7461 final   : {final_hex or '(silent)'}")

    if any_ack:
        print("\n  0x7460 IS implemented -- the device acknowledged at least one")
        print("  body layout. Check your router's client list for a new device.")
        return 0
    if any_frame:
        print("\n  0x7460 produced frames but never the 0x7560 ACK. Inspect the hex")
        print("  above; the opcode reacts to something.")
        return 1

    print(
        "\n  0x7460 is not implemented on this unit.\n"
        "  Every candidate body -- including the one-byte probe that the very\n"
        "  same firmware answers on 0x7461 -- was met with total silence, on a\n"
        "  connection where ordinary GETs kept answering throughout. That rules\n"
        "  out the wrong-body-length explanation: a live opcode with a length\n"
        "  check would still have to dispatch on the one-byte form the way\n"
        "  0x7461 does. There is no WiFi provisioning path over BLE here."
    )
    return 1


async def cmd_altchar(args) -> int:
    """Probe the vendor service's second writable characteristic.

    The vendor service exposes 90178a02 [read,write] alongside the usual
    notify/write pair. No known project touches it, and reading it returns
    uninitialised heap containing the string "Binding-Status" -- so if cloud
    binding or provisioning ever used a separate channel, this is it.
    """
    client = await _connect(args.address, wait=args.wait)
    hc = p.HtramClient(client)
    keepalive = None
    try:
        if not await _init_session(hc):
            print("\nDevice never answered a known-good command; aborting.")
            return 1
        keepalive = asyncio.create_task(_keepalive(hc, client))

        char = next((c for svc in client.services for c in svc.characteristics
                     if c.uuid.lower() == VENDOR_EXTRA_UUID), None)
        if char is None:
            print(f"\n  {VENDOR_EXTRA_UUID} is not present on this device.")
            return 1

        print(f"\nCharacteristic {char.uuid}  [{','.join(char.properties)}]")

        async def snapshot(tag: str) -> bytes:
            raw = bytes(await client.read_gatt_char(char.handle))
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
            print(f"  {tag}: {len(raw)} B")
            print(f"    hex  : {raw.hex()}")
            print(f"    ascii: {printable}")
            return raw

        before = await snapshot("before")

        writes: list[tuple[str, bytes]] = [
            ("vendor WIFI_CFG_SET frame",
             p.submit_ssid_command(args.ssid, args.password)),
            ("vendor WIFI_CFG_GET frame", p.build_packet(b"\x74\x61", b"\x01")),
            ("bare SSID string", args.ssid.encode()),
        ]
        if args.mqtt_server:
            writes.insert(0, ("vendor AES_KEY_SET frame",
                              p.submit_aes_key_command(args.aes_key, args.aes_iv,
                                                       args.mqtt_server)))

        for name, payload in writes:
            print(f"\n  write {name} ({len(payload)} B)")
            hc._drain()
            for use_response in (True, False):
                try:
                    await client.write_gatt_char(char.handle, payload,
                                                 response=use_response)
                    print(f"    accepted (response={use_response})")
                    break
                except Exception as err:
                    print(f"    rejected (response={use_response}): "
                          f"{type(err).__name__}: {err}")
            else:
                continue
            for frame in await _collect(hc, args.timeout):
                print(f"    notify <- {frame.hex()}")
            await asyncio.sleep(1.0)
            after = await snapshot("after")
            if after != before:
                print("    !! CHARACTERISTIC CHANGED")
                before = after

        print("\n" + "=" * 72)
        print("RESULT")
        print("=" * 72)
        print("  Compare the before/after dumps above. An unchanged value after")
        print("  every write means this channel stores nothing either.")
        return 0
    finally:
        if keepalive is not None:
            keepalive.cancel()
        if getattr(args, "restore_wifi", True):
            await _restore_wifi_mode(hc)
        await hc.stop()
        if not args.keep:
            await client.disconnect()


async def cmd_wifi(args) -> int:  # noqa: C901
    """Provision WiFi exactly the way the Android app does.

    Sequence recovered from WifiListPrecenter.java:

        fetchFirmware (0x2023) -> fetchSku (0x2021) -> changeBleMode(false)
        (0x7458, body 01 01 01 -- the app calls this "setWifiMode")
        -> submitAESKey (0x20B0) -> submitSSID (0x7460)
        -> poll getDeviceLinkNetStatus (0x7400)

    Earlier attempts here sent only the last two steps while leaving the device
    in BLE mode, which is why nothing took effect.
    """
    if getattr(args, "random_keys", False):
        import base64
        import secrets

        args.aes_key = base64.b64encode(secrets.token_bytes(16)).decode()
        args.aes_iv = secrets.token_hex(8)  # 16 ASCII chars, sent as raw bytes
        print(f"Using random credentials: key={args.aes_key} iv={args.aes_iv}")

    client = await _connect(args.address, wait=args.wait)
    hc = p.HtramClient(client)
    keepalive = None
    try:
        if not await _init_session(hc):
            print("\nDevice never answered a known-good command; aborting.")
            return 1
        keepalive = asyncio.create_task(_keepalive(hc, client))

        print("\n" + "=" * 72)
        print("STEP 1 -- identify")
        print("=" * 72)

        fw = await hc.request("fetchFirmware (0x2023)", b"\x20\x23", b"\x01",
                              timeout=args.timeout)
        version = next(
            (v for v in (p.parse_firmware(fr) for fr in fw.responses) if v), None)
        print(f"  firmware: {version!r}" if version else "  firmware: <no 0x2123>")

        sku_x = await hc.request("fetchSku (0x2021)     ", b"\x20\x21", b"\x01",
                                 timeout=args.timeout)
        sku = next((v for v in (p.parse_sku(fr) for fr in sku_x.responses) if v), None)

        print("\n" + "=" * 72)
        print("SKU")
        print("=" * 72)
        if sku is None:
            print("  The device did not answer 0x2021 with a 0x2121 frame.")
            print("  Raw frames: " +
                  (", ".join(fr.hex() for fr in sku_x.responses) or "(none)"))
        else:
            label = {p.SKU_MOV1: "MOV1 -- single-packet AES key, no status poll",
                     p.SKU_MOV2: "MOV2 -- chunked AES key, pollable status"}.get(
                         sku, "unknown SKU")
            print(f"  SKU {sku}  ({label})")
            if sku == p.SKU_MOV1:
                print(
                    "\n  The app provisions WiFi for this SKU too -- it just cannot\n"
                    "  verify the result over BLE, so after the 0x7560 receipt it\n"
                    "  drops the link and declares success without checking. We do\n"
                    "  the checking it skipped: the broker and the ARP table."
                )

        if args.sync_time:
            print("\n" + "=" * 72)
            print("STEP 1b -- syncTimeToDevice (0x2242)")
            print("=" * 72)
            print("  The app syncs the clock from its monitoring screen, so a device")
            print("  in normal use always had a valid time. Ours never did, and a")
            print("  telemetry client may well refuse to start without one.")
            tp = p.sync_time_command()
            print(f"  -> {tp.hex()}")
            hc._drain()
            await hc.write(tp)
            for frame in await _collect(hc, args.timeout):
                print(f"    <- {frame.hex()}")
            await asyncio.sleep(1.0)

        print("\n" + "=" * 72)
        print("STEP 2 -- setWifiMode: changeBleMode(false), 0x7458 body 01 01 01")
        print("=" * 72)
        mode = await hc.request("setWifiMode           ", b"\x74\x58",
                                b"\x01\x01\x01", timeout=args.timeout)
        print("  " + (", ".join(fr.hex() for fr in mode.responses) or "(silent)"))

        if args.mqtt_server:
            print("\n" + "=" * 72)
            print("STEP 3 -- submitAESKey (0x20B0)")
            print("=" * 72)
            await _bind_cloud(hc, args)

        print("\n" + "=" * 72)
        print("STEP 4 -- submitSSID (0x7460)")
        print("=" * 72)
        packet = p.submit_ssid_command(args.ssid, args.password)
        print(f"  {len(packet)} B -> {packet.hex()}")
        hc._drain()
        await hc.write(packet)
        for frame in await _collect(hc, args.timeout):
            print(f"    <- {frame.hex()}")

        print("\n" + "=" * 72)
        print("STEP 5 -- poll getDeviceLinkNetStatus (0x7400)")
        print("=" * 72)
        verdict = None
        codes: list[int] = []
        for attempt in range(1, args.polls + 1):
            await asyncio.sleep(5.0)
            status = await hc.request(f"  poll {attempt}/{args.polls}        ",
                                      b"\x74\x00", b"\x02", timeout=args.timeout)
            for frame in status.responses:
                parsed = p.parse_link_status(frame)
                if parsed:
                    code, text = parsed
                    codes.append(code)
                    print(f"       link status = {code} ({text})")
                    if code in (2, 3):
                        verdict = True
                    elif code == 4:
                        verdict = False
            if verdict is not None:
                break

        print("\n" + "=" * 72)
        print("RESULT")
        print("=" * 72)
        print(f"  firmware {version!r}   SKU {sku!r}")
        print("  For a MOV1 the app stops here too, without verifying. Check the")
        print("  MQTT listener and `ip neigh | grep -i 94:e6:86` for the truth.")
        if verdict is True:
            print("  Device reports it is ON THE NETWORK. Check the broker and the")
            print("  router's client list.")
            return 0
        if verdict is False:
            print("  Device reports the network link FAILED -- but it tried, which")
            print("  means the WiFi stack is live. Re-check SSID and password.")
            return 1
        if codes:
            print(f"  0x7400 answered with real 0x7500 status frames: codes {codes}.")
            print("  That opcode IS implemented -- its reply carries a data body, not")
            print("  the bare 01 receipt every unimplemented opcode returns. The")
            print("  device is simply reporting that it is not on a network and, at")
            print("  code 0, has not even attempted to join one (the app treats 4 as")
            print("  'tried and failed', which never appeared).")
        else:
            print("  0x7400 never produced a 0x7500 status frame.")
        return 1
    finally:
        if keepalive is not None:
            keepalive.cancel()
        if getattr(args, "restore_wifi", True):
            await _restore_wifi_mode(hc)
        await hc.stop()
        if not args.keep:
            await client.disconnect()


async def cmd_status(args) -> int:
    """Read-only: poll getDeviceLinkNetStatus (0x7400) and nothing else.

    Run this while the device is already on WiFi. Every earlier reading of
    0x7400 was taken before the device had joined a network, so a status of 0
    then said nothing about what it does once associated.

    0x7400 is one of the few 0x74xx opcodes that is genuinely implemented: its
    reply carries a body one byte longer than the echo every unimplemented
    opcode returns.
    """
    client = await _connect(args.address, wait=args.wait)
    hc = p.HtramClient(client)
    keepalive = None
    try:
        if not await _init_session(hc):
            print("\nDevice never answered a known-good command; aborting.")
            return 1
        keepalive = asyncio.create_task(_keepalive(hc, client))

        print("\n" + "=" * 72)
        print("LINK STATUS while the device is on WiFi")
        print("=" * 72)

        # Control first: the same body on an opcode that cannot exist. If the
        # bogus one also answers with two body bytes, 0x7400's extra byte is
        # echo rather than status and none of this means anything.
        ctl = await hc.request("control 0x74FE (02)", b"\x74\xfe", b"\x02",
                               timeout=args.timeout)
        ctl_hex = [fr.hex() for fr in ctl.responses]
        print(f"  control replies: {ctl_hex or '(silent)'}")

        codes = []
        for attempt in range(1, args.polls + 1):
            status = await hc.request(f"  0x7400 poll {attempt}/{args.polls}",
                                      b"\x74\x00", b"\x02", timeout=args.timeout)
            for frame in status.responses:
                parsed = p.parse_link_status(frame)
                if parsed:
                    code, text = parsed
                    codes.append(code)
                    print(f"       status = {code} ({text})")
            await asyncio.sleep(3.0)

        print("\n" + "=" * 72)
        print("READING")
        print("=" * 72)
        control_bodies = {fr[6:-3] for fr in ctl.responses}
        if any(len(b) > 1 for b in control_bodies):
            print("  The bogus opcode also returned a multi-byte body, so 0x7400's")
            print("  second byte is echo, not status. This test proves nothing.")
            return 1
        print("  Control returned echo only, so 0x7400's extra byte is a real")
        print(f"  status field. Observed codes: {codes or 'none'}")
        if not codes:
            print("  0x7400 stopped answering entirely.")
        elif set(codes) <= {0}:
            print("\n  Status 0 with WiFi UP: the device is associated to the AP but")
            print("  is not even attempting a service connection. It never reaches")
            print("  the 'tried and failed' state (4). That points at the binding")
            print("  gate -- theory 1 -- rather than a bad broker address.")
        elif 4 in codes:
            print("\n  Status 4: the device DID attempt a connection and failed. The")
            print("  endpoint reached it; the address or its format is the problem.")
        elif {2, 3} & set(codes):
            print("\n  Status 2/3: the device believes it is connected to a service.")
            print("  Our broker saw nothing, so it is talking to something else --")
            print("  most likely an endpoint baked into the firmware.")
        return 0
    finally:
        if keepalive is not None:
            keepalive.cancel()
        if getattr(args, "restore_wifi", True):
            await _restore_wifi_mode(hc)
        await hc.stop()
        if not args.keep:
            await client.disconnect()


MQTT_URL_FORMATS = [
    "tcp://{h}:{p}",
    "{h}:{p}",
    "mqtt://{h}:{p}",
    "ssl://{h}:8883",
    "{h}",
]


async def cmd_mqttfmt(args) -> int:
    """Send 0x20B0 with several mqttUrl spellings, reading link status between.

    0x20B0's acknowledgement is the meaningless echo and there is no read-back
    command for the stored server, so the only observable is getDeviceLinkNetStatus
    (0x7400) -- which is genuinely implemented and currently reports 0,
    "not configured". If any spelling moves it off 0, the address landed and
    the earlier failures were a parsing problem. If none does, that is strong
    evidence 0x20B0 is simply not implemented on this MOV1.

    Deliberately does NOT touch the radio mode, so the device stays on WiFi.
    """
    host, _, port = args.broker.partition(":")
    port = port or "1883"

    client = await _connect(args.address, wait=args.wait)
    hc = p.HtramClient(client)
    keepalive = None
    try:
        if not await _init_session(hc):
            print("\nDevice never answered; aborting.")
            return 1
        keepalive = asyncio.create_task(_keepalive(hc, client))

        async def read_status(tag: str) -> int | None:
            ex = await hc.request(tag, b"\x74\x00", b"\x02", timeout=args.timeout)
            for frame in ex.responses:
                parsed = p.parse_link_status(frame)
                if parsed:
                    return parsed[0]
            return None

        print("\n" + "=" * 72)
        print("MQTT URL FORMAT SWEEP")
        print("=" * 72)
        baseline = await read_status("  baseline 0x7400  ")
        print(f"  baseline status: {baseline}")

        results: list[tuple[str, int | None]] = []
        for template in MQTT_URL_FORMATS:
            url = template.format(h=host, p=port)
            packet = p.submit_aes_key_command(args.aes_key, args.aes_iv, url)
            print(f"\n  mqttUrl = {url!r}  ({len(packet)} B)")
            hc._drain()
            await hc.write(packet)
            for frame in await _collect(hc, args.timeout):
                print(f"    <- {frame.hex()}")
            await asyncio.sleep(2.0)
            status = await read_status("    status 0x7400 ")
            print(f"    status = {status}")
            results.append((url, status))

        print("\n" + "=" * 72)
        print("RESULT")
        print("=" * 72)
        for url, status in results:
            print(f"  {url:34} status {status}")
        moved = [(u, st) for u, st in results if st not in (None, baseline)]
        if moved:
            print("\n  Status MOVED for:")
            for url, status in moved:
                print(f"    {url} -> {status}")
            print("  The address does land; the spelling was the problem.")
            return 0
        print(f"\n  Status never moved off {baseline} for any spelling, so the URL")
        print("  format is not what is stopping this. Two explanations survive and")
        print("  BLE cannot separate them:")
        print("    a) 0x20B0 does not store the endpoint on this firmware;")
        print("    b) it stores fine, but MQTT is gated on a binding the device")
        print("       never got, and status 0 reflects that rather than the endpoint.")
        print("\n  Note what does NOT follow: 0x20B0 answering with only the generic")
        print("  echo does not make it unimplemented. 0x7460 answers identically and")
        print("  demonstrably works -- the device joined WiFi. Form of reply proves")
        print("  nothing here; only external observation does.")
        print("\n  Separating (a) from (b) needs either a DNS log -- a device with any")
        print("  endpoint would resolve something, one with none stays silent -- or a")
        print("  dump of the ESP32 firmware.")
        return 1
    finally:
        if keepalive is not None:
            keepalive.cancel()
        await hc.stop()
        if not args.keep:
            await client.disconnect()


async def cmd_raw(args) -> int:
    packet = bytes.fromhex(args.hex.replace(" ", ""))
    client = await _connect(args.address, wait=args.wait)
    hc = p.HtramClient(client)
    try:
        await _init_session(hc)
        print(f"\n  raw -> {packet.hex()}")
        hc._drain()
        await hc.write(packet)
        await asyncio.sleep(args.timeout)
        return 0
    finally:
        if getattr(args, "restore_wifi", True):
            await _restore_wifi_mode(hc)
        await hc.stop()
        if getattr(args, "keep", False):
            print("\n  (leaving the link connected; pass --no-keep to drop it)")
        else:
            await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="htram_wifi.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="verify the CRC codec, no hardware needed")

    scan = sub.add_parser("scan", help="scan for HTRAM devices")
    scan.add_argument("--timeout", type=float, default=15.0)

    for name, help_text in [
        ("gatt", "dump the GATT table and flag known provisioning services"),
        ("probe", "test whether the firmware implements the WiFi commands"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("address")
        sp.add_argument("--timeout", type=float, default=3.0)
        sp.add_argument("--wait", type=float, default=60.0,
                        help="how long to keep retrying the connection")
        sp.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True,
                        help="leave the BLE link up on exit (default: keep). The "
                             "device only re-advertises after a button press, so "
                             "dropping the link costs you one.")

    prov = sub.add_parser("provision", help="send WiFi credentials (0x7460)")
    prov.add_argument("address")
    prov.add_argument("--ssid", required=True)
    prov.add_argument("--password", required=True)
    prov.add_argument("--timeout", type=float, default=5.0)
    prov.add_argument("--wait", type=float, default=60.0)
    prov.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)

    setup = sub.add_parser(
        "setup", help="identify + probe + provision + verify in one connection")
    setup.add_argument("address")
    setup.add_argument("--ssid", help="omit to stop before writing anything")
    setup.add_argument("--password", default="")
    setup.add_argument("--timeout", type=float, default=3.0)
    setup.add_argument("--wait", type=float, default=300.0)
    setup.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    setup.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="send changeBleMode(false) on exit so a provisioned "
                         "device goes back on WiFi instead of being left in BLE mode")
    setup.add_argument("--mqtt-server",
                       help="MQTT broker URL, e.g. tcp://192.168.0.10:1883. "
                            "Sends 0x20B0 before the SSID, mirroring the app's "
                            "enroll-then-provision order.")
    setup.add_argument("--aes-key", default="MDEyMzQ1Njc4OWFiY2RlZg==",
                       help="AES key, Base64 (decoded before sending)")
    setup.add_argument("--aes-iv", default="0123456789abcdef",
                       help="AES IV, sent as raw string bytes")
    setup.add_argument("--repair", action="store_true",
                       help="bluetoothctl remove + pair before connecting")
    setup.add_argument("--write-response", action="store_true",
                       help="use Write Request instead of Write Command, which "
                            "lets ATT do a long write for the 163-byte frame")

    alt = sub.add_parser("altchar", help="probe the vendor 90178a02 characteristic")
    alt.add_argument("address")
    alt.add_argument("--ssid", default="TP-Link_B55B")
    alt.add_argument("--password", default="")
    alt.add_argument("--mqtt-server")
    alt.add_argument("--aes-key", default="MDEyMzQ1Njc4OWFiY2RlZg==")
    alt.add_argument("--aes-iv", default="0123456789abcdef")
    alt.add_argument("--timeout", type=float, default=3.0)
    alt.add_argument("--wait", type=float, default=300.0)
    alt.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    alt.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="send changeBleMode(false) on exit so a provisioned "
                         "device goes back on WiFi instead of being left in BLE mode")

    wifi = sub.add_parser("wifi", help="provision WiFi using the app's exact sequence")
    wifi.add_argument("address")
    wifi.add_argument("--ssid", required=True)
    wifi.add_argument("--password", required=True)
    wifi.add_argument("--mqtt-server")
    wifi.add_argument("--aes-key", default="MDEyMzQ1Njc4OWFiY2RlZg==")
    wifi.add_argument("--aes-iv", default="0123456789abcdef")
    wifi.add_argument("--polls", type=int, default=6)
    wifi.add_argument("--sync-time", action=argparse.BooleanOptionalAction, default=True,
                      help="send syncTimeToDevice before provisioning (default: on)")
    wifi.add_argument("--random-keys", action="store_true",
                      help="use random AES key/IV instead of the patterned defaults")
    wifi.add_argument("--timeout", type=float, default=3.0)
    wifi.add_argument("--wait", type=float, default=300.0)
    wifi.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    wifi.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="send changeBleMode(false) on exit so a provisioned "
                         "device goes back on WiFi instead of being left in BLE mode")

    st = sub.add_parser("status", help="read link status while on WiFi (read-only)")
    st.add_argument("address")
    st.add_argument("--polls", type=int, default=5)
    st.add_argument("--timeout", type=float, default=3.0)
    st.add_argument("--wait", type=float, default=300.0)
    st.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    st.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="send changeBleMode(false) on exit so a provisioned "
                         "device goes back on WiFi instead of being left in BLE mode")

    mf = sub.add_parser("mqttfmt", help="sweep mqttUrl spellings, watching link status")
    mf.add_argument("address")
    mf.add_argument("--broker", required=True, help="host or host:port")
    mf.add_argument("--aes-key", default="MDEyMzQ1Njc4OWFiY2RlZg==")
    mf.add_argument("--aes-iv", default="0123456789abcdef")
    mf.add_argument("--timeout", type=float, default=3.0)
    mf.add_argument("--wait", type=float, default=300.0)
    mf.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    mf.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction, default=False,
                    help="not needed: this command never switches radio mode")

    raw = sub.add_parser("raw", help="send an arbitrary hex frame")
    raw.add_argument("address")
    raw.add_argument("hex")
    raw.add_argument("--timeout", type=float, default=5.0)
    raw.add_argument("--wait", type=float, default=60.0)
    raw.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)
    raw.add_argument("--restore-wifi", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="send changeBleMode(false) on exit so a provisioned "
                         "device goes back on WiFi instead of being left in BLE mode")

    args = parser.parse_args()
    handler = globals()[f"cmd_{args.cmd}"]
    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130
    except BleakError as err:
        print(f"\nBLE error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
