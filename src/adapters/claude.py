import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .types import AgentInfo, UsageEntry

CLAUDE_DIRS = [
    os.path.expanduser("~/.claude/projects"),
    os.path.expanduser("~/.config/claude/projects"),
]


def detect() -> AgentInfo | None:
    for d in _get_claude_dirs():
        if Path(d).is_dir():
            return AgentInfo(
                id="claude-code",
                name="Claude Code",
                data_dir=d,
                installed=True,
            )
    return None


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    entries: list[UsageEntry] = []
    seen: set[str] = set()
    cutoff = None
    if hours_back > 0:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    for base_dir in _get_claude_dirs():
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for jsonl_path in base.rglob("*.jsonl"):
            fallback_project = _extract_project_from_dir(jsonl_path, base)
            _parse_jsonl(jsonl_path, fallback_project, entries, seen, cutoff)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def _get_claude_dirs() -> list[str]:
    dirs = list(CLAUDE_DIRS)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for p in env.split(","):
            projects_dir = os.path.join(p.strip(), "projects")
            if projects_dir not in dirs:
                dirs.insert(0, projects_dir)
    return dirs


def _project_from_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        rel = cwd[len(home):].strip(os.sep)
    else:
        rel = cwd.strip(os.sep)
    parts = rel.split(os.sep)
    return parts[-1] if parts and parts[-1] else rel or "unknown"


def _extract_project_from_dir(jsonl_path: Path, base: Path) -> str:
    rel = jsonl_path.relative_to(base)
    project_dir = str(rel.parts[0]) if rel.parts else "unknown"
    decoded = project_dir.replace("-", os.sep).strip(os.sep)
    home = os.path.expanduser("~").strip(os.sep)
    if decoded.startswith(home):
        decoded = decoded[len(home):].strip(os.sep)
    parts = decoded.split(os.sep)
    return parts[-1] if parts else "unknown"


def _parse_jsonl(
    path: Path,
    project: str,
    entries: list[UsageEntry],
    seen: set[str],
    cutoff: datetime | None,
) -> None:
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

                if data.get("type") != "assistant":
                    continue

                entry = _parse_assistant_entry(data, project)
                if entry is None:
                    continue

                if cutoff and entry.timestamp < cutoff:
                    continue

                if entry.dedup_key in seen:
                    continue
                seen.add(entry.dedup_key)

                entries.append(entry)
    except (OSError, PermissionError):
        pass


def _parse_assistant_entry(data: dict, project: str) -> UsageEntry | None:
    message = data.get("message")
    if not message or not isinstance(message, dict):
        return None

    usage = message.get("usage")
    if not usage or not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0

    if input_tokens == 0 and output_tokens == 0 and cache_creation == 0 and cache_read == 0:
        return None

    timestamp_str = data.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    message_id = message.get("id", "")
    request_id = data.get("requestId") or ""
    model = message.get("model", "unknown")
    session_id = data.get("sessionId", "")
    cost_usd = data.get("costUSD")

    cwd = data.get("cwd", "")
    if cwd:
        project = _project_from_cwd(cwd)

    return UsageEntry(
        timestamp=ts,
        session_id=session_id,
        message_id=message_id,
        request_id=request_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        cost_usd=cost_usd,
        project=project,
        agent_id="claude-code",
    )
