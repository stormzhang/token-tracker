import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from rich.console import Console

from token_tracker import sidebar
from token_tracker.i18n import t
from token_tracker.sidebar import (
    ATTENTION,
    IDLE,
    RUNNING,
    WAITING,
    LiveSession,
    Prompt,
    _infer_state,
    _parse_claude,
    _parse_codex,
    _scan_claude_sessions,
)
from token_tracker.ui.sidebar import render_sidebar, render_split_sidebar


@pytest.fixture(autouse=True)
def _clear_parse_cache(tmp_path, monkeypatch):
    # 模块级解析缓存按 (mtime, size) 命中，tmp_path 各测试独立但防跨测试串味
    sidebar._parse_cache.clear()
    # Codex 终端映射是用户级缓存；测试默认指向临时空文件，不能读到主人真实会话。
    monkeypatch.setattr(sidebar.config, "TERMINAL_MAP_FILE", str(tmp_path / "tt-terminal-map.json"))


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _iso(seconds_ago: float = 60) -> str:
    """相对当前时间的 ISO 时间戳——last_activity 以内容事件时间为准，fixture 不能用死日期。"""
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _u(content, ts: str | None = None, **extra) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content},
            "timestamp": ts or _iso(), **extra}


def _a(content, model: str = "claude-fable-5", ts: str | None = None) -> dict:
    return {"type": "assistant", "timestamp": ts or _iso(55),
            "message": {"role": "assistant", "model": model, "content": content}}


# --- CC transcript 解析 ---

def test_claude_prompt_extraction_filters_noise(tmp_path):
    # 只保留人敲的提示词：slash command 记录 / isMeta / tool_result / 子代理 sidechain 全过滤
    rows = [
        {"type": "summary", "summary": "历史摘要行"},
        _u("<command-name>/clear</command-name>"),
        _u("<local-command-caveat>Caveat: ...</local-command-caveat>", isMeta=True),
        _u("真提示词一", sessionId="s-abc", gitBranch="feature/x", promptId="prompt-cc-1"),
        _u([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _u([{"type": "image", "source": {}}, {"type": "text", "text": "带图提示词"}]),
        _u("子代理里的任务描述", isSidechain=True),
        _u("[Image: source: /tmp/x.png]", isMeta=True),
        _u("[Request interrupted by user for tool use]"),
        _u("<task-notification> <task-id>a36fa19b</task-id> 后台任务完成通知"),
        # 注入片段与真提示词同消息：只丢噪音片段、保留真提示词
        _u([{"type": "text", "text": "<system-reminder>召回的记忆背景</system-reminder>"},
            {"type": "text", "text": "混合消息里的真提示词"}]),
    ]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s-abc.jsonl", rows), "fallback", 5)
    assert parsed is not None
    assert [p.text for p in parsed.prompts] == ["真提示词一", "带图提示词", "混合消息里的真提示词"]
    assert parsed.session_id == "s-abc"
    assert parsed.prompts[0].timestamp is not None
    assert parsed.branch == "feature/x"  # transcript 自带 gitBranch，白拿


def test_claude_max_prompts_keeps_latest(tmp_path):
    rows = [_u(f"提示词{i}") for i in range(5)]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", 3)
    assert [p.text for p in parsed.prompts] == ["提示词2", "提示词3", "提示词4"]


def test_claude_none_max_prompts_keeps_all(tmp_path):
    rows = [_u(f"提示词{i}") for i in range(12)]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", None)
    assert [p.text for p in parsed.prompts] == [f"提示词{i}" for i in range(12)]


def test_claude_pending_tool_tracking(tmp_path):
    tool_use = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
    tool_result = [{"type": "tool_result", "tool_use_id": "t1", "content": "done"}]
    # 末条是无结果的 tool_use → pending（等授权/工具中）
    parsed = _parse_claude(_write_jsonl(tmp_path / "a.jsonl", [_u("跑一下"), _a(tool_use)]), "p", 3)
    assert parsed.pending_tool is True
    # tool_result 回来 → 不再 pending
    parsed = _parse_claude(
        _write_jsonl(tmp_path / "b.jsonl", [_u("跑一下"), _a(tool_use), _u(tool_result)]), "p", 3)
    assert parsed.pending_tool is False
    # 纯文本回复收尾 → 不 pending
    parsed = _parse_claude(_write_jsonl(tmp_path / "c.jsonl", [_u("你好"), _a("好的")]), "p", 3)
    assert parsed.pending_tool is False
    assert parsed.model == "claude-fable-5"


def test_claude_no_prompts_returns_none(tmp_path):
    rows = [_u("<command-name>/clear</command-name>")]
    assert _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", 3) is None


def test_claude_filters_bare_slash_command_records(tmp_path):
    # CC 对 slash 命令实测记两条 user 消息：<command-name> 包裹（既有过滤）+ 裸文本（本用例）
    rows = [
        _u("/compact"),
        _u("/cd ~/project/token-tracker"),
        _u("/plugin:cmd-name 带参数"),
        _u("/Users/stormzhang/x.md 这个文件帮我看下"),  # 路径开头的真提示词不误伤
    ]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", 5)
    assert [p.text for p in parsed.prompts] == ["/Users/stormzhang/x.md 这个文件帮我看下"]


def test_claude_skips_compact_summary_injection(tmp_path):
    # /compact 后注入的巨型摘要带 isCompactSummary/isVisibleInTranscriptOnly 标记：
    # 不进提示词、不消费待回答的 AskUserQuestion（压缩≠回答了提问）、不计活动时间
    ask = [{"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
            "input": {"questions": [{"question": "合并到 main 吗？", "options": [{"label": "合并"}]}]}}]
    rows = [
        _u("真提示词", ts=_iso(300)),
        _a(ask, ts=_iso(240)),
        _u("This session is being continued from a previous conversation...", ts=_iso(10),
           isCompactSummary=True, isVisibleInTranscriptOnly=True),
    ]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", 5)
    assert [p.text for p in parsed.prompts] == ["真提示词"]
    assert "合并到 main 吗？" in parsed.next_hint
    # 活动时间停在压缩前最后一条真实事件（240s 前），不被摘要（10s 前）顶新
    assert (datetime.now(UTC) - parsed.last_event).total_seconds() > 120


# --- 状态推断 ---

def test_infer_state_matrix():
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=5)
    stale = now - timedelta(minutes=5)
    ancient = now - timedelta(hours=2)
    assert _infer_state(now, fresh, False, False) == RUNNING
    assert _infer_state(now, stale, False, True) == RUNNING   # 心跳新鲜也算在跑
    assert _infer_state(now, stale, True, False) == ATTENTION
    assert _infer_state(now, stale, False, False) == WAITING
    assert _infer_state(now, ancient, False, False) == IDLE
    assert _infer_state(now, ancient, True, False) == ATTENTION  # pending 优先于 idle


# --- Codex rollout 解析 ---

def test_codex_parse(tmp_path):
    rows = [
        {"timestamp": "2026-07-12T02:00:00.000Z", "type": "session_meta",
         "payload": {"id": "cx-1", "timestamp": "2026-07-12T02:00:00.000Z", "cwd": "/tmp/nope/beta",
                     "git": {"commit_hash": "abc", "branch": "main"}}},
        {"timestamp": "2026-07-12T02:00:00.500Z", "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": "turn-cx-1"}},
        {"timestamp": "2026-07-12T02:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "<user_instructions>注入的模板</user_instructions>"}},
        {"timestamp": "2026-07-12T02:00:02.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "介绍下这个项目"}},
    ]
    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), 3)
    assert parsed is not None
    assert parsed.session_id == "cx-1"
    assert parsed.project == "beta"  # cwd 不存在 .git → 落最后一段
    assert [p.text for p in parsed.prompts] == ["介绍下这个项目"]
    assert parsed.branch == "main"  # session_meta.git.branch
    assert parsed.pending_tool is True  # task_started 无 complete

    rows.append({"timestamp": "2026-07-12T02:01:00.000Z", "type": "event_msg",
                 "payload": {"type": "task_complete"}})
    sidebar._parse_cache.clear()
    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), 3)
    assert parsed.pending_tool is False


def test_codex_none_max_prompts_keeps_all(tmp_path):
    rows = [
        {"timestamp": _iso(30), "type": "session_meta",
         "payload": {"id": "cx-all", "cwd": "/tmp/project"}},
        *[
            {"timestamp": _iso(20 - i), "type": "event_msg",
             "payload": {"type": "user_message", "message": f"提示词{i}"}}
            for i in range(12)
        ],
    ]
    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), None)
    assert [p.text for p in parsed.prompts] == [f"提示词{i}" for i in range(12)]


def test_codex_parse_response_item_messages(tmp_path):
    rows = [
        {"timestamp": _iso(60), "type": "session_meta",
         "payload": {"id": "cx-response-item", "cwd": "/tmp/project"}},
        {"timestamp": _iso(50), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "# AGENTS.md instructions\n注入规范"}]}},
        {"timestamp": _iso(40), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<environment_context>注入环境</environment_context>"}]}},
        {"timestamp": _iso(30), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<skill>注入 skill</skill>"}]}},
        {"timestamp": _iso(20), "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "<image name=[Image #1]>"},
             {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
             {"type": "input_text", "text": "</image>"},
             {"type": "input_text", "text": "解释这张截图"},
         ]}},
        {"timestamp": _iso(10), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "请确认是否继续修复？"}]}},
    ]

    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), 5)

    assert parsed is not None
    assert parsed.session_id == "cx-response-item"
    assert [p.text for p in parsed.prompts] == ["解释这张截图"]
    assert parsed.next_hint == "请确认是否继续修复？"


def test_codex_parse_dual_written_channels_dedupes_by_event(tmp_path):
    """双写文件（event_msg + response_item 各一份）以 event_msg 为准，孪生副本不重复；
    response_item 里 assistant 的结构化 JSON 不覆盖 event_msg 的正常回复。"""
    rows = [
        {"timestamp": _iso(70), "type": "session_meta",
         "payload": {"id": "cx-dual", "cwd": "/tmp/project"}},
        {"timestamp": _iso(60), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<recommended_plugins> 注入</recommended_plugins>"},
                                 {"type": "input_text", "text": "# AGENTS.md instructions\n注入规范"}]}},
        {"timestamp": _iso(50), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "你好"}]}},
        {"timestamp": _iso(49), "type": "event_msg",
         "payload": {"type": "user_message", "message": "你好"}},
        {"timestamp": _iso(40), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "什么是 4k"}]}},
        {"timestamp": _iso(39), "type": "event_msg",
         "payload": {"type": "user_message", "message": "什么是 4k"}},
        {"timestamp": _iso(30), "type": "event_msg",
         "payload": {"type": "agent_message", "message": "4K 是分辨率。"}},
        {"timestamp": _iso(20), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": '{"risk_level":"low"}'}]}},
    ]

    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), 5)

    assert parsed is not None
    assert [p.text for p in parsed.prompts] == ["你好", "什么是 4k"]
    assert parsed.next_hint == "4K 是分辨率。"


def test_codex_parse_response_item_only_keeps_real_repeats(tmp_path):
    """纯新版日志（无 event_msg/user_message）保留 response_item 通道，
    同文真实重复输入不去重，注入前缀过滤，next_hint 回退 response_item 回复。"""
    rows = [
        {"timestamp": _iso(50), "type": "session_meta",
         "payload": {"id": "cx-ri-only", "cwd": "/tmp/project"}},
        {"timestamp": _iso(40), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<recommended_plugins> 注入</recommended_plugins>"}]}},
        *[
            {"timestamp": _iso(30 - i), "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "继续做下一步"}]}}
            for i in range(3)
        ],
        {"timestamp": _iso(1), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "已完成，是否提交？"}]}},
    ]

    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), None)

    assert parsed is not None
    assert [p.text for p in parsed.prompts] == ["继续做下一步"] * 3
    assert parsed.next_hint == "已完成，是否提交？"


def test_codex_parse_filters_approval_tool_and_goal_injections(tmp_path):
    """自动审批工具注入的 transcript 历史与 goal 模式内部上下文不进提示词列表。"""
    rows = [
        {"timestamp": _iso(50), "type": "session_meta",
         "payload": {"id": "cx-inject", "cwd": "/tmp/project"}},
        {"timestamp": _iso(40), "type": "event_msg",
         "payload": {"type": "user_message",
                     "message": "The following is the Codex agent history whose request action you are approving..."}},
        {"timestamp": _iso(30), "type": "event_msg",
         "payload": {"type": "user_message",
                     "message": "<codex_internal_context source=\"goal\"> Continue working toward the act..."}},
        {"timestamp": _iso(25), "type": "event_msg",
         "payload": {"type": "user_message",
                     "message": ">>> TRANSCRIPT DELTA START [62] tool exec result: Script completed..."}},
        {"timestamp": _iso(20), "type": "event_msg",
         "payload": {"type": "user_message", "message": "真实提示词"}},
    ]

    parsed = _parse_codex(_write_jsonl(tmp_path / "rollout.jsonl", rows), 5)

    assert parsed is not None
    assert [p.text for p in parsed.prompts] == ["真实提示词"]


def test_hint_text_returns_empty_for_raw_json_reply():
    """结构化输出（自动审批 JSON）不作「下一步」提示。"""
    assert sidebar._hint_text('{"risk_level":"low","outcome":"allow"}') == ""
    assert sidebar._hint_text('["a", "b"]') == ""
    assert sidebar._hint_text("修复已完成，是否提交？") == "修复已完成，是否提交？"


# --- 扫描：窗口过滤 + 排序 + 缓存 ---

def _make_claude_base(tmp_path: Path) -> Path:
    base = tmp_path / "projects"
    (base / "-Users-x-project-alpha").mkdir(parents=True)
    return base


def test_scan_claude_window_filter_and_sort(tmp_path):
    base = _make_claude_base(tmp_path)
    d = base / "-Users-x-project-alpha"
    now = datetime.now(UTC)
    old = _write_jsonl(d / "old.jsonl", [_u("久远会话", ts=_iso(24 * 3600))])
    ancient_ts = (now - timedelta(hours=24)).timestamp()
    os.utime(old, (ancient_ts, ancient_ts))
    mid = _write_jsonl(d / "mid.jsonl", [_u("两分钟前的", ts=_iso(120))])
    mid_ts = (now - timedelta(seconds=120)).timestamp()
    os.utime(mid, (mid_ts, mid_ts))
    fresh = _write_jsonl(d / "fresh.jsonl", [_u("刚刚的", ts=_iso(3))])
    fresh_ts = (now - timedelta(seconds=3)).timestamp()
    os.utime(fresh, (fresh_ts, fresh_ts))
    # 回归（design-agent 实测）：内容 24h 没动、但文件 mtime 被 CC 常驻进程触碰得很新
    # ——活动时间以内容事件为准，这种会话必须被窗口过滤，不能靠 mtime 冒到第一位
    _write_jsonl(d / "touched.jsonl", [_u("很久以前的内容", ts=_iso(24 * 3600))])

    got = _scan_claude_sessions(now - timedelta(hours=12), now, None, 3, dirs=[str(base)])
    got.sort(key=lambda s: s.last_activity, reverse=True)
    assert [s.session_id for s in got] == ["fresh", "mid"]  # 24h 前的与「假触碰」的都被过滤
    assert got[0].state == RUNNING
    assert got[1].state == WAITING
    assert got[0].project == "alpha"  # 无 cwd 时从目录名解码
    assert got[0].agent_id == "claude-code"


def test_scan_claude_uses_cache_for_unchanged_file(tmp_path, monkeypatch):
    base = _make_claude_base(tmp_path)
    d = base / "-Users-x-project-alpha"
    _write_jsonl(d / "s.jsonl", [_u("你好")])
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=12)
    assert len(_scan_claude_sessions(cutoff, now, None, 3, dirs=[str(base)])) == 1
    # 文件未变 → 第二次扫描不再触发解析（mtime+size+max_prompts 缓存命中）
    monkeypatch.setattr(sidebar, "_parse_claude",
                        lambda *a, **k: pytest.fail("cache miss: reparsed unchanged file"))
    assert len(_scan_claude_sessions(cutoff, now, None, 3, dirs=[str(base)])) == 1


def test_scan_claude_does_not_cache_result_if_file_changes_during_parse(tmp_path, monkeypatch):
    base = _make_claude_base(tmp_path)
    path = _write_jsonl(base / "-Users-x-project-alpha" / "s.jsonl", [_u("第一条")])
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=12)
    parse_claude = sidebar._parse_claude
    appended = False

    def parse_then_append(*args, **kwargs):
        nonlocal appended
        parsed = parse_claude(*args, **kwargs)
        if not appended:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_u("解析期间追加"), ensure_ascii=False) + "\n")
            appended = True
        return parsed

    monkeypatch.setattr(sidebar, "_parse_claude", parse_then_append)
    first = _scan_claude_sessions(cutoff, now, None, None, dirs=[str(base)])
    second = _scan_claude_sessions(cutoff, now, None, None, dirs=[str(base)])

    assert [p.text for p in first[0].prompts] == ["第一条"]
    assert [p.text for p in second[0].prompts] == ["第一条", "解析期间追加"]


def test_scan_claude_cache_isolated_by_max_prompts(tmp_path):
    base = _make_claude_base(tmp_path)
    d = base / "-Users-x-project-alpha"
    _write_jsonl(d / "s.jsonl", [_u(f"提示词{i}") for i in range(5)])
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=12)

    first = _scan_claude_sessions(cutoff, now, None, 2, dirs=[str(base)])
    second = _scan_claude_sessions(cutoff, now, None, 4, dirs=[str(base)])

    assert [p.text for p in first[0].prompts] == ["提示词3", "提示词4"]
    assert [p.text for p in second[0].prompts] == ["提示词1", "提示词2", "提示词3", "提示词4"]


def test_scan_sessions_caps_at_max_sessions(monkeypatch):
    # 窗口内超过 max_sessions 时按最近活动取前 N；不足 N 全显（切片天然满足）
    now = datetime.now(UTC)
    fake = [LiveSession(agent_id="claude-code", session_id=f"s{i}", project=f"p{i}",
                        last_activity=now - timedelta(minutes=i), state=WAITING,
                        prompts=[Prompt("x", now)])
            for i in range(12)]
    monkeypatch.setattr(sidebar, "_live_claude_sids", lambda: None)  # 不读真实注册表
    monkeypatch.setattr(sidebar, "_scan_claude_sessions", lambda *a, **k: fake)
    monkeypatch.setattr(sidebar, "_scan_codex_sessions", lambda *a, **k: [])
    monkeypatch.setattr(sidebar, "_scan_kimi_sessions", lambda *a, **k: [])  # 不读真实 ~/.kimi-code
    monkeypatch.setattr(sidebar, "_scan_pi_sessions", lambda *a, **k: [])  # 不读真实 ~/.pi
    got = sidebar.scan_sessions()
    assert len(got) == 10
    assert [s.session_id for s in got] == [f"s{i}" for i in range(10)]  # 最新的 10 个


# --- CC 会话注册表探活（已关闭的会话不算活跃）---

def _reg_write(reg_dir: Path, pid: int, sid: str, started_at_ms: float | None = None) -> None:
    reg_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {"pid": pid, "sessionId": sid}
    if started_at_ms is not None:
        data["startedAt"] = started_at_ms
    (reg_dir / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def test_live_claude_sids_missing_registry_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sidebar, "claude_home", lambda: str(tmp_path))
    assert sidebar._live_claude_sids() is None  # 老版本 CC 无注册表：无法判断、不过滤


def test_live_claude_sids_maps_alive_pids_to_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(sidebar, "claude_home", lambda: str(tmp_path))
    reg = tmp_path / "sessions"
    _reg_write(reg, 111, "sa", 1783617514463)  # startedAt 毫秒 → 传给探活时转秒
    _reg_write(reg, 222, "sb")
    (reg / "333.json").write_text(json.dumps({"sessionId": "sd"}), encoding="utf-8")  # pid 从文件名兜底
    (reg / "no-pid.json").write_text(json.dumps({"sessionId": "sc"}), encoding="utf-8")  # 无 pid 可依 → 跳过
    (reg / "broken.json").write_text("{oops", encoding="utf-8")  # 坏文件只跳过自己

    seen: dict[int, float | None] = {}

    def fake_alive(want):
        seen.update(want)
        return {111, 333}

    monkeypatch.setattr(sidebar, "_alive_pids", fake_alive)
    assert sidebar._live_claude_sids() == {"sa", "sd"}
    assert seen == {111: 1783617514.463, 222: None, 333: None}


def test_alive_pids_own_process_and_start_time_guard():
    me = os.getpid()
    assert sidebar._alive_pids({}) == set()
    assert me in sidebar._alive_pids({me: None})  # 不带启动时间：纯探活
    # 注册的启动时间与真实启动时刻差太远 → 视为 pid 已被复用、判死
    assert sidebar._alive_pids({me: 0.0}) == set()
    # 与真实启动时刻一致（epoch 比对，不受 TZ 环境变量影响——procStart 字符串
    # 按 CC 的时区渲染、ps lstart 按本进程 TZ 渲染，字符串比对会全军覆没）→ 存活
    # ps 强制 C locale 保英文日期（与 _alive_pids 同口径；中文 locale 下裸 ps 输出中文日期）
    lstart = subprocess.run(["ps", "-o", "lstart=", "-p", str(me)],
                            capture_output=True, text=True,
                            env={**os.environ, "LC_ALL": "C"}).stdout.strip()
    real_start = sidebar._parse_lstart(" ".join(lstart.split()))
    assert real_start is not None
    assert me in sidebar._alive_pids({me: real_start})


def test_parse_lstart_roundtrip_and_failure():
    assert sidebar._parse_lstart("not a date") is None  # 非英文 locale 等解析失败 → 只探活不比时间
    ts = sidebar._parse_lstart("Thu Jul 9 17:18:34 2026")
    assert ts is not None
    assert time.localtime(ts)[:6] == (2026, 7, 9, 17, 18, 34)


def test_scan_claude_drops_sessions_not_in_registry(tmp_path):
    base = _make_claude_base(tmp_path)
    d = base / "-Users-x-project-alpha"
    _write_jsonl(d / "open.jsonl", [_u("进程还开着")])
    _write_jsonl(d / "closed.jsonl", [_u("已经退出的")])
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=12)
    got = _scan_claude_sessions(cutoff, now, None, 3, dirs=[str(base)], live_sids={"open"})
    assert [s.session_id for s in got] == ["open"]  # 不在注册表 = 已关闭，不进列表
    got = _scan_claude_sessions(cutoff, now, None, 3, dirs=[str(base)], live_sids=None)
    assert {s.session_id for s in got} == {"open", "closed"}  # 注册表不可用 → 不过滤


def test_registry_update_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(sidebar, "claude_home", lambda: str(tmp_path))
    now = datetime.now(UTC)
    cc = LiveSession(agent_id="claude-code", session_id="s", project="p",
                     last_activity=now, state=WAITING)
    cx = LiveSession(agent_id="codex", session_id="c", project="p",
                     last_activity=now, state=WAITING)
    assert sidebar.registry_update_hint([cc]) is True   # 有 CC 会话且无注册表 → 提示升级
    assert sidebar.registry_update_hint([cx]) is False  # 纯 codex 列表与 CC 版本无关
    (tmp_path / "sessions").mkdir()
    assert sidebar.registry_update_hint([cc]) is False  # 注册表在 → 不提示


def test_render_update_hint_line():
    now = datetime.now(UTC)
    s = LiveSession(agent_id="claude-code", session_id="s", project="proj",
                    last_activity=now, state=WAITING, prompts=[Prompt("你好", now)])
    console = Console(record=True, width=80)
    console.print(render_sidebar([s], update_hint=True))
    assert "Claude Code" in console.export_text()  # zh/en 文案都含该词
    console = Console(record=True, width=80)
    console.print(render_sidebar([s]))
    assert "Claude Code" not in console.export_text()


def test_read_status_merges_claude_and_codex_terminal_maps(tmp_path, monkeypatch):
    status = tmp_path / "tt-status.json"
    status.write_text(json.dumps({
        "session_id": "s-live", "_received_at": datetime.now(UTC).isoformat(),
        "_terminal_map": {"s-live": {"iterm": "w0t1p0:AAA-BBB", "tmux": "%3"}},
    }), encoding="utf-8")
    codex_map = tmp_path / "tt-terminal-map.json"
    codex_map.write_text(json.dumps({
        "_terminal_map": {"cx-live": {"iterm": "w0t2p0:CODEX"}},
    }), encoding="utf-8")
    monkeypatch.setattr(sidebar.config, "STATUS_FILE", str(status))
    monkeypatch.setattr(sidebar.config, "TERMINAL_MAP_FILE", str(codex_map))
    hb, term_map = sidebar._read_status()
    assert hb is not None and hb[0] == "s-live"
    assert term_map["s-live"]["iterm"] == "w0t1p0:AAA-BBB"
    assert term_map["cx-live"]["iterm"] == "w0t2p0:CODEX"
    assert sidebar.terminal_info("s-live")["tmux"] == "%3"
    assert sidebar.terminal_info("cx-live")["iterm"] == "w0t2p0:CODEX"
    assert sidebar.terminal_info("unknown") == {}


def test_read_status_returns_codex_map_without_claude_status(tmp_path, monkeypatch):
    codex_map = tmp_path / "tt-terminal-map.json"
    codex_map.write_text(json.dumps({
        "_terminal_map": {"cx-only": {"tmux": "%9"}},
    }), encoding="utf-8")
    monkeypatch.setattr(sidebar.config, "STATUS_FILE", str(tmp_path / "missing-status.json"))
    monkeypatch.setattr(sidebar.config, "TERMINAL_MAP_FILE", str(codex_map))
    hb, term_map = sidebar._read_status()
    assert hb is None
    assert term_map == {"cx-only": {"tmux": "%9"}}


def test_scan_claude_attaches_terminal(tmp_path):
    base = _make_claude_base(tmp_path)
    _write_jsonl(base / "-Users-x-project-alpha" / "s1.jsonl", [_u("你好")])
    now = datetime.now(UTC)
    term_map = {"s1": {"iterm": "w0t2p0:CCC"}}
    got = _scan_claude_sessions(now - timedelta(hours=12), now, None, 3,
                                dirs=[str(base)], term_map=term_map)
    assert got[0].terminal == {"iterm": "w0t2p0:CCC"}


def test_scan_codex_attaches_terminal(tmp_path):
    base = tmp_path / "sessions"
    base.mkdir()
    rows = [
        {"timestamp": _iso(70), "type": "session_meta",
         "payload": {"id": "cx-1", "cwd": "/tmp/project-codex"}},
        {"timestamp": _iso(60), "type": "event_msg",
         "payload": {"type": "user_message", "message": "修复跳转"}},
    ]
    _write_jsonl(base / "rollout.jsonl", rows)
    now = datetime.now(UTC)
    got = sidebar._scan_codex_sessions(
        now - timedelta(hours=5), now, None, 3, sessions_dir=str(base),
        term_map={"cx-1": {"iterm": "w0t3p0:CODEX"}},
    )
    assert got[0].terminal == {"iterm": "w0t3p0:CODEX"}


def test_render_head_click_meta_and_link_style():
    # 链接语义统一：有终端定位=可点（meta 带 app. 前缀防派发到 Static 静默失败）
    # 且项目名带蓝色下划线标记；无定位=无 meta 无链接样式，所见即所得
    from rich.text import Text

    def _collect(session):
        actions, underlined = [], False
        for r in render_sidebar([session]).renderables:
            if isinstance(r, Text):
                for span in r.spans:
                    meta = getattr(span.style, "meta", None)
                    if meta and "@click" in meta:
                        actions.append(meta["@click"])
                    if isinstance(span.style, str) and "underline" in span.style:
                        underlined = True
        return actions, underlined

    now = datetime.now(UTC)
    mapped = LiveSession(agent_id="claude-code", session_id="sX", project="p",
                         last_activity=now, state=WAITING,
                         prompts=[Prompt("x", now)], terminal={"iterm": "w0t0p0:X"})
    actions, underlined = _collect(mapped)
    assert actions == ["app.jump_to('sX')"]
    assert underlined
    unmapped = LiveSession(agent_id="claude-code", session_id="sY", project="p",
                           last_activity=now, state=WAITING,
                           prompts=[Prompt("x", now)], terminal={})
    actions, underlined = _collect(unmapped)
    assert actions == [] and not underlined


def test_click_link_id_stable_across_renders():
    # 回归：Rich 每次 from_meta 生成随机 link_id，0.5s 整帧重绘若每帧新建样式，
    # Textual 按 link_id 定位的 hover 高亮半秒即失联（下划线时有时无）
    from rich.text import Text
    now = datetime.now(UTC)
    session = LiveSession(agent_id="claude-code", session_id="sZ", project="p",
                          last_activity=now, state=WAITING,
                          prompts=[Prompt("x", now)], terminal={"iterm": "w0t0p0:X"})

    def _link_ids(group):
        ids = set()
        for r in group.renderables:
            if isinstance(r, Text):
                for span in r.spans:
                    st = span.style
                    if getattr(st, "_meta", None):
                        ids.add(st.link_id)
        return ids

    first, second = _link_ids(render_sidebar([session])), _link_ids(render_sidebar([session]))
    assert len(first) == 1
    assert first == second  # 跨帧稳定


def test_heartbeat_marks_running(tmp_path):
    base = _make_claude_base(tmp_path)
    d = base / "-Users-x-project-alpha"
    p = _write_jsonl(d / "hb.jsonl", [_u("在等心跳")])
    now = datetime.now(UTC)
    stale_ts = (now - timedelta(minutes=5)).timestamp()
    os.utime(p, (stale_ts, stale_ts))
    hb = ("hb", now - timedelta(seconds=3))
    got = _scan_claude_sessions(now - timedelta(hours=12), now, hb, 3, dirs=[str(base)])
    assert got[0].state == RUNNING  # 文件虽停写，statusline 心跳仍新鲜


# --- 渲染 ---

def test_render_sidebar_smoke():
    now = datetime.now(UTC)
    sessions = [
        LiveSession(agent_id="claude-code", session_id="s1", project="fuxi",
                    last_activity=now - timedelta(seconds=10), state=RUNNING,
                    prompts=[Prompt("全面看下这个项目", now), Prompt("全去做", now)],
                    model="claude-fable-5"),
        LiveSession(agent_id="codex", session_id="s2", project="wx-clawbot",
                    last_activity=now - timedelta(minutes=9), state=WAITING,
                    prompts=[Prompt("介绍下这个项目", now)]),
    ]
    console = Console(record=True, width=60, force_terminal=True)
    console.print(render_sidebar(sessions))
    text = console.export_text()
    assert "fuxi" in text
    assert "全去做" in text
    assert "wx-clawbot" in text
    assert "Codex" in text


def test_render_prompt_wraps_two_lines_with_ellipsis():
    # 长提示词最多折 2 行、末行省略号；正文右侧留 2 格（行宽 ≤ 终端宽 - 2）
    now = datetime.now(UTC)
    long_prompt = "这是一条非常长的提示词内容" * 12
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt(long_prompt, now)])]
    # height 必须显式给：TERM=dumb 的环境（CI / 非交互 shell）里 Rich 的 size 走
    # is_dumb_terminal 分支、忽略 width 参数回退 80 列；宽高都给才命中 _width/_height 快路径
    console = Console(record=True, width=40, height=25, force_terminal=True)
    console.print(render_sidebar(sessions))
    out_lines = console.export_text().splitlines()
    body_lines = [ln for ln in out_lines if "提示词" in ln or "非常长" in ln]
    assert len(body_lines) == 2
    assert body_lines[-1].rstrip().endswith("…")
    assert all(len(ln.rstrip()) <= 40 - 2 for ln in body_lines)  # 右侧留白 2 格


def test_render_history_prompt_uses_one_line_latest_uses_two():
    now = datetime.now(UTC)
    sessions = [LiveSession(
        agent_id="claude-code",
        session_id="s1",
        project="compact-project",
        last_activity=now,
        state=WAITING,
        prompts=[
            Prompt("HISTORY-" + "old" * 40, now),
            Prompt("LATEST-" + "new" * 40, now),
        ],
    )]
    console = Console(record=True, width=40)
    console.print(render_sidebar(sessions))
    out = console.export_text().splitlines()
    head_idx = next(i for i, line in enumerate(out) if "compact-project" in line)
    prompt_lines = out[head_idx + 1:]

    assert len(prompt_lines) == 3
    assert prompt_lines[0].startswith("├ HISTORY-") and prompt_lines[0].rstrip().endswith("…")
    assert prompt_lines[1].startswith("└ LATEST-")
    assert prompt_lines[2].startswith("  ") and prompt_lines[2].rstrip().endswith("…")


def test_render_idle_session_only_shows_latest_prompt_without_hint():
    now = datetime.now(UTC)
    sessions = [LiveSession(
        agent_id="codex",
        session_id="idle",
        project="idle-project",
        last_activity=now,
        state=IDLE,
        prompts=[Prompt("OLD-HIDDEN", now), Prompt("LATEST-" + "new" * 40, now)],
        next_hint="HINT-HIDDEN",
    )]
    console = Console(record=True, width=40)
    console.print(render_sidebar(sessions))
    out = console.export_text().splitlines()
    text = "\n".join(out)
    head_idx = next(i for i, line in enumerate(out) if "idle-project" in line)
    prompt_lines = out[head_idx + 1:]

    assert "OLD-HIDDEN" not in text
    assert "HINT-HIDDEN" not in text
    assert len(prompt_lines) == 2
    assert prompt_lines[0].startswith("└ LATEST-")
    assert prompt_lines[1].startswith("  ") and prompt_lines[1].rstrip().endswith("…")


def test_render_tree_glyphs_count_prompts():
    # 树状语义：每条提示词一个分支符（├，末条 └）——数分支即数提示词；
    # 历史提示词只占一行，超出直接省略，不再产生 │ 续行
    now = datetime.now(UTC)
    long_prompt = "很长的提示词内容" * 15
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt(long_prompt, now), Prompt("第二条", now),
                                     Prompt("第三条", now)])]
    console = Console(record=True, width=40, force_terminal=True)
    console.print(render_sidebar(sessions))
    out = console.export_text().splitlines()
    assert sum(1 for ln in out if ln.startswith("├")) == 2
    assert sum(1 for ln in out if ln.startswith("└")) == 1
    assert not any(ln.startswith("│") for ln in out)
    assert next(ln for ln in out if ln.startswith("├")).rstrip().endswith("…")


def test_render_prompt_short_stays_single_line():
    now = datetime.now(UTC)
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt("短提示词", now)])]
    console = Console(record=True, width=40, force_terminal=True)
    console.print(render_sidebar(sessions))
    body_lines = [ln for ln in console.export_text().splitlines() if "短提示词" in ln]
    assert len(body_lines) == 1
    assert "…" not in body_lines[0]


def test_next_hint_scores_action_over_narrative(tmp_path):
    # 「下一步」= 打分精简：完成陈述被滤掉，行动/征询句保留；后续新回复覆盖旧的
    rows = [
        _u("改一下"),
        _a("改好了。\n\n要我继续做 B 么"),
        _u("继续"),
        _a([{"type": "text", "text": "B 也完成。\n验证后我们再发版"}]),
    ]
    parsed = _parse_claude(_write_jsonl(tmp_path / "s.jsonl", rows), "p", 5)
    assert parsed.next_hint == "验证后我们再发版"


def test_hint_text_scoring_and_noise_filter():
    from token_tracker.sidebar import _hint_text
    reply = "\n".join([
        "## 结论",
        "细节修改已提交（abc123）。",     # 完成陈述 → 滤掉
        "```python",                     # 代码块整段剔除
        "print('hi')",
        "```",
        "---",                           # 分隔线剔除
        "| a | b |",                     # 表格行剔除
        "**加粗的建议**",                 # 粗体星号剥掉 + 「建议」行动词保留
        "重启侧边栏生效。要不要我继续做 B？",  # 切句：两句都是正分
    ])
    got = _hint_text(reply)
    assert got.splitlines() == ["加粗的建议", "重启侧边栏生效。", "要不要我继续做 B？"]
    assert "已提交" not in got and "print" not in got and "##" not in got and "**" not in got


def test_hint_text_falls_back_to_last_line():
    from token_tracker.sidebar import _hint_text
    # 纯汇报无任何行动信号 → 回退最后一个有效行（单行，精简）
    assert _hint_text("统计口径核对完毕。\n数字与后台一致。") == "数字与后台一致。"


def test_hint_text_only_scans_last_paragraph():
    from token_tracker.sidebar import _hint_text
    # 只看最后一段（主人定）：中段的大纲列表、前段的问句都不再进「下一步」
    reply = "\n".join([
        "大纲如下：",
        "1. 开头要不要放钩子？",
        "2. 中间论证",
        "",
        "先看第 1 节，看完告诉我。",
    ])
    assert _hint_text(reply) == "先看第 1 节，看完告诉我。"


def test_hint_text_trailing_code_block_falls_back():
    from token_tracker.sidebar import _hint_text
    # 结尾是内含空行的代码块（段落切分的坑）：代码不漏进提示、回退到代码块前的段落
    reply = "\n".join([
        "跑一下这个验证：",
        "",
        "```python",
        "a = 1",
        "",
        "b = 2",
        "```",
    ])
    assert _hint_text(reply) == "跑一下这个验证："


def test_ask_user_question_takes_priority(tmp_path):
    # 待回答的 AskUserQuestion（结构化字段，零猜测）优先于文本打分
    ask = [{"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
            "input": {"questions": [{"question": "范围选哪个？", "header": "范围",
                                     "options": [{"label": "只做方向一"}, {"label": "一二连做"}],
                                     "multiSelect": False}]}}]
    rows = [_u("开工"), _a("我先确认范围。建议尽快定。"), _a(ask)]
    parsed = _parse_claude(_write_jsonl(tmp_path / "a.jsonl", rows), "p", 5)
    assert parsed.next_hint == "范围选哪个？\n· 只做方向一 / 一二连做"
    # 用户回答（tool_result）后提问被消费 → 回落文本打分
    rows += [_u([{"type": "tool_result", "tool_use_id": "t1", "content": "只做方向一"}]),
             _a("好，方向一开工。做完说一声。")]
    parsed = _parse_claude(_write_jsonl(tmp_path / "b.jsonl", rows), "p", 5)
    assert "范围选哪个" not in parsed.next_hint
    assert parsed.next_hint.splitlines()[-1] == "做完说一声。"  # 「开工」也是行动词，前一句保留属合理


def test_codex_next_hint_from_agent_message(tmp_path):
    rows = [
        {"timestamp": "2026-07-12T02:00:00.000Z", "type": "session_meta",
         "payload": {"id": "cx", "timestamp": "2026-07-12T02:00:00.000Z", "cwd": "/tmp/x"}},
        {"timestamp": "2026-07-12T02:00:02.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "做个功能"}},
        {"timestamp": "2026-07-12T02:00:09.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "完成了。\n需要我补测试吗"}},
    ]
    parsed = _parse_codex(_write_jsonl(tmp_path / "r.jsonl", rows), 5)
    assert parsed.next_hint == "需要我补测试吗"


def test_render_next_hint_and_spinner_frame():
    now = datetime.now(UTC)
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=RUNNING,
                            prompts=[Prompt("干活", now)], next_hint="验证后我们再发版")]
    console = Console(record=True, width=60, force_terminal=True)
    console.print(render_sidebar(sessions, spinner_frame=0))
    console.print(render_sidebar(sessions, spinner_frame=1))
    out = console.export_text()
    assert "验证后我们再发版" in out
    assert "✢ proj" in out and "✳ proj" in out  # 帧不同 → 会话头星形符号轮转（标题自带 ✳，不数全局）


def test_split_sidebar_shows_all_prompts_newest_first_as_tree():
    now = datetime.now(UTC)
    prompts = [Prompt(f"[P{i:02d}]", now + timedelta(seconds=i)) for i in range(12)]
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="hidden-project",
                            branch="hidden-branch", last_activity=now, state=WAITING,
                            prompts=prompts, next_hint="hidden-next-step")]
    console = Console(record=True, width=40, force_terminal=True)
    console.print(render_split_sidebar(sessions))
    lines = console.export_text().splitlines()
    output = "\n".join(lines)

    assert lines[0].startswith("├ 12. [P11]")  # 最新提示词编号最大，无标题 / 时钟
    assert "hidden-project" not in output
    assert "hidden-branch" not in output
    assert "hidden-next-step" not in output
    assert "tt sidebar" not in output
    assert "[P00]" in output and "[P01]" in output
    positions = [next(i for i, line in enumerate(lines) if token in line)
                 for token in (f"[P{i:02d}]" for i in range(11, -1, -1))]
    assert positions == sorted(positions)
    assert all(second - first == 2 for first, second in pairwise(positions))
    assert all(lines[first + 1] == "│" for first in positions[:-1])
    for sequence, position in zip(range(12, 0, -1), positions, strict=True):
        marker = "└" if sequence == 1 else "├"
        assert lines[position].startswith(f"{marker} {sequence:>2}. [P{sequence - 1:02d}]")

    default_console = Console(record=True, width=40)
    default_console.print(render_sidebar(sessions))
    default_output = default_console.export_text()
    assert "hidden-project" in default_output and "hidden-next-step" in default_output


def test_split_sidebar_empty_waits_for_first_prompt():
    console = Console(record=True, width=40)
    console.print(render_split_sidebar([]))

    actual = " ".join(console.export_text().split())
    assert actual == " ".join(t("sidebar_waiting_prompt").split())


def test_split_sidebar_latest_highlight_excludes_tree_and_leaves_one_right_cell():
    from rich.cells import cell_len

    from token_tracker.ui.theme import get_active_theme

    now = datetime.now(UTC)
    full_text = "完整内容" * 100 + "\n第二段"
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt("older", now), Prompt(full_text, now)])]
    console = Console(width=30, force_terminal=True)
    options = console.options.update(width=30)
    lines = console.render_lines(render_split_sidebar(sessions), options, pad=False)
    old_line = next(line for line in lines if "older" in "".join(segment.text for segment in line))
    latest_start = next(i for i, line in enumerate(lines)
                        if "完整内容" in "".join(segment.text for segment in line))
    latest_end = next(i for i in range(latest_start, len(lines))
                      if "".join(segment.text for segment in lines[i]).rstrip() == "│")
    latest_lines = lines[latest_start:latest_end]

    assert all(segment.style is None or segment.style.bgcolor is None for segment in old_line)
    assert all(segment.style is None or not segment.style.dim
               for line in lines for segment in line if segment.text)
    expected_background = get_active_theme()["base"]["overlay0"]
    for line in latest_lines:
        assert cell_len("".join(segment.text for segment in line)) == 29
        assert line[0].text in {"├ ", "│ ", "└ "}
        assert line[0].style is None or line[0].style.bgcolor is None
        assert all(segment.style is not None and segment.style.bgcolor is not None
                   for segment in line[1:] if segment.text)
        assert {
            segment.style.bgcolor.triplet.hex
            for segment in line[1:]
            if segment.text and segment.style and segment.style.bgcolor and segment.style.bgcolor.triplet
        } == {expected_background}
    prefix_width = len("├ 2. ")
    rendered_latest = "".join(
        "".join(segment.text for segment in line)[prefix_width:].rstrip()
        for line in latest_lines
    )
    assert rendered_latest.count("完整内容") == 100
    assert "第二段" in rendered_latest


def test_render_hint_wraps_to_three_lines_then_ellipsis():
    # 「下一步」一行原文正常折行（主人定）：最多 3 行、仍放不下才在第三行末省略；
    # 折行/续行等宽空格悬挂对齐正文列，右侧留 2 格
    now = datetime.now(UTC)
    long_line = "这是特别长的建议内容" * 8  # 160 格，远超 3 行容量
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt("短", now)],
                            next_hint=f"第一行建议\n{long_line}\n1. 选项甲")]
    # 同上：显式 height，避免 TERM=dumb 环境下 Rich 忽略 width 回退 80 列
    console = Console(record=True, width=40, height=25, force_terminal=True)
    console.print(render_sidebar(sessions))
    out = console.export_text().splitlines()
    arrow_idx = next(i for i, ln in enumerate(out) if "↳" in ln)
    hint_lines = out[arrow_idx:]
    assert len(hint_lines) == 5  # 1 + 超长行折满 3 行 + 1
    assert "第一行建议" in hint_lines[0]
    assert not hint_lines[1].rstrip().endswith("…")  # 前两折行正常显示
    assert hint_lines[3].rstrip().endswith("…")      # 3 行仍放不下 → 末行省略
    assert "选项甲" in hint_lines[4]
    first_text_col = hint_lines[0].index(":") + 2  # 正文起始列
    assert all(ln[:first_text_col].strip() == "" for ln in hint_lines[1:])  # 悬挂对齐
    assert all(len(ln.rstrip()) <= 40 - 2 for ln in hint_lines)  # 右侧留白 2 格


def test_fold_cjk_fills_and_ascii_word_atomic():
    from rich.cells import cell_len

    from token_tracker.ui.sidebar import _fold
    # CJK 逐字可折、填满换行
    assert _fold("中" * 10, 8) == ["中中中中", "中中中中", "中中"]
    # 英文单词是原子 token：放不下整个挪下一行，不拦腰切
    got = _fold("中中中中 provider 之后", 10)
    assert any(ln == "provider" or ln.startswith("provider") for ln in got)
    assert all("provide" not in ln or "provider" in ln for ln in got)  # 没有被切成 provide/r
    # 单 token 超过整行宽（长 URL）：按格硬切兜底
    got = _fold("https://example.com/very/long/path", 10)
    assert len(got) > 1 and all(cell_len(ln) <= 10 for ln in got)


def test_render_mixed_cjk_ascii_fills_width():
    # 回归：Rich 词折行把空格后的整段中文当一个词挪下行，省略号/换行
    # 出现在断词点、右侧大片留白；硬折后每行应填满可用宽度再折行
    now = datetime.now(UTC)
    sessions = [LiveSession(agent_id="claude-code", session_id="s1", project="proj",
                            last_activity=now, state=WAITING,
                            prompts=[Prompt("短", now)],
                            next_hint="取最后一条 AI 回复的尾部继续更多内容这一行非常长必须折行")]
    console = Console(record=True, width=40, force_terminal=True)
    console.print(render_sidebar(sessions))
    from rich.cells import cell_len
    hint_line = next(ln for ln in console.export_text().splitlines() if "↳" in ln)
    # 按终端格宽（CJK 一字两格）首行应填满到右留白附近，不在断词点提前留白换行
    assert cell_len(hint_line.rstrip()) >= 40 - 2 - 2


def test_render_header_count_and_compact_clock_line():
    import re as _re
    now = datetime.now(UTC)
    sessions = [LiveSession(agent_id="claude-code", session_id=f"s{i}", project="p",
                            last_activity=now, state=WAITING, prompts=[Prompt("x", now)])
                for i in range(3)]
    console = Console(record=True, width=60, force_terminal=True)
    console.print(render_sidebar(sessions))
    out = console.export_text().splitlines()
    assert "3" in out[0]  # 活跃会话计数在标题行
    labels = (t("sidebar_tz_bj"), t("sidebar_tz_la"), t("sidebar_tz_ldn"))
    clock_lines = [ln for ln in out if all(label in ln for label in labels)]
    assert len(clock_lines) == 1
    clock_line = clock_lines[0]
    assert len(_re.findall(r"\d{2}:\d{2}", clock_line)) == 3
    assert not _re.search(r"\d{2}:\d{2}:\d{2}", clock_line)
    assert not _re.search(r"\d{2}-\d{2}", clock_line)
    assert "周" not in clock_line


def test_render_branch_and_session_separator():
    # 项目名后接 (分支)（statusline L1 同款）；会话块之间一条分割线、首块前是空行
    now = datetime.now(UTC)
    mk = lambda i: LiveSession(agent_id="claude-code", session_id=f"s{i}", project=f"p{i}",  # noqa: E731
                               last_activity=now, state=WAITING, branch="main",
                               prompts=[Prompt("x", now)])
    console = Console(record=True, width=50, force_terminal=True)
    console.print(render_sidebar([mk(1), mk(2), mk(3)]))
    out = console.export_text().splitlines()
    assert sum(1 for ln in out if "p1(main)" in ln or "p2(main)" in ln or "p3(main)" in ln) == 3
    rules = [ln for ln in out if set(ln.strip()) == {"─"}]
    assert len(rules) == 2  # 3 个会话块之间 2 条分割线


def test_render_sidebar_empty():
    console = Console(record=True, width=60)
    console.print(render_sidebar([]))
    assert "tt sidebar" in console.export_text()


# --- Kimi wire.jsonl 解析（<kimi_home>/sessions/<wd_*>/<session_*>/agents/main/wire.jsonl） ---

def _ms(seconds_ago: float = 60) -> int:
    """Kimi wire 事件时间戳：epoch 毫秒（int）。"""
    return int((datetime.now(UTC) - timedelta(seconds=seconds_ago)).timestamp() * 1000)


def _kimi_turn(text: str, seconds_ago: float = 60, kind: str = "user") -> dict:
    return {"type": "turn.prompt", "input": [{"type": "text", "text": text}],
            "origin": {"kind": kind}, "time": _ms(seconds_ago)}


def _kimi_loop(event: dict, seconds_ago: float = 60) -> dict:
    return {"type": "context.append_loop_event", "event": event, "time": _ms(seconds_ago)}


def _write_kimi_session(sessions_dir: Path, sid: str, rows: list[dict],
                        work_dir: str = "", with_state: bool = True,
                        wd: str = "wd_myproj_abc123def456") -> None:
    session_dir = sessions_dir / wd / sid
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(wire, rows)
    if with_state:
        (session_dir / "state.json").write_text(json.dumps({"workDir": work_dir}), encoding="utf-8")


def _scan_kimi(sessions_dir: Path, max_prompts: int | None = 3) -> list[LiveSession]:
    now = datetime.now(UTC)
    return sidebar._scan_kimi_sessions(now - timedelta(hours=5), now, None, max_prompts,
                                       sessions_dir=str(sessions_dir))


def test_kimi_scan_extracts_prompts_model_and_project(tmp_path):
    sessions_dir = tmp_path / "sessions"
    proj = tmp_path / "myproj"
    _write_kimi_session(sessions_dir, "session_s1", [
        _kimi_turn("第一条提示词", 120),
        _kimi_turn("goal 描述不是提示词", 110, kind="goal"),
        _kimi_turn('<cron-fire jobId="x"><prompt>定时任务</prompt></cron-fire>', 100),
        _kimi_turn("/skill:tt-sidebar", 90),
        {"type": "usage.record", "model": "kimi-code/k3",
         "usage": {"inputOther": 1, "output": 2}, "time": _ms(80)},
        _kimi_turn("第二条提示词", 70),
    ], work_dir=str(proj))
    # 子代理的 wire 不算主人的提示词来源
    sub_wire = (sessions_dir / "wd_myproj_abc123def456" / "session_s1"
                / "agents" / "agent-0" / "wire.jsonl")
    sub_wire.parent.mkdir(parents=True)
    _write_jsonl(sub_wire, [_kimi_turn("子代理的提示词", 60)])

    got = _scan_kimi(sessions_dir)
    assert len(got) == 1
    s = got[0]
    assert s.agent_id == "kimi"
    assert s.session_id == "session_s1"
    assert s.project == "myproj"
    assert s.model == "kimi-code/k3"
    assert [p.text for p in s.prompts] == ["第一条提示词", "第二条提示词"]
    assert s.state == WAITING  # 70s 前最后活动、无 pending → 等输入


def test_kimi_scan_pending_tool_call_marks_attention(tmp_path):
    sessions_dir = tmp_path / "sessions"
    rows = [
        _kimi_turn("跑个任务", 120),
        _kimi_loop({"type": "tool.call", "toolCallId": "c1", "name": "Bash", "args": {}}, 100),
    ]
    _write_kimi_session(sessions_dir, "session_s1", rows, work_dir="/tmp/myproj")
    assert _scan_kimi(sessions_dir)[0].state == ATTENTION

    rows.append(_kimi_loop({"type": "tool.result", "toolCallId": "c1", "result": {}}, 90))
    _write_kimi_session(sessions_dir, "session_s1", rows, work_dir="/tmp/myproj")
    assert _scan_kimi(sessions_dir)[0].state == WAITING


def test_kimi_scan_ask_user_question_becomes_hint(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_kimi_session(sessions_dir, "session_s1", [
        _kimi_turn("选个方案", 120),
        _kimi_loop({"type": "tool.call", "toolCallId": "q1", "name": "AskUserQuestion",
                    "args": {"questions": [{"question": "选哪个？",
                                            "options": [{"label": "甲"}, {"label": "乙"}]}]}}, 100),
    ], work_dir="/tmp/myproj")
    got = _scan_kimi(sessions_dir)
    assert got[0].next_hint == "选哪个？\n· 甲 / 乙"


def test_kimi_scan_skips_sessions_outside_window(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_kimi_session(sessions_dir, "session_old", [_kimi_turn("很早的提示词", 6 * 3600)],
                        work_dir="/tmp/myproj")
    assert _scan_kimi(sessions_dir) == []


def test_kimi_scan_truncates_prompts_and_falls_back_to_wd_name(tmp_path):
    sessions_dir = tmp_path / "sessions"
    _write_kimi_session(sessions_dir, "session_s2", [
        _kimi_turn("第一条", 120),
        _kimi_turn("第二条", 110),
    ], with_state=False)  # 无 state.json → 项目名回退 wd_<name>_<hash> 目录名
    got = _scan_kimi(sessions_dir, max_prompts=1)
    assert got[0].project == "myproj"
    assert [p.text for p in got[0].prompts] == ["第二条"]
