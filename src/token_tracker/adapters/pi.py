"""Pi 数据源：sessions/<slug>/<ts>_<uuid>.jsonl 的 assistant message → UsageEntry。

布局：`<pi_home>/sessions/<项目目录slug>/<UTC时间戳>_<uuid>.jsonl`（格式 version 3，属内部
格式，宽松降级：字段缺失/类型不符就跳过该条）。首行 `{"type":"session","cwd":...}` 给出
项目归属；只有 role=assistant 且带 usage 的 message 产生 entry（一条消息一条 entry）。
usage 字段（pi-ai parseChunkUsage 实测）：input 已扣除 cacheRead/cacheWrite；
output=completion_tokens，**reasoning 已含在 output 内**（OpenAI 惯例），不另计；
totalTokens=input+output+cacheRead+cacheWrite；cost.total 是 pi 按自己 models.json 定价
算好的美元成本（无定价的 provider 全 0）→ 直接作 cost_usd，不再走定价表。
"""
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .types import AgentInfo, UsageEntry
from .util import (
    file_may_have_events_since,
    iter_jsonl_dicts,
    pi_home,
    project_from_cwd,
)

PI_DIR = pi_home()
SESSIONS_DIR = os.path.join(PI_DIR, "sessions")


def detect() -> AgentInfo | None:
    # 以 sessions 目录判断（与 sidebar 纳入口径一致；没跑过会话的裸安装不产生数据）
    if Path(SESSIONS_DIR).is_dir():
        return AgentInfo(id="pi", name="Pi")
    return None


def current_session_id_for_cwd(fresh_within_s: float | None = None) -> str:
    """Pi 的 bash 工具会给子进程注入 PI_SESSION_ID（会话内直接命中）；没有则回退目录探测：
    取首行 session.cwd 等于当前目录、mtime 最新的那个会话文件（jsonl 只追加写，mtime 即
    「最近活动时间」，相当于 Kimi state.json 的 updatedAt）。

    fresh_within_s：只认 mtime 在该秒数内的会话（None 不限），语义同 Kimi 版。
    """
    env_id = os.environ.get("PI_SESSION_ID", "").strip()
    if env_id:
        return env_id
    cwd = os.getcwd()
    now = datetime.now(UTC)
    best_id, best_ts = "", None
    for path in Path(SESSIONS_DIR).glob("*/*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            continue
        if fresh_within_s is not None and (now - mtime).total_seconds() > fresh_within_s:
            continue
        session_id, session_cwd = _session_meta(path)
        if session_cwd != cwd:
            continue
        if best_ts is None or mtime > best_ts:
            best_id, best_ts = session_id, mtime
    return best_id


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    entries: list[UsageEntry] = []
    seen: set[str] = set()
    cutoff = None
    if hours_back > 0:
        cutoff = datetime.now(UTC) - timedelta(hours=hours_back)

    sessions_path = Path(SESSIONS_DIR)
    if not sessions_path.is_dir():
        return entries

    for jsonl_path in sessions_path.glob("*/*.jsonl"):
        if not file_may_have_events_since(jsonl_path, cutoff):
            continue
        _parse_session(jsonl_path, entries, seen, cutoff)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def _session_meta(path: Path) -> tuple[str, str]:
    """读首行 session 头，返回 (session_id, cwd)；读不到/字段缺失回退 (文件名 stem, "")。"""
    for data in iter_jsonl_dicts(path):
        if data.get("type") != "session":
            continue
        session_id = data.get("id")
        cwd = data.get("cwd")
        return (
            session_id if isinstance(session_id, str) and session_id else path.stem,
            cwd if isinstance(cwd, str) else "",
        )
    return path.stem, ""


def _parse_session(path: Path, entries: list[UsageEntry], seen: set[str], cutoff: datetime | None) -> None:
    # 布局：…/sessions/<slug>/<ts>_<uuid>.jsonl，首行 session 头给 id/cwd
    session_id, cwd = _session_meta(path)
    project = project_from_cwd(cwd) if cwd else "unknown"
    for data in iter_jsonl_dicts(path):
        if data.get("type") != "message":
            continue
        ts = _parse_iso(data.get("timestamp"))
        if ts is None or (cutoff is not None and ts < cutoff):
            continue
        message = data.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        entry = _message_to_entry(data, message, usage, ts, session_id, project)
        if entry is None or entry.dedup_key in seen:
            continue
        seen.add(entry.dedup_key)
        entries.append(entry)


def _message_to_entry(
    data: dict, message: dict, usage: dict, ts: datetime, session_id: str, project: str
) -> UsageEntry | None:
    input_tokens = _int_or_zero(usage.get("input"))
    output_tokens = _int_or_zero(usage.get("output"))
    cache_read_tokens = _int_or_zero(usage.get("cacheRead"))
    cache_creation_tokens = _int_or_zero(usage.get("cacheWrite"))
    if not (input_tokens or output_tokens or cache_read_tokens or cache_creation_tokens):
        return None
    # model 存 provider/modelId（如 deepseek-com/deepseek-v4-flash）；缺 provider 退化裸 modelId
    model_id = message.get("model")
    provider = message.get("provider")
    if isinstance(provider, str) and provider and isinstance(model_id, str) and model_id:
        model = f"{provider}/{model_id}"
    elif isinstance(model_id, str) and model_id:
        model = model_id
    else:
        model = "unknown"
    # cost.total 是 pi 按 models.json 定价算好的美元成本（无定价 provider 全 0，语义=真的 0）；
    # cost 字段整体缺失才给 None，回退 tt 定价表
    cost = usage.get("cost")
    cost_total = cost.get("total") if isinstance(cost, dict) else None
    cost_usd = float(cost_total) if isinstance(cost_total, (int, float)) else None
    message_id = data.get("id")
    if not isinstance(message_id, str) or not message_id:
        # 无消息 id：会话内时间戳兜底（同 Kimi 用毫秒时间戳的思路），供 dedup_key 去重
        message_id = ts.isoformat()
    return UsageEntry(
        timestamp=ts,
        session_id=session_id,
        # 消息 id 仅会话内唯一（8 位 hex），跨会话可能撞，前缀 session_id 保 dedup_key 全局唯一
        message_id=f"{session_id}:{message_id}",
        request_id="",  # pi 会话格式没有 request id 概念
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        cost_usd=cost_usd,
        project=project,
        agent_id="pi",
    )


def _parse_iso(raw: object) -> datetime | None:
    """Pi 事件时间是 ISO 8601 字符串（`2026-08-27T03:04:55.779Z`）。"""
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0
