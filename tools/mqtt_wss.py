#!/usr/bin/env python3
"""MQTT-over-WebSocket-Secure broker, just enough for the HTRAM to connect.

The device does NOT speak plain MQTT. Captured from the AT link between its
GD32 and its ESP32 modem:

    AT+MQTTUSERCFG=0,7,"RM1221412257","1617:V1.00 :45179","<sha256>",0,0,
    AT+MQTTCONN=0,"192.168.0.56",443,0

ESP-AT scheme 7 is MQTT over WebSocket Secure, and the port is hardcoded to
443 in the GD32 -- it ignores any port in the URL. So reaching this device
needs TLS + a WebSocket upgrade + MQTT inside, on 443. Scheme 7 does not
verify the server certificate, so a self-signed one is fine.

Port 443 is privileged:

    sudo .venv/bin/python tools/mqtt_wss.py --cert htram-cert.pem --key htram-key.pem

Stdlib only. Not a real broker -- it accepts everything and logs everything,
because the point is to see what the device says.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time
from datetime import datetime

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

MQTT_TYPES = {
    1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC",
    6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK",
    10: "UNSUBSCRIBE", 11: "UNSUBACK", 12: "PINGREQ", 13: "PINGRESP",
    14: "DISCONNECT",
}

_lock = threading.Lock()
_logfile = None
_clients: dict[str, "WSFramer"] = {}      # client_id -> framer
_clients_lock = threading.Lock()


def log(peer: str, msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {peer:21} {msg}"
    with _lock:
        print(line, flush=True)
        if _logfile is not None:
            _logfile.write(line + "\n")
            _logfile.flush()


def ws_accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


class WSFramer:
    """Minimal RFC 6455 framing. The device is the client, so its frames are
    masked and ours must not be."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = bytearray()

    def _recv_into_buf(self) -> bool:
        chunk = self.sock.recv(4096)
        if not chunk:
            return False
        self.buf.extend(chunk)
        return True

    def read_message(self) -> tuple[int, bytes] | None:
        """Return (opcode, payload) for one complete frame, or None on close."""
        while True:
            if len(self.buf) < 2:
                if not self._recv_into_buf():
                    return None
                continue
            b0, b1 = self.buf[0], self.buf[1]
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            offset = 2
            if length == 126:
                if len(self.buf) < 4:
                    if not self._recv_into_buf():
                        return None
                    continue
                length = struct.unpack(">H", self.buf[2:4])[0]
                offset = 4
            elif length == 127:
                if len(self.buf) < 10:
                    if not self._recv_into_buf():
                        return None
                    continue
                length = struct.unpack(">Q", self.buf[2:10])[0]
                offset = 10
            mask = b""
            if masked:
                if len(self.buf) < offset + 4:
                    if not self._recv_into_buf():
                        return None
                    continue
                mask = bytes(self.buf[offset:offset + 4])
                offset += 4
            if len(self.buf) < offset + length:
                if not self._recv_into_buf():
                    return None
                continue
            payload = bytes(self.buf[offset:offset + length])
            del self.buf[:offset + length]
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            return opcode, payload

    def send(self, payload: bytes, opcode: int = 0x2) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header.extend(struct.pack(">H", n))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", n))
        self.sock.sendall(bytes(header) + payload)


def read_mqtt_string(data: bytes, offset: int) -> tuple[str, int]:
    length = int.from_bytes(data[offset:offset + 2], "big")
    start = offset + 2
    return data[start:start + length].decode("utf-8", "replace"), start + length




def mqtt_publish(topic: str, payload: bytes) -> bytes:
    """Build an MQTT PUBLISH packet, QoS 0."""
    body = len(topic).to_bytes(2, "big") + topic.encode() + payload
    header = bytearray([0x30])
    n = len(body)
    while True:
        byte = n % 128
        n //= 128
        header.append(byte | (0x80 if n else 0))
        if not n:
            break
    return bytes(header) + body


def outbox_worker(path: str, poll: float = 1.0) -> None:
    """Watch a file for lines to publish downlink.

    The broker runs as root on port 443 while analysis runs as the user, so a
    file is the simplest channel between them. Each line is
    `<client_id> <hex>` or just `<hex>` to use the sole connected client.
    Lines are consumed as they appear; blank lines and `#` comments ignored.
    """
    import os
    offset = 0
    while True:
        time.sleep(poll)
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                fh.seek(offset)
                lines = fh.readlines()
                offset = fh.tell()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and not all(c in "0123456789abcdefABCDEF" for c in parts[0]):
                cid, hexdata = parts
            else:
                with _clients_lock:
                    cid = next(iter(_clients), None)
                hexdata = line
            if cid is None:
                log("outbox", f"no client connected, dropping {hexdata[:40]}")
                continue
            try:
                payload = bytes.fromhex(hexdata.replace(" ", ""))
            except ValueError:
                log("outbox", f"not hex: {line[:60]}")
                continue
            topic = f"D/{cid}"
            with _clients_lock:
                ws = _clients.get(cid)
            if ws is None:
                log("outbox", f"client {cid} not connected")
                continue
            try:
                ws.send(mqtt_publish(topic, payload))
                log("outbox", f"-> {topic}  {len(payload)} B  {payload.hex()}")
            except Exception as err:
                log("outbox", f"send failed: {err}")


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        log(peer, "TCP connected")
        try:
            tls = self.server.ssl_context.wrap_socket(self.request, server_side=True)
        except ssl.SSLError as err:
            log(peer, f"TLS handshake failed: {err}")
            return
        log(peer, f"TLS up ({tls.version()}, {tls.cipher()[0] if tls.cipher() else '?'})")

        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = tls.recv(4096)
                if not chunk:
                    log(peer, "closed before completing the HTTP request")
                    return
                request += chunk

            head = request.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            log(peer, "HTTP upgrade request:")
            for line in head.splitlines():
                log(peer, f"    {line}")

            key = ""
            for line in head.splitlines()[1:]:
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if not key:
                log(peer, "no Sec-WebSocket-Key -- not a WebSocket client")
                return

            tls.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + ws_accept_key(key).encode() + b"\r\n"
                b"Sec-WebSocket-Protocol: mqtt\r\n\r\n"
            )
            log(peer, "WebSocket established, subprotocol mqtt")

            ws = WSFramer(tls)
            pending = bytearray()
            while True:
                msg = ws.read_message()
                if msg is None:
                    break
                opcode, payload = msg
                if opcode == 0x8:
                    log(peer, "WebSocket close")
                    break
                if opcode == 0x9:  # ping
                    ws.send(payload, opcode=0xA)
                    continue
                if opcode not in (0x1, 0x2, 0x0):
                    continue
                pending.extend(payload)
                self._drain_mqtt(peer, ws, pending)
        except (ConnectionResetError, OSError, ssl.SSLError) as err:
            log(peer, f"connection error: {err}")
        finally:
            cid = getattr(self, "client_id", None)
            if cid:
                with _clients_lock:
                    _clients.pop(cid, None)
            log(peer, "disconnected")

    def _drain_mqtt(self, peer: str, ws: WSFramer, buf: bytearray) -> None:
        while len(buf) >= 2:
            ptype = buf[0] >> 4
            flags = buf[0] & 0x0F
            length, consumed = 0, 0
            for i in range(1, min(5, len(buf))):
                length |= (buf[i] & 0x7F) << (7 * (i - 1))
                consumed = i
                if not buf[i] & 0x80:
                    break
            else:
                return
            total = 1 + consumed + length
            if len(buf) < total:
                return
            body = bytes(buf[1 + consumed:total])
            del buf[:total]
            self._handle_mqtt(peer, ws, ptype, flags, body)

    def _handle_mqtt(self, peer, ws, ptype, flags, body) -> None:
        name = MQTT_TYPES.get(ptype, f"UNKNOWN({ptype})")
        if ptype == 1:  # CONNECT
            proto, off = read_mqtt_string(body, 0)
            level = body[off]
            cflags = body[off + 1]
            keepalive = int.from_bytes(body[off + 2:off + 4], "big")
            off += 4
            client_id, off = read_mqtt_string(body, off)
            log(peer, f"CONNECT proto={proto!r} v{level} flags=0x{cflags:02x} "
                      f"keepalive={keepalive}s")
            log(peer, f"        client_id={client_id!r}")
            if cflags & 0x04:  # will
                _, off = read_mqtt_string(body, off)
                _, off = read_mqtt_string(body, off)
            if cflags & 0x80:
                username, off = read_mqtt_string(body, off)
                log(peer, f"        username={username!r}")
                if cflags & 0x40:
                    password, off = read_mqtt_string(body, off)
                    log(peer, f"        password={password!r}")
                    expect = hashlib.sha256(username.encode()).hexdigest()
                    log(peer, f"        sha256(username) {'MATCHES' if expect == password else 'differs'}")
            ws.send(b"\x20\x02\x00\x00")  # CONNACK accepted
            log(peer, "        -> CONNACK accepted")
            with _clients_lock:
                _clients[client_id] = ws
            self.client_id = client_id
        elif ptype == 3:  # PUBLISH
            qos = (flags >> 1) & 3
            topic, off = read_mqtt_string(body, 0)
            pid = None
            if qos:
                pid = int.from_bytes(body[off:off + 2], "big")
                off += 2
            payload = body[off:]
            log(peer, f"PUBLISH topic={topic!r} qos={qos} len={len(payload)}")
            log(peer, f"        hex   {payload.hex()}")
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in payload)
            log(peer, f"        ascii {printable}")
            if qos == 1 and pid is not None:
                ws.send(b"\x40\x02" + pid.to_bytes(2, "big"))
        elif ptype == 8:  # SUBSCRIBE
            pid = int.from_bytes(body[:2], "big")
            off, topics = 2, []
            while off < len(body):
                topic, off = read_mqtt_string(body, off)
                topics.append((topic, body[off]))
                off += 1
            log(peer, f"SUBSCRIBE {topics}")
            ws.send(b"\x90" + bytes([2 + len(topics)]) + pid.to_bytes(2, "big")
                    + b"\x00" * len(topics))
        elif ptype == 12:
            log(peer, "PINGREQ")
            ws.send(b"\xd0\x00")
        elif ptype == 14:
            log(peer, "DISCONNECT")
        else:
            log(peer, f"{name} {body.hex()}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--cert", default="htram-cert.pem")
    ap.add_argument("--key", default="htram-key.pem")
    ap.add_argument("--log", help="also append every line to this file")
    ap.add_argument("--outbox", help="file watched for '<hex>' lines to publish "
                                     "to D/<client_id>")
    args = ap.parse_args()

    # Note: AT+MQTTCONN is issued with reconnect=0, so the device does NOT
    # retry on its own. Restarting this server costs a power cycle of the
    # device -- prefer leaving it up and reading the log file.
    global _logfile
    if args.log:
        _logfile = open(args.log, "a", encoding="utf-8")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(args.cert, args.key)
    # The device uses ESP-AT scheme 7, which does not verify the certificate,
    # but its TLS stack is old -- keep the floor low so it can negotiate.
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass

    if args.outbox:
        threading.Thread(target=outbox_worker, args=(args.outbox,),
                         daemon=True).start()

    server = Server((args.host, args.port), Handler)
    server.ssl_context = ctx
    print(f"MQTT-over-WSS listening on {args.host}:{args.port}")
    print(f"cert {args.cert}  key {args.key}")
    if args.log:
        print(f"logging to {args.log}")
    if args.outbox:
        print(f"outbox: append hex lines to {args.outbox} to publish downlink")
    print("Waiting for the HTRAM. Power-cycle it to trigger a connection.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
