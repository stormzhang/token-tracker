import json
import tempfile
import unittest
from pathlib import Path

from src.adapters import codex


def _write_session(path: Path, session_id: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session_meta", "payload": {"id": session_id}}) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _token_count(timestamp: str, limit_id: str, five: float, weekly: float) -> dict:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": limit_id,
                "primary": {"used_percent": five, "resets_at": 1780312510},
                "secondary": {"used_percent": weekly, "resets_at": 1780846058},
            },
        },
    }


class CodexRateLimitsTest(unittest.TestCase):
    def test_load_rate_limits_uses_json_timestamp_instead_of_file_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_file = root / "old" / "session.jsonl"
            new_file = root / "new" / "session.jsonl"
            _write_session(old_file, "old-session", [_token_count("2026-06-01T07:00:00Z", "codex", 5.0, 8.0)])
            _write_session(new_file, "new-session", [_token_count("2026-06-01T09:00:00Z", "codex", 25.0, 13.0)])

            original_sessions_dir = codex.SESSIONS_DIR
            original_state_db = codex.STATE_DB
            try:
                codex.SESSIONS_DIR = str(root)
                codex.STATE_DB = str(root / "missing.sqlite")
                rate_limits = codex.load_rate_limits()
            finally:
                codex.SESSIONS_DIR = original_sessions_dir
                codex.STATE_DB = original_state_db

        self.assertIsNotNone(rate_limits)
        self.assertEqual(rate_limits.five_hour_pct, 25.0)
        self.assertEqual(rate_limits.seven_day_pct, 13.0)
        self.assertEqual(rate_limits.updated_at, "2026-06-01T09:00:00Z")

    def test_extract_rate_limits_ignores_non_codex_limit_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            _write_session(path, "session", [_token_count("2026-06-01T09:25:06Z", "codex_bengalfox", 0.0, 0.0)])

            rate_limits = codex._extract_rate_limits(path, {})

        self.assertIsNone(rate_limits)


if __name__ == "__main__":
    unittest.main()
