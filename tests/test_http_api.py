import json
import unittest
from dataclasses import dataclass


@dataclass
class FakeRateLimits:
    five_hour_pct: float | None = 12.5
    five_hour_resets_at: int | None = 1780312510
    seven_day_pct: float | None = 34.0
    seven_day_resets_at: int | None = 1780846058
    model: str = "gpt-5.5"
    updated_at: str = "2026-06-01T06:20:32.061Z"


class HttpApiTest(unittest.TestCase):
    def test_codex_rate_limit_payload_has_expected_percentages(self):
        from src.http_api import build_codex_rate_limit_payload

        payload = build_codex_rate_limit_payload(lambda: FakeRateLimits())

        self.assertEqual(payload["agent"], "codex")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["five_hour"]["used_percent"], 12.5)
        self.assertEqual(payload["weekly"]["used_percent"], 34.0)
        self.assertEqual(payload["5h"]["used_percent"], 12.5)
        self.assertEqual(payload["wk"]["used_percent"], 34.0)
        self.assertEqual(payload["5h"]["reset_at"], 1780312510)
        self.assertIn("reset_at_iso", payload["wk"])
        self.assertEqual(payload["model"], "gpt-5.5")

    def test_handler_returns_json_for_codex_usage_alias(self):
        from src.http_api import encode_json_response

        status, headers, body = encode_json_response({"ok": True})

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True})


if __name__ == "__main__":
    unittest.main()
