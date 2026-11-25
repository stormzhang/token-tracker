"""Read Antigravity CLI model responses and statusline quota snapshots."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .types import AgentInfo, UsageEntry
from .util import file_may_have_events_since, iter_jsonl_dicts, project_from_cwd

AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"


def detect() -> AgentInfo | None:
    return AgentInfo(id="agy", name="Antigravity CLI") if AGY_HOME.is_dir() else None


def transcripts() -> Iterator[Path]:
    return (AGY_HOME / "brain").glob("*/.system_generated/logs/transcript.jsonl")


def load_projects() -> dict[str, str]:
    db = AGY_HOME / "conversation_summaries.db"
    try:
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro&immutable=1", uri=True)) as connection:
            rows = connection.execute("SELECT conversation_id, workspace_uris FROM conversation_summaries").fetchall()
    except sqlite3.Error:
        return {}
    projects = {}
    for session_id, workspaces in rows:
        try:
            uris = json.loads(workspaces)
            if not isinstance(uris, list) or not uris or not isinstance(uris[0], str):
                continue
            uri = urlsplit(uris[0])
            if uri.scheme == "file" and uri.netloc in ("", "localhost") and uri.path:
                projects[session_id] = project_from_cwd(url2pathname(uri.path))
        except (TypeError, ValueError, OSError):
            continue
    return projects


def parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else None


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours_back) if hours_back > 0 else None
    return _load_entries(cutoff, cutoff)


def load_recent_entries(cutoff: datetime) -> list[UsageEntry]:
    return _load_entries(cutoff, None)


def _load_entries(file_cutoff: datetime | None, entry_cutoff: datetime | None) -> list[UsageEntry]:
    entries: list[UsageEntry] = []
    projects = load_projects()
    for path in transcripts():
        if not file_may_have_events_since(path, file_cutoff):
            continue
        session_id = path.parents[2].name
        seen: set[int] = set()
        for row in iter_jsonl_dicts(path):
            if row.get("type") != "PLANNER_RESPONSE":
                continue
            step = row.get("step_index")
            timestamp = parse_timestamp(row.get("created_at"))
            if type(step) is not int or step in seen or timestamp is None:
                continue
            if entry_cutoff is not None and timestamp < entry_cutoff:
                continue
            input_tokens = _token_count(row.get("input_tokens"))
            output_tokens = _token_count(row.get("output_tokens"))
            cache_read = _token_count(row.get("cache_read_tokens"))
            if not (input_tokens or output_tokens or cache_read):
                continue
            model = row.get("model")
            seen.add(step)
            entries.append(UsageEntry(
                timestamp=timestamp, session_id=session_id, message_id=f"{session_id}:{step}",
                request_id="", model=model if isinstance(model, str) and model else "unknown",
                input_tokens=input_tokens, output_tokens=output_tokens,
                cache_creation_tokens=0, cache_read_tokens=cache_read, cost_usd=None,
                project=projects.get(session_id, "unknown"), agent_id="agy",
            ))
    return sorted(entries, key=lambda entry: entry.timestamp)


def _token_count(value: object) -> int:
    return value if type(value) is int and value > 0 else 0
