"""Serve the lab's current campaign summary on this computer's loopback interface."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
from threading import Event, Thread
from urllib.parse import urlsplit

from aggregate_cache import cached, maintain

SITE = Path(__file__).resolve().parent
PORT = 8766
ASSETS = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/style.css": ("style.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
}


class Monitor(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # The monitor runs without a terminal and stores no visit history.
        pass

    def reply(self, code, mime_type, body):
        self.send_response(code)
        self.send_header("Content-Type", mime_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host", "").lower() not in {
            f"127.0.0.1:{PORT}", f"localhost:{PORT}"
        }:
            self.reply(403, "text/plain", b"Local requests only")
            return
        route = urlsplit(self.path).path
        if route == "/api/summary":
            try:
                result = cached()
            except (OSError, KeyError, ValueError, sqlite3.Error):
                self.reply(503, "application/json", b'{"error":"Campaign temporarily unavailable"}')
                return
            self.reply(200, "application/json", result)
        elif route in ASSETS:
            name, mime_type = ASSETS[route]
            try:
                body = (SITE / "ui" / name).read_bytes()
            except OSError:
                self.reply(503, "text/plain", b"Dashboard asset unavailable")
                return
            self.reply(200, mime_type, body)
        else:
            self.reply(404, "text/plain", b"Not found")

    do_HEAD = do_GET


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", PORT), Monitor) as server:
        stop = Event()
        thread = Thread(target=maintain, args=(stop,), daemon=True)
        thread.start()
        try:
            server.serve_forever()
        finally:
            stop.set()
            thread.join(5)
