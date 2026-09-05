#!/usr/bin/env python3
"""Static server for the design studio, plus POST /save so the page can drop a
PNG of the current render next to itself for review."""
import base64, http.server, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "shots")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_POST(self):
        if self.path != "/save":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        name, _, b64 = self.rfile.read(n).decode().partition("|")
        name = os.path.basename(name) or "shot.png"
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(base64.b64decode(b64))
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"design studio: http://localhost:{port}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
