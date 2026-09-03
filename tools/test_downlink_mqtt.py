#!/usr/bin/env python3
"""
Test Downlink command via MQTT WSS on D/<serial>.

Format recovered from GD32F150 firmware dump (function at 0x08008FE8):
  [0:5]   Magic + version: b"DC\x00\x02\x00"
  [5]     Command Type: 0x04 (or 0x03)
  [6:10]  Transaction ID / timestamp (uint32 LE)
  [10:12] SKU check: 0x51 0x06 (must match telemetry bytes 10-11)
  [12:15] Length: 0x00 0x00 0x0E (14 bytes body)
  [15:17] High alarm threshold (uint16 LE, e.g. 1000)
  [17:19] Low alarm threshold (uint16 LE, e.g. 800)
  [19:21] Brightness (uint16 LE, 0..100)
  [21:23] Auto screen-off timeout (uint16 LE: 0 = Always ON, 1 = 2 min)
  [23:25] Temperature unit (uint16 LE: 0 = Celsius, 1 = Fahrenheit)
  [25:27] Screen display (uint16 LE: 1 = ON, 0 = OFF)
  [27:29] Buzzer / sound (uint16 LE: 0 = Sound ON, 1 = Muted)
  [29:31] CRC-16 over [15:29] (uint16 LE)

Total frame length: exactly 31 bytes.
"""

import argparse
import base64
import os
import socket
import ssl
import struct
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from custom_components.htram.protocol import crc16, decode_telemetry


def build_downlink_frame(
    high_threshold: int = 1000,
    low_threshold: int = 800,
    brightness: int = 100,   # [19:21] Brightness: 0..100 %
    auto_off: int = 0,       # [21:23] Auto screen-off: 0 = Always ON, 1 = 2 min timeout
    temp_unit: int = 0,      # [23:25] Temp unit: 0 = C, 1 = F
    screen_on: int = 1,      # [25:27] Screen display: 1 = ON, 0 = OFF (Turn Off Display)
    buzzer: int = 0,         # [27:29] Buzzer: 0 = Sound ON, 1 = Mute
    transaction_id: int | None = None,
    sku: bytes = b"\x51\x06",
) -> bytes:
    if transaction_id is None:
        transaction_id = int(time.time())

    header = bytearray()
    header += b"DC\x00\x02\x00"                  # [0:5]
    header += b"\x04"                            # [5] Command: 0x04
    header += struct.pack("<I", transaction_id)  # [6:10] Transaction ID
    header += sku                                # [10:12] SKU (0x51, 0x06)
    header += b"\x00\x00\x0e"                    # [12:15] Length: 14 bytes body

    body = bytearray()
    body += struct.pack("<H", high_threshold)    # [15:17] High alarm threshold
    body += struct.pack("<H", low_threshold)     # [17:19] Low alarm threshold
    body += struct.pack("<H", brightness)        # [19:21] Brightness (0..100%)
    body += struct.pack("<H", auto_off)          # [21:23] Auto screen-off (0=always on, 1=2min)
    body += struct.pack("<H", temp_unit)         # [23:25] Temp unit: 0=C, 1=F
    body += struct.pack("<H", screen_on)         # [25:27] Screen: 1=ON, 0=OFF
    body += struct.pack("<H", buzzer)            # [27:29] Buzzer: 0=Sound ON, 1=Mute

    chk = struct.pack("<H", crc16(body))         # [29:31] CRC-16

    frame = bytes(header + body + chk)
    assert len(frame) == 31, f"Frame must be 31 bytes, got {len(frame)}"
    return frame


def ws_connect(broker_ip: str, port: int, sni: str) -> ssl.SSLSocket:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw_sock = socket.create_connection((broker_ip, port), timeout=10)
    s = ctx.wrap_socket(raw_sock, server_hostname=sni)

    key = base64.b64encode(os.urandom(16)).decode()
    upgrade_req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {sni}:{port}\r\n"
        f"Connection: Upgrade\r\n"
        f"Upgrade: websocket\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Protocol: mqtt\r\n\r\n"
    ).encode()
    s.sendall(upgrade_req)
    time.sleep(0.3)
    resp = s.recv(4096)
    if b"101" not in resp:
        raise RuntimeError(f"WebSocket handshake failed: {resp[:100]}")
    return s


def ws_frame(payload: bytes) -> bytes:
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        hdr = bytes([0x82, 0x80 | length])
    else:
        hdr = bytes([0x82, 0x80 | 126]) + struct.pack(">H", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return hdr + mask + masked


def mqtt_encode_str(s: str | bytes) -> bytes:
    b = s.encode() if isinstance(s, str) else s
    return struct.pack(">H", len(b)) + b


def mqtt_publish(topic: str, payload: bytes) -> bytes:
    body = mqtt_encode_str(topic) + payload
    length = len(body)
    rem_len = bytearray()
    while True:
        d = length % 128
        length //= 128
        rem_len.append(d | (0x80 if length else 0))
        if not length:
            break
    return bytes([0x30]) + rem_len + body


def main():
    parser = argparse.ArgumentParser(description="Send MQTT Downlink to HTRAM device")
    parser.add_argument("--broker", default="192.168.0.45", help="Broker IP")
    parser.add_argument("--port", type=int, default=443, help="Broker port")
    parser.add_argument("--sni", default="mqtt.kms-lab.in.ua", help="TLS SNI")
    parser.add_argument("--target", default="RM1221412257", help="Target serial number")
    parser.add_argument("--unit", choices=["C", "F"], default="C", help="Temperature unit (C or F)")
    parser.add_argument("--brightness", type=int, default=100, help="Brightness (0..100 percent)")
    parser.add_argument("--auto-off", type=int, choices=[0, 1], default=0, help="Auto screen off (0 = always on, 1 = 2 min timeout)")
    parser.add_argument("--buzzer", type=int, choices=[0, 1], default=0, help="Buzzer (0=On, 1=Mute)")
    parser.add_argument("--screen-on", type=int, choices=[0, 1], default=1, help="Screen State: 1 = ON, 0 = OFF (Turn Off Display)")
    parser.add_argument("--low", type=int, default=800, help="Low CO2 alarm threshold")
    parser.add_argument("--high", type=int, default=1000, help="High CO2 alarm threshold")
    args = parser.parse_args()

    unit_val = 1 if args.unit == "F" else 0

    print(f"Connecting to {args.broker}:{args.port} (SNI: {args.sni})...")
    s = ws_connect(args.broker, args.port, args.sni)
    print("WebSocket connected!")

    # MQTT Connect packet
    cid = mqtt_encode_str("downlink-tool")
    user = mqtt_encode_str("ha-bridge")
    pwd = mqtt_encode_str("x")
    var_hdr = b"\x00\x04MQTT\x04\xc2" + struct.pack(">H", 60)
    conn_payload = var_hdr + cid + user + pwd
    s.sendall(ws_frame(bytes([0x10, len(conn_payload)]) + conn_payload))
    time.sleep(0.3)
    s.recv(1024)
    print("MQTT connected!")

    # Subscribe to telemetry topic C/<serial>
    c_topic = f"C/{args.target}"
    d_topic = f"D/{args.target}"
    sub_payload = struct.pack(">H", 1) + mqtt_encode_str(c_topic) + b"\x00"
    s.sendall(ws_frame(bytes([0x82, len(sub_payload)]) + sub_payload))
    print(f"Subscribed to {c_topic}")

    # Build downlink frame
    frame = build_downlink_frame(
        high_threshold=args.high,
        low_threshold=args.low,
        brightness=args.brightness,
        auto_off=args.auto_off,
        temp_unit=unit_val,
        screen_on=args.screen_on,
        buzzer=args.buzzer,
    )

    print("\n" + "=" * 60)
    print(f"Target:       {args.target}")
    print(f"Downlink topic: {d_topic}")
    print(f"Parameters:   Brightness={args.brightness}%, AutoOff={args.auto_off}, Unit={args.unit}, ScreenOn={args.screen_on}, Buzzer={args.buzzer}, Low={args.low}, High={args.high}")
    print(f"Payload (31B): {frame.hex(' ')}")
    print("=" * 60 + "\n")

    print(f"Publishing to {d_topic}...")
    s.sendall(ws_frame(mqtt_publish(d_topic, frame)))
    print("Published successfully!")

    print(f"\nListening on {c_topic} for ACK and telemetry...")
    t0 = time.time()
    s.settimeout(1.0)
    got_ack = False
    got_telemetry = False

    while time.time() - t0 < 35:
        try:
            data = s.recv(4096)
            if not data:
                continue
            idx = 0
            while True:
                idx = data.find(b"DC\x00\x02", idx)
                if idx == -1:
                    break
                # Check for ACK (15 bytes) or Telemetry (27 bytes)
                pkt = data[idx:]
                if len(pkt) >= 15 and pkt[4] == 0x02 and pkt[5] == 0x04:
                    ack = pkt[:15]
                    tx_id = struct.unpack("<I", ack[6:10])[0]
                    print(f"\n✓ Instant ACK received from {args.target} ({len(ack)} bytes):")
                    print(f"  Hex:            {ack.hex(' ')}")
                    print(f"  Transaction ID: {tx_id}")
                    print(f"  Command:        0x{ack[5]:02X}")
                    got_ack = True
                    idx += 15
                elif len(pkt) >= 27 and pkt[4] == 0x00 and pkt[5] == 0x01:
                    raw = pkt[:27]
                    t = decode_telemetry(raw)
                    print(f"\n✓ Telemetry received from {args.target} ({len(raw)} bytes):")
                    print(f"  CO2:         {t.co2} ppm (Alarm level: {t.alarm_level})")
                    print(f"  Temperature: {t.temperature} °C")
                    print(f"  Humidity:    {t.humidity} %")
                    print(f"  Battery:     {t.battery}% ({t.battery_voltage}V, charging={t.charging})")
                    got_telemetry = True
                    idx += 27
                else:
                    idx += 4

            if got_ack and got_telemetry:
                break
        except socket.timeout:
            pass

    print("\nDone!")


if __name__ == "__main__":
    main()
