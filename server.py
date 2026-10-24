#!/usr/bin/env python3
"""Loopback-only streaming chat demo with server-side provider credentials."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from wire import StreamError, demo_events, endpoint_url, measure, stream

ROOT = Path(__file__).parent


def validate_messages(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 40:
        raise ValueError("Send between 1 and 40 messages")
    messages = []
    total = 0
    for message in value:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ValueError("Only user and assistant messages are supported")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Messages must contain nonempty text")
        total += len(text)
        messages.append({"role": message["role"], "content": text})
    if total > 24000 or messages[-1]["role"] != "user":
        raise ValueError("Conversation must end with a user message and contain at most 24000 characters")
    return messages


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log chat text, credentials or request bodies.

    def allowed(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        return (self.headers.get("Host") == expected and
                self.headers.get("Origin") in {None, f"http://{expected}"})

    def send_headers(self, status, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()

    def json_response(self, status, data):
        self.send_headers(status, "application/json")
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if not self.allowed():
            return self.json_response(403, {"error": "Open the exact loopback URL printed by the server"})
        if self.path == "/api/config":
            return self.json_response(200, {"demo": self.server.config.demo,
                "model": "synthetic-local" if self.server.config.demo else self.server.config.model,
                "max_tokens": self.server.config.max_tokens})
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in assets:
            return self.json_response(404, {"error": "Not found"})
        name, mime = assets[self.path]
        self.send_headers(200, mime)
        self.wfile.write((ROOT / "web" / name).read_bytes())

    def do_POST(self):
        if not self.allowed():
            return self.json_response(403, {"error": "Cross-origin requests are not allowed"})
        if self.path != "/api/chat":
            return self.json_response(404, {"error": "Not found"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json_response(415, {"error": "Expected application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 120000:
                raise ValueError("Body must be 1–120000 bytes")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            messages = validate_messages(body.get("messages"))
        except (ValueError, UnicodeError) as exc:
            return self.json_response(400, {"error": str(exc)})
        if not self.server.slots.acquire(blocking=False):
            return self.json_response(429, {"error": "A stream is already running; wait for it to finish"})
        try:
            self.send_headers(200, "application/x-ndjson")
            def emit(data):
                self.wfile.write((json.dumps(data) + "\n").encode())
                self.wfile.flush()
            config = self.server.config
            events = demo_events() if config.demo else stream(config.base_url,
                os.environ["INFERENCE_API_KEY"], config.model, messages,
                config.max_tokens, config.timeout, not config.no_usage)
            try:
                result = measure(events, emit)
                emit({"type": "metrics", "synthetic": config.demo, **result})
            except (StreamError, ValueError) as exc:
                emit({"type": "error", "message": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass  # Closing the downstream socket stops consumption; provider billing may continue.
        finally:
            self.server.slots.release()


def make_server(config):
    server = ThreadingHTTPServer(("127.0.0.1", config.port), Handler)
    server.config = config
    server.slots = threading.BoundedSemaphore(1)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--no-usage", action="store_true")
    args = parser.parse_args()
    args.base_url = os.getenv("INFERENCE_BASE_URL", "")
    args.model = os.getenv("INFERENCE_MODEL", "")
    if not 1 <= args.max_tokens <= 8192 or not 0 < args.timeout <= 600:
        parser.error("max-tokens must be 1–8192 and timeout (0,600]")
    if not args.demo:
        if not args.model or not os.getenv("INFERENCE_API_KEY"):
            parser.error("Set INFERENCE_BASE_URL, INFERENCE_MODEL and INFERENCE_API_KEY")
        try:
            endpoint_url(args.base_url)
        except ValueError as exc:
            parser.error(str(exc))
    server = make_server(args)
    print(f"Open http://127.0.0.1:{server.server_port} — {'SYNTHETIC DEMO' if args.demo else 'LIVE: requests consume credits'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
