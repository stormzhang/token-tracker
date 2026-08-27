"""Pi adapter：sessions/<slug>/<ts>_<uuid>.jsonl 的 assistant message → UsageEntry。

目录布局照真实数据：`<sessions>/<项目目录slug>/<UTC时间戳>_<uuid>.jsonl`，
首行 `{"type":"session","id":...,"cwd":...}` 给会话 id 与项目归属。
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_tracker.adapters import pi


def _iso(seconds_ago: float = 60) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _header(sid: str, cwd: str = "") -> dict:
    return {"type": "session", "version": 3, "id": sid, "timestamp": _iso(3600), "cwd": cwd}


def _assistant(seconds_ago: float = 60, msg_id: str = "aa000001",
               provider: str = "deepseek-com", model: str = "deepseek-v4-flash",
               cost_total: float | None = 0.0, **usage: int) -> dict:
    u = {**usage}
    if cost_total is not None:
        u["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost_total}
    return {
        "type": "message", "id": msg_id, "parentId": None, "timestamp": _iso(seconds_ago),
        "message": {"role": "assistant", "provider": provider, "model": model, "usage": u},
    }


def _write_session(sessions_dir: Path, sid: str, rows: list[dict], cwd: str = "",
                   slug: str = "--home-downey-proj--") -> Path:
    session_file = sessions_dir / slug / f"2026-08-27T03-04-13-688Z_{sid}.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    with open(session_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(_header(sid, cwd)) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return session_file


def _load(sessions_dir: Path, hours_back: int = 0, monkeypatch=None) -> list:
    if monkeypatch is not None:
        monkeypatch.setattr(pi, "SESSIONS_DIR", str(sessions_dir))
    return pi.load_entries(hours_back)


def test_detect_requires_sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "SESSIONS_DIR", str(tmp_path / "missing"))
    assert pi.detect() is None
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(pi, "SESSIONS_DIR", str(sessions_dir))
    info = pi.detect()
    assert info is not None and info.id == "pi" and info.name == "Pi"


def test_assistant_messages_become_entries(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()  # project_from_cwd 取 git 根目录名
    _write_session(sessions_dir, "s1", [
        {"type": "model_change", "timestamp": _iso(130), "provider": "anthropic", "modelId": "claude-opus-4-8"},
        {"type": "message", "id": "u1", "timestamp": _iso(125),
         "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},  # 非 assistant 忽略
        _assistant(120, msg_id="m1", input=13931, output=470, cacheRead=0, cacheWrite=0,
                   reasoning=392, totalTokens=14401, cost_total=0.0),
        _assistant(60, msg_id="m2", input=530, output=175, cacheRead=23552, cacheWrite=10,
                   cost_total=0.0123),
    ], cwd=str(proj))

    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 2
    first, second = entries  # 按时间正序
    assert first.agent_id == "pi"
    assert first.session_id == "s1"
    assert first.model == "deepseek-com/deepseek-v4-flash"
    assert first.project == "proj"
    assert first.input_tokens == 13931
    assert first.output_tokens == 470  # reasoning 已含在 output 内，不另计
    assert first.cache_read_tokens == 0
    assert first.cost_usd == 0.0  # pi 已计价（无定价 provider 全 0），不回退定价表
    assert first.dedup_key != second.dedup_key
    assert second.cache_read_tokens == 23552
    assert second.cache_creation_tokens == 10
    assert second.cost_usd == 0.0123


def test_missing_cost_falls_back_to_pricing_table(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "s1", [
        _assistant(60, msg_id="m1", input=100, output=1, cost_total=None),  # 无 cost 字段
    ])
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 1
    assert entries[0].cost_usd is None  # 走 calculate_cost 定价表


def test_model_without_provider_and_unknown(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    row = _assistant(60, msg_id="m1", input=100, output=1)
    row["message"]["provider"] = ""  # 缺 provider → 裸 modelId
    row2 = _assistant(50, msg_id="m2", input=100, output=1)
    del row2["message"]["model"]  # 缺 model → unknown
    _write_session(sessions_dir, "s1", [row, row2])
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert [e.model for e in entries] == ["deepseek-v4-flash", "unknown"]


def test_zero_usage_messages_skipped(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "s1", [
        _assistant(60, msg_id="m1", input=0, output=0, cacheRead=0, cacheWrite=0),
        _assistant(50, msg_id="m2"),  # 字段缺失同样按 0 丢弃
        _assistant(40, msg_id="m3", input=1),
    ])
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 1
    assert entries[0].input_tokens == 1


def test_hours_back_cutoff_filters_old_messages(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "s1", [
        _assistant(3600 * 10, msg_id="m1", input=100, output=1),  # 10 小时前
        _assistant(60, msg_id="m2", input=200, output=1),
    ])
    entries = _load(sessions_dir, hours_back=1, monkeypatch=monkeypatch)
    assert len(entries) == 1
    assert entries[0].input_tokens == 200


def test_malformed_rows_ignored(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    session_file = _write_session(sessions_dir, "s1", [
        _assistant(60, msg_id="m1", input=100, output=1),
    ])
    # 坏行 / 缺 usage / 坏时间戳 / 缺 message 不崩、跳过
    with open(session_file, "a", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"type": "message", "id": "m2", "timestamp": _iso(45),
                            "message": {"role": "assistant"}}) + "\n")
        f.write(json.dumps({"type": "message", "id": "m3", "timestamp": "bad",
                            "message": {"role": "assistant", "usage": {"input": 5}}}) + "\n")
        f.write(json.dumps(["not-a-dict"]) + "\n")
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 1
    assert entries[0].input_tokens == 100


def test_project_unknown_without_cwd(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    session_file = _write_session(sessions_dir, "s1", [_assistant(60, msg_id="m1", input=100, output=1)])
    # 无 cwd 的 session 头 → 项目 unknown
    lines = session_file.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps({"type": "session", "version": 3, "id": "s1"})
    session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 1
    assert entries[0].project == "unknown"


def test_message_id_dedup_across_sessions(tmp_path, monkeypatch):
    # 消息 id 仅会话内唯一（8 位 hex），两个会话用同一 msg id 也必须各算一条
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "s1", [_assistant(60, msg_id="m1", input=100, output=1)])
    _write_session(sessions_dir, "s2", [_assistant(60, msg_id="m1", input=200, output=1)])
    entries = _load(sessions_dir, monkeypatch=monkeypatch)
    assert len(entries) == 2
    assert {e.session_id for e in entries} == {"s1", "s2"}


def test_current_session_id_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "SESSIONS_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("PI_SESSION_ID", "env-session-id")
    assert pi.current_session_id_for_cwd() == "env-session-id"


def test_current_session_id_for_cwd_picks_latest_matching(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(pi, "SESSIONS_DIR", str(sessions_dir))
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    old = _write_session(sessions_dir, "s_old", [], cwd=str(proj))
    new = _write_session(sessions_dir, "s_new", [], cwd=str(proj))
    _write_session(sessions_dir, "s_elsewhere", [], cwd=str(tmp_path / "other"))
    # mtime 即「最近活动时间」：显式控制新旧
    os.utime(old, (1000000000, 1000000000))
    os.utime(new, (1700000000, 1700000000))

    assert pi.current_session_id_for_cwd() == "s_new"


def test_current_session_id_for_cwd_freshness_gate(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(pi, "SESSIONS_DIR", str(sessions_dir))
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    stale = _write_session(sessions_dir, "s_stale", [], cwd=str(proj))
    os.utime(stale, (1000000000, 1000000000))  # 2020 年的 mtime

    # 不限新鲜度能命中；限 30 分钟则被门控掉（cli 会话内收窄防常驻误判）
    assert pi.current_session_id_for_cwd() == "s_stale"
    assert pi.current_session_id_for_cwd(fresh_within_s=30 * 60) == ""
