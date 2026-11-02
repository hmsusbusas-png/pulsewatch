"""Local HTTP target used to exercise the monitor against private addresses."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

STATE = {"healthy": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/state/"):
            STATE["healthy"] = self.path.endswith("/up")
            self._respond(200, json.dumps(STATE))
            return
        if not STATE["healthy"]:
            self._respond(503, "down")
            return
        if self.path == "/slow":
            time.sleep(3)
        self._respond(200, "ok")

    def _respond(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local target for pulsewatch demos")
    parser.add_argument("--port", type=int, default=9099)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    print(f"local target on http://127.0.0.1:{args.port}", flush=True)
    print("/state/up and /state/down flip the response, /slow blocks for 3 seconds", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
