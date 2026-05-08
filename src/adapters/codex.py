import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .types import AgentInfo, RateLimits, UsageEntry

CODEX_DIR = os.path.expanduser("~/.codex")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
STATE_DB = os.path.join(CODEX_DIR, "state_5.sqlite")


def detect() -> AgentInfo | None:
    if Path(SESSIONS_DIR).is_dir():
        return AgentInfo(
            id="codex",
            name="Codex",
            data_dir=SESSIONS_DIR,
            installed=True,
        )
    return None


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    entries: list[UsageEntry] = []
    seen: set[str] = set()
    cutoff = None
    if hours_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    models = _load_thread_models()

    sessions_path = Path(SESSIONS_DIR)
    if not sessions_path.is_dir():
        return entries

    for jsonl_path in sessions_path.rglob("*.jsonl"):
        _parse_jsonl(jsonl_path, models, entries, seen, cutoff)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def _load_thread_models() -> dict[str, str]:
    if not os.path.exists(STATE_DB):
        return {}
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        rows = conn.execute("SELECT id, model FROM threads WHERE model IS NOT NULL").fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except (sqlite3.Error, OSError):
        return {}


def load_rate_limits() -> RateLimits | None:
    sessions_path = Path(SESSIONS_DIR)
    if not sessions_path.is_dir():
        return None

    jsonl_files = sorted(sessions_path.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    models = _load_thread_models()

    for path in jsonl_files[:5]:
        rl = _extract_rate_limits(path, models)
        if rl:
            return rl
    return None


def _extract_rate_limits(path: Path, models: dict[str, str]) -> RateLimits | None:
    session_id = ""
    last_rl = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "session_meta":
                    session_id = data.get("payload", {}).get("id", "")
                if data.get("type") != "event_msg":
                    continue
                payload = data.get("payload", {})
                if payload.get("type") != "token_count":
                    continue
                rl = payload.get("rate_limits")
                if rl:
                    last_rl = (rl, data.get("timestamp", ""), session_id)
    except (OSError, PermissionError):
        return None

    if not last_rl:
        return None

    rl, ts, sid = last_rl
    primary = rl.get("primary") or {}
    secondary = rl.get("secondary") or {}

    five_pct = primary.get("used_percent")
    five_reset = primary.get("resets_at")
    seven_pct = secondary.get("used_percent")
    seven_reset = secondary.get("resets_at")

    now_ts = datetime.now(timezone.utc).timestamp()
    if five_reset and five_reset < now_ts:
        five_pct = 0.0
    if seven_reset and seven_reset < now_ts:
        seven_pct = 0.0

    if five_pct is None and seven_pct is None:
        return None

    model_name = models.get(sid, "")

    return RateLimits(
        five_hour_pct=five_pct,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_pct,
        seven_day_resets_at=seven_reset,
        model=model_name,
        updated_at=ts,
    )


def _project_from_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        rel = cwd[len(home):].strip(os.sep)
    else:
        rel = cwd.strip(os.sep)
    parts = rel.split(os.sep)
    return parts[-1] if parts and parts[-1] else rel or "unknown"


def _parse_jsonl(
    path: Path,
    models: dict[str, str],
    entries: list[UsageEntry],
    seen: set[str],
    cutoff: datetime | None,
) -> None:
    session_id = ""
    session_ts = ""
    project = "unknown"
    model = "unknown"
    last_usage = None
    msg_count = 0

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                row_type = data.get("type")

                if row_type == "session_meta":
                    payload = data.get("payload", {})
                    session_id = payload.get("id", "")
                    session_ts = payload.get("timestamp", "")
                    cwd = payload.get("cwd", "")
                    if cwd:
                        project = _project_from_cwd(cwd)
                    model = models.get(session_id, "unknown")
                    continue

                if row_type != "event_msg":
                    continue

                payload = data.get("payload", {})
                if payload.get("type") == "token_count":
                    info = payload.get("info")
                    if info and info.get("total_token_usage"):
                        last_usage = info["total_token_usage"]
                        msg_count += 1
    except (OSError, PermissionError):
        return

    if not last_usage or not session_id:
        return

    cached = last_usage.get("cached_input_tokens", 0)
    input_tokens = last_usage.get("input_tokens", 0) - cached
    output_tokens = last_usage.get("output_tokens", 0) + last_usage.get("reasoning_output_tokens", 0)

    if input_tokens == 0 and output_tokens == 0:
        return

    try:
        ts = datetime.fromisoformat(session_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return

    if cutoff and ts < cutoff:
        return

    if session_id in seen:
        return
    seen.add(session_id)

    entries.append(UsageEntry(
        timestamp=ts,
        session_id=session_id,
        message_id=session_id,
        request_id="",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=cached,
        cost_usd=None,
        project=project,
        agent_id="codex",
        message_count=msg_count,
    ))
