"""tt sidebar 数据层：扫描活跃会话、提取提示词历史、推断会话状态。

数据源与调研结论见本地 sidebar-research/README.md（不入 git）：
- CC transcript jsonl（`~/.claude/projects/`）——提示词唯一来源；格式官方不承诺稳定，
  全程防御式解析：缺字段/类型不对一律跳过不崩。
- Codex rollout jsonl（`~/.codex/sessions/`）——兼容旧版 `event_msg/user_message`
  与新版 `response_item/message`（双写时以 event_msg 为准去重），
  `task_started` / `task_complete` 供状态判定。
- Kimi wire jsonl（`~/.kimi-code/sessions/<wd_*>/<session_*>/agents/main/wire.jsonl`）——
  `turn.prompt`（origin.kind=="user"）是提示词来源；`usage.record` 给模型；
  `tool.call`/`tool.result` 配对判 pending；项目名取自同目录 state.json 的 cwd（兼容旧版 workDir）。
- Pi 会话 jsonl（`~/.pi/agent/sessions/<slug>/<ts>_<uuid>.jsonl`，格式 version 3 内部格式）——
  首行 session 头给 id/cwd；role=user 的 message 是提示词来源；assistant message 的
  toolCall/toolResult 配对判 pending；model_change / assistant message 给模型。宽松降级解析。
- 心跳 `config.STATUS_FILE`（CC statusline 每帧落盘）——`session_id` + `_received_at`
  判「正在跑」，白拿、零新增开销；Codex Stop hook 的终端定位单独落
  `config.TERMINAL_MAP_FILE`，读取时与 CC status 文件里的映射合并。
- CC 会话注册表 `<claude_home>/sessions/<pid>.json`（实测 CC 2.1.205+ 维护，正常退出
  即删文件）——含 sessionId / pid / procStart，据此把「transcript 还在窗口期但进程
  已死」的会话判为已关闭、不进列表；目录不存在（老版本 CC）不过滤并提示升级。
hooks 事件流（PermissionRequest 等授权的精确信号）留 v2 接入；当前状态为启发式推断。
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config
from .adapters import claude as claude_adapter
from .adapters import codex as codex_adapter
from .adapters.util import (
    claude_home,
    iter_jsonl_dicts,
    kimi_home,
    kimi_project_from_session_dir,
    parse_epoch_ms,
    pi_home,
    project_from_cwd,
)

# 会话状态（启发式，见 _infer_state；ATTENTION 无法区分「等授权」和「长工具在跑」，v2 接 hooks 后才能）
RUNNING = "running"      # 正在生成 / 写盘
ATTENTION = "attention"  # 有未完成的工具调用且不再写盘——大概率在等授权
WAITING = "waiting"      # 轮次已结束，等下一条输入
IDLE = "idle"            # 长时间无动静

RUNNING_WINDOW_S = 30       # transcript 多久内有写盘算「正在跑」
HEARTBEAT_FRESH_S = 15      # 心跳多新算「正在跑」
IDLE_AFTER_S = 30 * 60      # 多久无动静降级为 idle

DEFAULT_HOURS_BACK = 5      # 只看窗口期内有动静的会话
DEFAULT_MAX_PROMPTS = 5     # 每会话保留最近 N 条提示词
DEFAULT_MAX_SESSIONS = 10   # 最多显示 N 个会话（按最近活动取前 N，窗口内不足 N 就全显）

# CC 里非「人敲的提示词」的内容前缀（slash command 记录 / 本地命令回显 / 中断标记 /
# 后台任务通知 / harness 注入的 system-reminder）——按文本片段级过滤，见 _claude_prompt_text
_CLAUDE_SKIP_PREFIXES = ("<command-", "<local-command-", "[Request interrupted",
                         "<task-notification", "<system-reminder")
# slash 命令 CC 实测记**两条** user 消息：<command-name> 包裹（上面前缀过滤）+ 裸文本
# （"/compact"、"/cd ~/x"，无任何标记字段）——裸文本按首 token 识别：/命令名（可带
# 插件:命令 冒号形式与参数）。路径类真提示词（/Users/... 有第二个斜杠）不匹配、不误伤
_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_-]*(:[A-Za-z0-9_-]+)?(\s|$)")
# Codex 里包装成 user message 的注入内容（用户指令模板 / 环境上下文 / skill / 图片信封 /
# goal 模式内部上下文 / 自动审批工具注入的 transcript 历史等）
_CODEX_SKIP_PREFIXES = ("# AGENTS.md instructions", "<user_instructions", "<environment_context", "<ide_",
                        "<permissions", "<turn_", "<skill>", "<image", "</image>", "<recommended_plugins",
                        "<codex_internal_context", "The following is the Codex agent history",
                        ">>> TRANSCRIPT")
# Kimi 里非「人敲的提示词」的内容前缀（cron 触发信封 / harness 注入 / 命令记录）
_KIMI_SKIP_PREFIXES = ("<cron-fire", "<system-reminder", "<command-")
# Pi 里非「人敲的提示词」的内容前缀（harness 注入）
_PI_SKIP_PREFIXES = ("<system-reminder",)

_CACHE_MAX = 512  # 解析缓存上限（常驻进程防无限增长，超了整体重建）


@dataclass
class Prompt:
    text: str
    timestamp: datetime | None


@dataclass
class LiveSession:
    agent_id: str
    session_id: str
    project: str
    last_activity: datetime                       # 最后一条有效内容事件时间（UTC aware）
    state: str
    prompts: list[Prompt] = field(default_factory=list)  # 时间正序，最后一条最新
    model: str = ""
    branch: str = ""  # git 分支（CC=transcript 自带 gitBranch；codex=session_meta.git.branch）
    terminal: dict = field(default_factory=dict)  # 终端定位 {"iterm": ..., "tmux": ...}（statusline 采集），空=不可跳转
    next_hint: str = ""  # 「下一步」提示：结构化待回答问题或末段规则提取结果，无则空


@dataclass
class _Parsed:
    """单个 transcript 的解析结果（与「现在几点」无关的部分，可按 mtime+size 缓存）。"""
    session_id: str
    project: str
    prompts: list[Prompt]
    pending_tool: bool  # 末个工具调用尚无结果（CC）/ task 未 complete（Codex）
    model: str = ""
    branch: str = ""
    next_hint: str = ""
    # 最后一条有效事件（真实提示词/工具结果/AI 回复）的时间戳——「最近活动」的权威来源。
    # 不能用文件 mtime：CC 常驻进程会对闲置会话的 transcript 做不改内容的周期性触碰
    # （实测 design-agent 会话 5.5h 没动、mtime 却常新），mtime 只配当缓存键与窗口初筛。
    last_event: datetime | None = None


@dataclass
class _ClaudeParseState:
    session_id: str
    project: str
    prompts: list[Prompt] = field(default_factory=list)
    pending_tool: bool = False
    model: str = ""
    last_reply: str = ""
    pending_question: str = ""
    last_event: datetime | None = None
    branch: str = ""


@dataclass
class _CodexParseState:
    session_id: str = ""
    project: str = "unknown"
    prompts: list[Prompt] = field(default_factory=list)
    # 与 prompts 等长逐条对齐：新版 Codex 双写 event_msg/user_message 与
    # response_item/message，解析完按通道决定去重（见 _parse_codex）。
    prompt_channels: list[str] = field(default_factory=list)
    pending_task: bool = False
    last_reply: str = ""           # event_msg/agent_message
    last_reply_response: str = ""  # response_item assistant（可能是结构化 JSON，仅作回退）
    last_event: datetime | None = None
    branch: str = ""


@dataclass
class _KimiParseState:
    session_id: str
    project: str = "unknown"
    prompts: list[Prompt] = field(default_factory=list)
    pending_calls: set[str] = field(default_factory=set)  # 已 call 未 result 的 toolCallId
    model: str = ""
    last_reply: str = ""
    pending_question: str = ""
    last_event: datetime | None = None


@dataclass
class _PiParseState:
    session_id: str = ""
    project: str = "unknown"
    prompts: list[Prompt] = field(default_factory=list)
    pending_calls: set[str] = field(default_factory=set)  # 已 call 未 result 的 toolCall id
    model: str = ""
    last_reply: str = ""
    last_event: datetime | None = None


# 按 (path, max_prompts) → (mtime_ns, size, result) 缓存：相同条数且文件未变才复用。
# 解析结果已经按 max_prompts 截断（None 表示保留全部），参数必须进 key，避免先查 2 条后
# 再查 5 条或全部时仍只返回 2 条。
_parse_cache: dict[tuple[str, int | None], tuple[int, int, _Parsed]] = {}


def scan_sessions(hours_back: int = DEFAULT_HOURS_BACK,
                  max_prompts: int | None = DEFAULT_MAX_PROMPTS,
                  agent_ids: set[str] | None = None,
                  max_sessions: int = DEFAULT_MAX_SESSIONS) -> list[LiveSession]:
    """窗口期内有动静的会话，按最近活动倒序、最多取前 max_sessions 个。
    agent_ids=None 表示不过滤。"""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours_back)
    heartbeat, term_map = _read_status()
    sessions: list[LiveSession] = []
    if agent_ids is None or "claude-code" in agent_ids:
        sessions.extend(_scan_claude_sessions(cutoff, now, heartbeat, max_prompts, term_map=term_map,
                                              live_sids=_live_claude_sids()))
    if agent_ids is None or "codex" in agent_ids:
        sessions.extend(_scan_codex_sessions(cutoff, now, heartbeat, max_prompts, term_map=term_map))
    if agent_ids is None or "kimi" in agent_ids:
        sessions.extend(_scan_kimi_sessions(cutoff, now, heartbeat, max_prompts, term_map=term_map))
    if agent_ids is None or "pi" in agent_ids:
        sessions.extend(_scan_pi_sessions(cutoff, now, heartbeat, max_prompts, term_map=term_map))
    sessions.sort(key=lambda s: s.last_activity, reverse=True)
    return sessions[:max_sessions]


def _infer_state(now: datetime, last_activity: datetime,
                 pending_tool: bool, heartbeat_fresh: bool) -> str:
    age = (now - last_activity).total_seconds()
    if heartbeat_fresh or age < RUNNING_WINDOW_S:
        return RUNNING
    if pending_tool:
        return ATTENTION
    if age > IDLE_AFTER_S:
        return IDLE
    return WAITING


def _read_terminal_map(path: str) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    term_map = data.get("_terminal_map") if isinstance(data, dict) else None
    if not isinstance(term_map, dict):
        return {}
    return {
        session_id: info
        for session_id, info in term_map.items()
        if isinstance(session_id, str) and isinstance(info, dict)
    }


def _read_status() -> tuple[tuple[str, datetime] | None, dict[str, dict]]:
    """读 CC statusline 落盘文件一次，返回 (心跳, 终端定位 map)。

    心跳 = (session_id, 最近一帧时间)，只反映最近渲染的那一个会话；
    终端定位 = CC statusline ≥2.0 写入 STATUS_FILE 的 `_terminal_map` + Codex Stop hook
    写入 TERMINAL_MAP_FILE 的 `_terminal_map`，按 session_id 合并；旧版脚本没有字段时
    返回空 dict、点击跳转优雅降级。
    """
    try:
        with open(config.STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    term_map = data.get("_terminal_map")
    if not isinstance(term_map, dict):
        term_map = {}
    else:
        term_map = {
            session_id: info
            for session_id, info in term_map.items()
            if isinstance(session_id, str) and isinstance(info, dict)
        }
    term_map.update(_read_terminal_map(config.TERMINAL_MAP_FILE))
    sid = data.get("session_id") or ""
    try:
        ts = datetime.fromisoformat(data.get("_received_at", ""))
    except (TypeError, ValueError):
        return None, term_map
    if not sid:
        return None, term_map
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (sid, ts), term_map


def terminal_info(session_id: str) -> dict:
    """某会话的终端定位（点击跳转用），无记录返回 {}。每次现读文件，保证拿最新。"""
    _, term_map = _read_status()
    info = term_map.get(session_id)
    return info if isinstance(info, dict) else {}


def _live_claude_sids() -> set[str] | None:
    """CC 会话注册表 → 进程仍存活的会话 sessionId 集合；注册表目录不存在（老版本 CC
    没有该特性）返回 None 表示「无法判断，别过滤」。

    正常退出 CC 会删掉自己的注册文件；crash 残留的文件靠 pid 探活 + 启动时间比对
    兜底（pid 可能已被无关进程复用）。启动时间用 `startedAt`（epoch 毫秒，时区无关），
    **不能用 `procStart` 字符串**——它按 CC 写入时的时区渲染，而 `ps lstart` 按本进程
    TZ 渲染（主人 CLI 设 TZ），字符串比对会把全部会话误判死。防御式解析：单个文件
    坏了只跳过该文件。
    """
    reg = Path(claude_home()) / "sessions"
    if not reg.is_dir():
        return None
    want: dict[int, float | None] = {}
    sid_by_pid: dict[int, str] = {}
    for path in reg.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        sid = data.get("sessionId")
        pid = data.get("pid")
        if not isinstance(pid, int):  # 缺 pid 字段时退回文件名（注册文件以 pid 命名）
            pid = int(path.stem) if path.stem.isdigit() else 0
        if not isinstance(sid, str) or not sid or pid <= 0:
            continue
        started_ms = data.get("startedAt")
        want[pid] = started_ms / 1000 if isinstance(started_ms, (int, float)) else None
        sid_by_pid[pid] = sid
    return {sid_by_pid[pid] for pid in _alive_pids(want)}


_START_TOLERANCE_S = 10  # 注册的 startedAt 与进程真实启动时刻的允许偏差（记录晚于启动零点几秒）


def _alive_pids(want: dict[int, float | None]) -> set[int]:
    """want: pid → 注册表记录的进程启动时间（epoch 秒，可 None）。返回确认存活的 pid。

    一次 `ps -o pid=,lstart=` 批量查询（强制 C locale 保英文日期格式）；lstart 按本进程 TZ
    解析回 epoch（ps 子进程继承同一 TZ，mktime 同源可逆），与 startedAt 差超 _START_TOLERANCE_S
    视为 pid 已被复用（判死）。lstart 解析不了只探活不比时间——失败方向
    是「多显示一个已关会话」，绝不误杀活会话。ps 不可用退化为 `os.kill(pid, 0)`。
    """
    if not want:
        return set()
    try:
        # 强制 C locale：lstart 输出格式随用户 locale 变化（如中文系统日期英文不在），
        # _parse_lstart 只认英文格式——不强制则非英文 locale 下启动时间比对静默失效
        out = subprocess.run(
            ["ps", "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in want)],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {pid for pid in want if _pid_exists(pid)}
    alive: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        expected = want.get(pid)
        actual = _parse_lstart(" ".join(parts[1:]))
        if expected is not None and actual is not None and abs(actual - expected) > _START_TOLERANCE_S:
            continue
        alive.add(pid)
    return alive


def _parse_lstart(raw: str) -> float | None:
    """`ps -o lstart` 输出（如 "Thu Jul 9 17:18:34 2026"）→ epoch 秒；解析失败返回 None。"""
    try:
        return time.mktime(time.strptime(raw, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # 含 PermissionError：进程在、只是无权发信号
        return True
    return True


def registry_update_hint(sessions: list[LiveSession]) -> bool:
    """列表里有 CC 会话、但本机 CC 没有会话注册表（版本太老）→ True，渲染层提示升级。"""
    if not any(s.agent_id == "claude-code" for s in sessions):
        return False
    return not (Path(claude_home()) / "sessions").is_dir()


def _heartbeat_fresh(heartbeat: tuple[str, datetime] | None, session_id: str, now: datetime) -> bool:
    return (heartbeat is not None and heartbeat[0] == session_id
            and (now - heartbeat[1]).total_seconds() < HEARTBEAT_FRESH_S)


def _cache_get(path: Path, max_prompts: int | None) -> tuple[float, tuple[int, int] | None, _Parsed | None]:
    """返回 (mtime, 读取前文件指纹, 缓存结果)；文件消失时后两项均为 None。"""
    try:
        st = path.stat()
    except OSError:
        return 0.0, None, None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _parse_cache.get((str(path), max_prompts))
    if hit and hit[:2] == stamp:
        return st.st_mtime, stamp, hit[2]
    return st.st_mtime, stamp, None


def _cache_put(path: Path, max_prompts: int | None, parsed: _Parsed,
               expected_stamp: tuple[int, int]) -> None:
    """文件在解析期间保持不变才缓存，避免 EOF 后追加的新事件被旧结果永久遮住。"""
    if len(_parse_cache) > _CACHE_MAX:
        _parse_cache.clear()
    try:
        st = path.stat()
    except OSError:
        return
    stamp = (st.st_mtime_ns, st.st_size)
    if stamp == expected_stamp:
        _parse_cache[(str(path), max_prompts)] = (*stamp, parsed)


# --- Claude Code ---

def _scan_claude_sessions(cutoff: datetime, now: datetime,
                          heartbeat: tuple[str, datetime] | None,
                          max_prompts: int | None,
                          dirs: list[str] | None = None,
                          term_map: dict[str, dict] | None = None,
                          live_sids: set[str] | None = None) -> list[LiveSession]:
    """dirs 供测试注入；默认复用 claude adapter 的目录解析（CLAUDE_CONFIG_DIR 等）。
    live_sids 是注册表探活结果：非 None 时不在其中的会话视为已关闭、直接不进列表。"""
    term_map = term_map or {}
    sessions: list[LiveSession] = []
    seen: set[str] = set()
    for base_dir in (dirs if dirs is not None else claude_adapter._get_claude_dirs()):
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for path in base.rglob("*.jsonl"):
            mtime, stamp, parsed = _cache_get(path, max_prompts)
            if mtime <= 0:
                continue
            mtime_dt = datetime.fromtimestamp(mtime, UTC)
            if mtime_dt < cutoff:  # 初筛：内容事件时间必然 ≤ mtime，mtime 过老可安全跳过
                continue
            if parsed is None:
                fallback = claude_adapter._extract_project_from_dir(path, base)
                parsed = _parse_claude(path, fallback, max_prompts)
                if parsed is None:
                    continue
                if stamp is not None:
                    _cache_put(path, max_prompts, parsed, stamp)
            if not parsed.prompts or parsed.session_id in seen:
                continue
            if live_sids is not None and parsed.session_id not in live_sids:
                continue  # 注册表可用且不在册：进程已退出，最近关闭的会话不算活跃
            # 权威活动时间 = 内容里最后一条有效事件；CC 会对闲置会话做不改内容的 mtime 触碰
            last_activity = parsed.last_event or mtime_dt
            if last_activity < cutoff:
                continue
            seen.add(parsed.session_id)
            sessions.append(LiveSession(
                agent_id="claude-code",
                session_id=parsed.session_id,
                project=parsed.project,
                last_activity=last_activity,
                state=_infer_state(now, last_activity, parsed.pending_tool,
                                   _heartbeat_fresh(heartbeat, parsed.session_id, now)),
                prompts=parsed.prompts,
                model=parsed.model,
                branch=parsed.branch,
                terminal=term_map.get(parsed.session_id) or {},
                next_hint=parsed.next_hint,
            ))
    return sessions


def _parse_claude(path: Path, fallback_project: str, max_prompts: int | None) -> _Parsed | None:
    state = _ClaudeParseState(session_id=path.stem, project=fallback_project)
    for data in iter_jsonl_dicts(path):
        if data.get("isSidechain"):  # 子代理 sidechain 的消息不是主人敲的提示词
            continue
        state.branch = data.get("gitBranch") or state.branch
        dtype = data.get("type")
        if dtype == "user":
            _consume_claude_user(data, state)
        elif dtype == "assistant":
            _consume_claude_assistant(data, state)
    if not state.prompts:
        return None
    # 「下一步」优先级链：结构化提问（待回答，零猜测）> 句子打分精简 > 末行兜底
    prompts = state.prompts if max_prompts is None else state.prompts[-max_prompts:]
    return _Parsed(state.session_id, state.project, prompts, state.pending_tool, state.model,
                   branch=state.branch, next_hint=state.pending_question or _hint_text(state.last_reply),
                   last_event=state.last_event)


def _consume_claude_user(data: dict, state: _ClaudeParseState) -> None:
    sid = data.get("sessionId")
    if sid:
        state.session_id = sid
    cwd = data.get("cwd")
    if cwd:
        state.project = project_from_cwd(cwd)
    # compact 摘要等注入消息不是主人敲的：不进提示词、不消费待决提问、不计活动时间
    if data.get("isMeta") or data.get("isCompactSummary") or data.get("isVisibleInTranscriptOnly"):
        return
    message = data.get("message")
    if not isinstance(message, dict):
        return
    state.pending_question = ""  # 回答/新提示/打断，提问已被消费
    content = message.get("content")
    if _is_tool_result(content):
        state.pending_tool = False
        state.last_event = _parse_ts(data.get("timestamp")) or state.last_event
        return
    text = _claude_prompt_text(content)
    if text is None:  # 注入通知/命令记录等：不算「主人动过」，不计活动时间
        return
    ts = _parse_ts(data.get("timestamp"))
    state.prompts.append(Prompt(text=text, timestamp=ts))
    state.last_event = ts or state.last_event
    state.pending_tool = False


def _consume_claude_assistant(data: dict, state: _ClaudeParseState) -> None:
    message = data.get("message")
    if not isinstance(message, dict):
        return
    state.last_event = _parse_ts(data.get("timestamp")) or state.last_event
    state.model = message.get("model") or state.model
    content = message.get("content")
    reply = ""
    if isinstance(content, list):
        state.pending_tool = any(isinstance(item, dict) and item.get("type") == "tool_use" for item in content)
        for item in content:
            if (isinstance(item, dict) and item.get("type") == "tool_use"
                    and item.get("name") == "AskUserQuestion"):
                question = _format_question(item.get("input") or {})
                state.pending_question = question or state.pending_question
        parts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        reply = "\n".join(part for part in parts if part)
    elif isinstance(content, str):
        state.pending_tool = False
        reply = content
    if reply.strip():
        state.last_reply = reply


def _is_tool_result(content: object) -> bool:
    return (isinstance(content, list)
            and any(isinstance(i, dict) and i.get("type") == "tool_result" for i in content))


def _claude_prompt_text(content: object) -> str | None:
    """user 行 content → 提示词文本；不是人敲的（命令记录 / 注入通知 / 空内容）返回 None。

    过滤按**文本片段级**做：注入内容（task-notification / system-reminder）可能与
    真提示词同处一条消息的不同 text 块，逐片段判前缀、只丢噪音片段，避免误杀真提示词。
    """
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [i.get("text", "") for i in content
                 if isinstance(i, dict) and i.get("type") == "text"]
    else:
        return None
    kept = [frag for frag in (p.strip() for p in parts)
            if frag and not frag.startswith(_CLAUDE_SKIP_PREFIXES)
            and not _SLASH_COMMAND_RE.match(frag)]
    if not kept:
        return None
    return "\n".join(kept)


# --- Codex ---

def _scan_codex_sessions(cutoff: datetime, now: datetime,
                         heartbeat: tuple[str, datetime] | None,
                         max_prompts: int | None,
                         sessions_dir: str | None = None,
                         term_map: dict[str, dict] | None = None) -> list[LiveSession]:
    term_map = term_map or {}  # Codex Stop hook ≥1.2 采集；未启用/尚未跑过一帧时为空、点击优雅降级
    base = Path(sessions_dir if sessions_dir is not None else codex_adapter.SESSIONS_DIR)
    if not base.is_dir():
        return []
    models = codex_adapter._load_thread_models()
    sessions: list[LiveSession] = []
    seen: set[str] = set()
    for path in base.rglob("*.jsonl"):
        mtime, stamp, parsed = _cache_get(path, max_prompts)
        if mtime <= 0:
            continue
        mtime_dt = datetime.fromtimestamp(mtime, UTC)
        if mtime_dt < cutoff:  # 初筛：内容事件时间必然 ≤ mtime
            continue
        if parsed is None:
            parsed = _parse_codex(path, max_prompts)
            if parsed is None:
                continue
            if stamp is not None:
                _cache_put(path, max_prompts, parsed, stamp)
        if not parsed.prompts or parsed.session_id in seen:
            continue
        last_activity = parsed.last_event or mtime_dt
        if last_activity < cutoff:
            continue
        seen.add(parsed.session_id)
        sessions.append(LiveSession(
            agent_id="codex",
            session_id=parsed.session_id,
            project=parsed.project,
            last_activity=last_activity,
            state=_infer_state(now, last_activity, parsed.pending_tool,
                               _heartbeat_fresh(heartbeat, parsed.session_id, now)),
            prompts=parsed.prompts,
            model=parsed.model or models.get(parsed.session_id, ""),
            branch=parsed.branch,
            terminal=term_map.get(parsed.session_id) or {},
            next_hint=parsed.next_hint,
        ))
    return sessions


def _parse_codex(path: Path, max_prompts: int | None) -> _Parsed | None:
    state = _CodexParseState()
    for data in iter_jsonl_dicts(path):
        payload = data.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = _parse_ts(data.get("timestamp"))
        if ts and (state.last_event is None or ts > state.last_event):
            state.last_event = ts
        dtype = data.get("type")
        if dtype == "session_meta":
            _consume_codex_meta(payload, state)
        elif dtype == "event_msg":
            _consume_codex_event(payload, ts, state)
        elif dtype == "response_item":
            _consume_codex_response_item(payload, ts, state)
    if not state.prompts:
        return None
    # 双写文件以 event_msg 为准，剔除 response_item 的孪生副本；event 通道完全没有
    # 真实提示词（纯新版日志）才保留 response_item 通道，同文真实重复输入不受影响。
    if "event" in state.prompt_channels:
        prompts = [p for p, ch in zip(state.prompts, state.prompt_channels, strict=True) if ch == "event"]
    else:
        prompts = state.prompts
    last_reply = state.last_reply or state.last_reply_response
    prompts = prompts if max_prompts is None else prompts[-max_prompts:]
    return _Parsed(state.session_id or path.stem, state.project, prompts, state.pending_task,
                   branch=state.branch, next_hint=_hint_text(last_reply), last_event=state.last_event)


def _consume_codex_meta(payload: dict, state: _CodexParseState) -> None:
    state.session_id = payload.get("id", "") or state.session_id
    cwd = payload.get("cwd", "")
    if cwd:
        state.project = project_from_cwd(cwd)
    git = payload.get("git")
    if isinstance(git, dict) and git.get("branch"):
        state.branch = git["branch"]


def _consume_codex_event(payload: dict, ts: datetime | None, state: _CodexParseState) -> None:
    event_type = payload.get("type")
    if event_type == "user_message":
        text = (payload.get("message") or "").strip()
        if text and not text.startswith(_CODEX_SKIP_PREFIXES):
            state.prompts.append(Prompt(text=text, timestamp=ts))
            state.prompt_channels.append("event")
    elif event_type == "agent_message":
        reply = payload.get("message") or ""
        if reply.strip():
            state.last_reply = reply
    elif event_type == "task_started":
        state.pending_task = True
    elif event_type in ("task_complete", "turn_aborted"):
        state.pending_task = False


def _codex_message_text(content: object, content_type: str) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [item.get("text", "").strip() for item in content
             if isinstance(item, dict) and item.get("type") == content_type]
    return "\n".join(part for part in parts if part)


def _consume_codex_response_item(payload: dict, ts: datetime | None, state: _CodexParseState) -> None:
    if payload.get("type") != "message":
        return
    role = payload.get("role")
    if role == "user":
        content = payload.get("content")
        if isinstance(content, list):
            parts = [item.get("text", "").strip() for item in content
                     if isinstance(item, dict) and item.get("type") == "input_text"]
            text = "\n".join(part for part in parts
                             if part and not part.startswith(_CODEX_SKIP_PREFIXES))
        else:
            text = _codex_message_text(content, "input_text")
            if text.startswith(_CODEX_SKIP_PREFIXES):
                text = ""
        if text:
            state.prompts.append(Prompt(text=text, timestamp=ts))
            state.prompt_channels.append("response")
    elif role == "assistant":
        reply = _codex_message_text(payload.get("content"), "output_text")
        if reply:
            state.last_reply_response = reply


# --- Kimi Code ---

def _scan_kimi_sessions(cutoff: datetime, now: datetime,
                        heartbeat: tuple[str, datetime] | None,
                        max_prompts: int | None,
                        sessions_dir: str | None = None,
                        term_map: dict[str, dict] | None = None) -> list[LiveSession]:
    """扫描 `<kimi_home>/sessions/<wd_*>/<session_*>/agents/main/wire.jsonl`。
    sessions_dir 供测试注入。Kimi 没有会话注册表，活跃度靠事件时间窗口判断（同 Codex）。"""
    term_map = term_map or {}
    base = Path(sessions_dir) if sessions_dir is not None else Path(kimi_home()) / "sessions"
    if not base.is_dir():
        return []
    sessions: list[LiveSession] = []
    seen: set[str] = set()
    for path in base.glob("*/session_*/agents/main/wire.jsonl"):
        mtime, stamp, parsed = _cache_get(path, max_prompts)
        if mtime <= 0:
            continue
        mtime_dt = datetime.fromtimestamp(mtime, UTC)
        if mtime_dt < cutoff:  # 初筛：内容事件时间必然 ≤ mtime
            continue
        if parsed is None:
            parsed = _parse_kimi(path, max_prompts)
            if parsed is None:
                continue
            if stamp is not None:
                _cache_put(path, max_prompts, parsed, stamp)
        if not parsed.prompts or parsed.session_id in seen:
            continue
        last_activity = parsed.last_event or mtime_dt
        if last_activity < cutoff:
            continue
        seen.add(parsed.session_id)
        sessions.append(LiveSession(
            agent_id="kimi",
            session_id=parsed.session_id,
            project=parsed.project,
            last_activity=last_activity,
            state=_infer_state(now, last_activity, parsed.pending_tool,
                               _heartbeat_fresh(heartbeat, parsed.session_id, now)),
            prompts=parsed.prompts,
            model=parsed.model,
            terminal=term_map.get(parsed.session_id) or {},
            next_hint=parsed.next_hint,
        ))
    return sessions


def _parse_kimi(path: Path, max_prompts: int | None) -> _Parsed | None:
    # 布局：…/sessions/<wd_*>/<session_*>/agents/main/wire.jsonl；state.json 在会话目录下
    session_dir = path.parents[2]
    state = _KimiParseState(session_id=session_dir.name, project=kimi_project_from_session_dir(session_dir))
    for data in iter_jsonl_dicts(path):
        ts = parse_epoch_ms(data.get("time"))
        if ts and (state.last_event is None or ts > state.last_event):
            state.last_event = ts
        dtype = data.get("type")
        if dtype == "turn.prompt":
            _consume_kimi_prompt(data, ts, state)
        elif dtype == "usage.record":
            model = data.get("model")
            if isinstance(model, str) and model:
                state.model = model
        elif dtype == "context.append_loop_event":
            event = data.get("event")
            if isinstance(event, dict):
                _consume_kimi_loop_event(event, state)
    if not state.prompts:
        return None
    prompts = state.prompts if max_prompts is None else state.prompts[-max_prompts:]
    return _Parsed(state.session_id, state.project, prompts, bool(state.pending_calls), state.model,
                   next_hint=state.pending_question or _hint_text(state.last_reply),
                   last_event=state.last_event)


def _consume_kimi_prompt(data: dict, ts: datetime | None, state: _KimiParseState) -> None:
    origin = data.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "user":
        return  # goal/cron 等非人敲来源不算提示词
    parts = [p.get("text", "") for p in (data.get("input") or [])
             if isinstance(p, dict) and p.get("type") == "text"]
    kept = [frag for frag in (p.strip() for p in parts)
            if frag and not frag.startswith(_KIMI_SKIP_PREFIXES) and not _SLASH_COMMAND_RE.match(frag)]
    if not kept:
        return
    state.pending_question = ""  # 回答/新提示，提问已被消费
    state.prompts.append(Prompt(text="\n".join(kept), timestamp=ts))


def _consume_kimi_loop_event(event: dict, state: _KimiParseState) -> None:
    event_type = event.get("type")
    if event_type == "content.part":
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text") or ""
            if text.strip():
                state.last_reply = text
    elif event_type == "tool.call":
        call_id = event.get("toolCallId")
        if isinstance(call_id, str) and call_id:
            state.pending_calls.add(call_id)
        if event.get("name") == "AskUserQuestion":
            question = _format_question(event.get("args") or {})
            state.pending_question = question or state.pending_question
    elif event_type == "tool.result":
        call_id = event.get("toolCallId")
        if isinstance(call_id, str):
            state.pending_calls.discard(call_id)


# --- Pi（~/.pi/agent/sessions/<slug>/<ts>_<uuid>.jsonl） ---

def _scan_pi_sessions(cutoff: datetime, now: datetime,
                      heartbeat: tuple[str, datetime] | None,
                      max_prompts: int | None,
                      sessions_dir: str | None = None,
                      term_map: dict[str, dict] | None = None) -> list[LiveSession]:
    """扫描 `<pi_home>/sessions/<slug>/*.jsonl`。
    sessions_dir 供测试注入。Pi 没有会话注册表，活跃度靠事件时间窗口判断（同 Codex/Kimi）。"""
    term_map = term_map or {}
    base = Path(sessions_dir) if sessions_dir is not None else Path(pi_home()) / "sessions"
    if not base.is_dir():
        return []
    sessions: list[LiveSession] = []
    seen: set[str] = set()
    for path in base.glob("*/*.jsonl"):
        mtime, stamp, parsed = _cache_get(path, max_prompts)
        if mtime <= 0:
            continue
        mtime_dt = datetime.fromtimestamp(mtime, UTC)
        if mtime_dt < cutoff:  # 初筛：内容事件时间必然 ≤ mtime
            continue
        if parsed is None:
            parsed = _parse_pi(path, max_prompts)
            if parsed is None:
                continue
            if stamp is not None:
                _cache_put(path, max_prompts, parsed, stamp)
        if not parsed.prompts or parsed.session_id in seen:
            continue
        last_activity = parsed.last_event or mtime_dt
        if last_activity < cutoff:
            continue
        seen.add(parsed.session_id)
        sessions.append(LiveSession(
            agent_id="pi",
            session_id=parsed.session_id,
            project=parsed.project,
            last_activity=last_activity,
            state=_infer_state(now, last_activity, parsed.pending_tool,
                               _heartbeat_fresh(heartbeat, parsed.session_id, now)),
            prompts=parsed.prompts,
            model=parsed.model,
            terminal=term_map.get(parsed.session_id) or {},
            next_hint=parsed.next_hint,
        ))
    return sessions


def _parse_pi(path: Path, max_prompts: int | None) -> _Parsed | None:
    # 布局：…/sessions/<slug>/<ts>_<uuid>.jsonl；首行 session 头给 id/cwd
    state = _PiParseState()
    for data in iter_jsonl_dicts(path):
        ts = _parse_ts(data.get("timestamp"))
        if ts and (state.last_event is None or ts > state.last_event):
            state.last_event = ts
        dtype = data.get("type")
        if dtype == "session":
            session_id = data.get("id")
            if isinstance(session_id, str) and session_id:
                state.session_id = session_id
            cwd = data.get("cwd")
            if isinstance(cwd, str) and cwd:
                state.project = project_from_cwd(cwd)
        elif dtype == "model_change":
            provider, model_id = data.get("provider"), data.get("modelId")
            if isinstance(provider, str) and provider and isinstance(model_id, str) and model_id:
                state.model = f"{provider}/{model_id}"
        elif dtype == "message":
            _consume_pi_message(data, ts, state)
    if not state.prompts:
        return None
    if not state.session_id:
        state.session_id = path.stem
    prompts = state.prompts if max_prompts is None else state.prompts[-max_prompts:]
    return _Parsed(state.session_id, state.project, prompts, bool(state.pending_calls), state.model,
                   next_hint=_hint_text(state.last_reply),
                   last_event=state.last_event)


def _consume_pi_message(data: dict, ts: datetime | None, state: _PiParseState) -> None:
    message = data.get("message")
    if not isinstance(message, dict):
        return
    role = message.get("role")
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    if role == "user":
        parts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        kept = [frag for frag in (p.strip() for p in parts)
                if frag and not frag.startswith(_PI_SKIP_PREFIXES) and not _SLASH_COMMAND_RE.match(frag)]
        if kept:
            state.prompts.append(Prompt(text="\n".join(kept), timestamp=ts))
    elif role == "assistant":
        provider, model_id = message.get("provider"), message.get("model")
        if isinstance(provider, str) and provider and isinstance(model_id, str) and model_id:
            state.model = f"{provider}/{model_id}"
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                text = b.get("text") or ""
                if text.strip():
                    state.last_reply = text
            elif b.get("type") == "toolCall":
                call_id = b.get("id")
                if isinstance(call_id, str) and call_id:
                    state.pending_calls.add(call_id)
    elif role == "toolResult":
        call_id = message.get("toolCallId")
        if isinstance(call_id, str):
            state.pending_calls.discard(call_id)


_HINT_MAX_LINES = 5   # 「下一步」显示上限（AskUserQuestion 格式化路径用；打分路径上限 3）
_HINT_PICK_LINES = 3  # 句子打分路径最多保留 N 个正分句

# 句子打分词表（确定性规则，随用随补）：命中行动/征询词加分，纯完成陈述减分
_ACTION_WORDS = ("要我", "需要我", "是否", "说一声", "确认", "建议", "重启", "验证", "跑一下",
                 "你来定", "等你", "接下来", "下一步", "要不要", "可以选", "告诉我", "试试",
                 "生效", "开工", "动手", "选一个", "定一个", "待你", "看看")
_DONE_WORDS = ("已提交", "已完成", "完成了", "全绿", "修好", "已合并", "已更新", "已修复", "通过", "落地")
_OPTION_RE = re.compile(r"^(\d+[.、)]|[-•·]|[A-Da-d][.、)])\s*")
_SENT_SPLIT_RE = re.compile(r"[^。！？；!?;]+[。！？；!?;]?")


def _clean_reply_lines(reply: str, line_limit: int = 160) -> list[str]:
    """回复正文降噪：剔代码块与围栏、分隔线、表格行、空行，剥标题井号与粗体星号，每行限长。"""
    kept: list[str] = []
    in_code = False
    for raw in reply.splitlines():
        ln = raw.strip()
        if ln.startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code or not ln:
            continue
        if ln.startswith("|") or re.fullmatch(r"[-*_=~]{3,}", ln):
            continue
        ln = re.sub(r"^#{1,6}\s+", "", ln).replace("**", "")
        kept.append(ln[:line_limit])
    return kept


def _score(sent: str) -> int:
    """「下一步」信号分：问句 +3、行动/征询词 +2、纯完成陈述 -2。纯规则，无模型。"""
    s = 0
    body = sent.rstrip("。；;.")
    if body.endswith(("？", "?", "吗", "么", "呢")):
        s += 3
    if any(w in sent for w in _ACTION_WORDS):
        s += 2
    if any(w in sent for w in _DONE_WORDS) and "？" not in sent and "?" not in sent:
        s -= 2
    return s


def _strip_code_blocks(reply: str) -> str:
    """剔除围栏代码块（含围栏行）、保留空行结构供分段——代码块内的空行会骗过
    段落切分（把半截代码当「最后一段」），必须先剔再分段。"""
    out: list[str] = []
    in_code = False
    for ln in reply.splitlines():
        if ln.strip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if not in_code:
            out.append(ln)
    return "\n".join(out)


def _hint_text(reply: str) -> str:
    """句子打分精简，只看回复的**最后一个有效段落**（空行分段，主人定）：收尾段才是
    「下一步」所在，整量分析会把中段大纲/列表也抓进来。结尾段清洗后为空（纯表格等）
    向上回退。段内切句取正分句（问句/建议/选项行）按原顺序保留末尾至多
    _HINT_PICK_LINES 个；全无信号（纯汇报）回退最后一个有效行。
    结构化输出（自动审批工具最终回复就是 raw JSON）不是给人看的「下一步」，直接空。"""
    if reply.strip().startswith(("{", "[")):
        return ""
    cleaned: list[str] = []
    for para in reversed(re.split(r"\n\s*\n", _strip_code_blocks(reply))):
        cleaned = _clean_reply_lines(para)
        if cleaned:
            break
    if not cleaned:
        return ""
    picked: list[str] = []
    for ln in cleaned:
        if _OPTION_RE.match(ln):  # 选项/列表行保持原样不切句；完成陈述类列表被减分排除
            if _score(ln) >= 0:
                picked.append(ln)
            continue
        for match in _SENT_SPLIT_RE.findall(ln):
            sent = match.strip()
            if sent and _score(sent) > 0:
                picked.append(sent)
    if picked:
        return "\n".join(picked[-_HINT_PICK_LINES:])
    return cleaned[-1]


def _format_question(tool_input: dict) -> str:
    """AskUserQuestion 的结构化提问 → 「下一步」文本：问题一行 + 选项一行（· A / B / C）。"""
    lines: list[str] = []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return ""
    for q in questions[:2]:
        if not isinstance(q, dict):
            continue
        text = (q.get("question") or "").strip()
        if text:
            lines.append(text[:160])
        opts = " / ".join((o.get("label") or "").strip()
                          for o in (q.get("options") or []) if isinstance(o, dict))
        if opts:
            lines.append(("· " + opts)[:160])
    return "\n".join(lines[:_HINT_MAX_LINES])


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
