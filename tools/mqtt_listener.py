#!/usr/bin/env python3
"""Minimal MQTT 3.1.1 broker, just enough for an HTRAM to connect and talk.

Not a real broker: it accepts any client, acknowledges everything, and logs
every packet. The point is evidence -- if the device ever reaches this, its
CONNECT carries a client id, which proves the WiFi provisioning worked.

    .venv/bin/python tools/mqtt_listener.py --host 0.0.0.0 --port 1883

Stdlib only, and port 1883 needs no privileges.
"""

from __future__ import annotations

import argparse
import socket
import socketserver
import threading
from datetime import datetime

PACKET_TYPES = {
    1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC",
    6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK",
    10: "UNSUBSCRIBE", 11: "UNSUBACK", 12: "PINGREQ", 13: "PINGRESP",
    14: "DISCONNECT",
}

_print_lock = threading.Lock()


def log(peer: str, message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    with _print_lock:
        print(f"[{stamp}] {peer:21} {message}", flush=True)


def _read_exact(conn: socket.socket, count: int) -> bytes | None:
    buf = b""
    while len(buf) < count:
        chunk = conn.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_remaining_length(conn: socket.socket) -> int | None:
    value = 0
    for shift in range(0, 28, 7):
        byte = _read_exact(conn, 1)
        if byte is None:
            return None
        value |= (byte[0] & 0x7F) << shift
        if not byte[0] & 0x80:
            return value
    return None


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length = int.from_bytes(data[offset : offset + 2], "big")
    start = offset + 2
    return data[start : start + length].decode("utf-8", "replace"), start + length


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        conn: socket.socket = self.request
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        log(peer, "TCP CONNECTED  <-- something reached the broker")
        try:
            while True:
                header = _read_exact(conn, 1)
                if header is None:
                    break
                ptype = header[0] >> 4
                remaining = _read_remaining_length(conn)
                if remaining is None:
                    break
                body = _read_exact(conn, remaining) if remaining else b""
                if body is None:
                    break
                name = PACKET_TYPES.get(ptype, f"UNKNOWN({ptype})")

                if ptype == 1:  # CONNECT
                    try:
                        protocol, offset = _read_string(body, 0)
                        level = body[offset]
                        flags = body[offset + 1]
                        keepalive = int.from_bytes(body[offset + 2 : offset + 4], "big")
                        client_id, _ = _read_string(body, offset + 4)
                        log(peer, f"CONNECT protocol={protocol!r} level={level} "
                                  f"flags=0x{flags:02x} keepalive={keepalive}s")
                        log(peer, f"        client_id={client_id!r}")
                    except Exception:
                        log(peer, f"CONNECT (unparsed) {body.hex()}")
                    conn.sendall(b"\x20\x02\x00\x00")  # CONNACK, accepted
                elif ptype == 3:  # PUBLISH
                    qos = (header[0] >> 1) & 0x03
                    topic, offset = _read_string(body, 0)
                    packet_id = None
                    if qos:
                        packet_id = int.from_bytes(body[offset : offset + 2], "big")
                        offset += 2
                    payload = body[offset:]
                    log(peer, f"PUBLISH topic={topic!r} qos={qos} "
                              f"payload={payload.hex()}")
                    printable = "".join(
                        chr(b) if 32 <= b < 127 else "." for b in payload
                    )
                    log(peer, f"        ascii={printable}")
                    if qos == 1 and packet_id is not None:
                        conn.sendall(b"\x40\x02" + packet_id.to_bytes(2, "big"))
                elif ptype == 8:  # SUBSCRIBE
                    packet_id = int.from_bytes(body[:2], "big")
                    offset = 2
                    topics = []
                    while offset < len(body):
                        topic, offset = _read_string(body, offset)
                        topics.append((topic, body[offset]))
                        offset += 1
                    log(peer, f"SUBSCRIBE {topics}")
                    conn.sendall(
                        b"\x90" + bytes([2 + len(topics)])
                        + packet_id.to_bytes(2, "big") + b"\x00" * len(topics)
                    )
                elif ptype == 12:  # PINGREQ
                    log(peer, "PINGREQ")
                    conn.sendall(b"\xd0\x00")
                elif ptype == 14:  # DISCONNECT
                    log(peer, "DISCONNECT")
                    break
                else:
                    log(peer, f"{name} {body.hex()}")
        except (ConnectionResetError, OSError) as err:
            log(peer, f"connection error: {err}")
        finally:
            log(peer, "TCP CLOSED")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()
    with Server((args.host, args.port), Handler) as server:
        print(f"Listening on {args.host}:{args.port} -- any TCP connection here "
              f"is proof the device joined the network.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
