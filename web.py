"""LAN-only browser screen/input proxy for the Station's local Xash viewer."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import os

ROOT = Path(__file__).resolve().parent
TARGET = os.environ.get("HALFLIFE_STATION_VIEWER", "http://127.0.0.1:12080")
HOST = os.environ.get("HALFLIFE_WEB_BIND", "127.0.0.1")
PORT = int(os.environ.get("HALFLIFE_WEB_PORT", "12081"))
FORWARD_PREFIXES = ("/input?", "/frame.png", "/status", "/performance", "/audio")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if not any(self.path.startswith(prefix) for prefix in FORWARD_PREFIXES):
            self.send_error(404)
            return
        self.forward()

    def do_POST(self):
        if not self.path.startswith("/audio?volume="):
            self.send_error(404)
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
            self.close_connection = True
            self.send_error(400, "Audio control accepts an empty body")
            return
        self.forward()

    def forward(self):
        try:
            request = Request(TARGET + self.path, method=self.command,
                              data=b"" if self.command == "POST" else None)
            try:
                response = urlopen(request, timeout=4)
            except HTTPError as error:
                response = error
            with response:
                data = response.read(4 * 1024 * 1024 + 1)
                if len(data) > 4 * 1024 * 1024:
                    self.send_error(502, "upstream response too large")
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
        except (OSError, URLError) as exc:
            self.send_error(502, f"Station viewer unavailable: {exc}")

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} {fmt % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Half-Life browser panel listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
