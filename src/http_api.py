import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping

from .adapters import codex
from .adapters.types import RateLimits


JsonValue = str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]


def _iso_from_epoch(epoch_seconds: int | None) -> str | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def _limit_payload(used_percent: float | None, resets_at: int | None) -> dict[str, JsonValue]:
    return {
        "used_percent": used_percent,
        "reset_at": resets_at,
        "reset_at_iso": _iso_from_epoch(resets_at),
    }


def build_codex_rate_limit_payload(
    load_rate_limits: Callable[[], RateLimits | None] = codex.load_rate_limits,
) -> dict[str, JsonValue]:
    rate_limits = load_rate_limits()
    if rate_limits is None:
        return {
            "agent": "codex",
            "available": False,
            "5h": _limit_payload(None, None),
            "wk": _limit_payload(None, None),
            "five_hour": _limit_payload(None, None),
            "weekly": _limit_payload(None, None),
            "model": "",
            "updated_at": "",
            "source_dir": os.path.expanduser("~/.codex"),
        }

    five_hour = _limit_payload(rate_limits.five_hour_pct, rate_limits.five_hour_resets_at)
    weekly = _limit_payload(rate_limits.seven_day_pct, rate_limits.seven_day_resets_at)
    return {
        "agent": "codex",
        "available": True,
        "5h": five_hour,
        "wk": weekly,
        "five_hour": five_hour,
        "weekly": weekly,
        "model": rate_limits.model,
        "updated_at": rate_limits.updated_at,
        "source_dir": os.path.expanduser("~/.codex"),
    }


def encode_json_response(payload: Mapping[str, JsonValue], status: int = 200) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return status, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
    }, body


class TokenTrackerHandler(BaseHTTPRequestHandler):
    server_version = "token-tracker-http/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"ok": True})
            return
        if self.path in ("/api/codex/rate-limits", "/api/codex/usage"):
            self._send_json(build_codex_rate_limit_payload())
            return
        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, fmt: str, *args: object) -> None:
        if os.environ.get("TT_HTTP_ACCESS_LOG") == "1":
            super().log_message(fmt, *args)

    def _send_json(self, payload: Mapping[str, JsonValue], status: int = 200) -> None:
        status, headers, body = encode_json_response(payload, status)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = ThreadingHTTPServer((host, port), TokenTrackerHandler)
    print(f"token-tracker HTTP API listening on http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve token-tracker Codex usage over HTTP")
    parser.add_argument("--host", default=os.environ.get("TT_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TT_HTTP_PORT", "8080")))
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
