import contextlib
from datetime import UTC, datetime, timedelta

from token_tracker import cli
from token_tracker.adapters.types import AgentInfo, RateLimits, SessionStats, StatusSummary, UsageEntry
from token_tracker.ui.console import capture_console
from token_tracker.ui.status import render_status


def _entry(agent, sid, model, ts, inp=100, out=50, msgs=1):
    return UsageEntry(
        timestamp=ts, session_id=sid, message_id=f"{sid}-{ts.isoformat()}", request_id="r",
        model=model, input_tokens=inp, output_tokens=out, cache_creation_tokens=0,
        cache_read_tokens=0, cost_usd=None, project="proj", agent_id=agent, message_count=msgs,
    )


def _session(sid, agent, model, start, dur, total, cost, msgs, active=None):
    return SessionStats(session_id=sid, project="proj", model=model, start_time=start,
                        end_time=start, duration_minutes=dur,
                        active_minutes=dur if active is None else active, total_tokens=total,
                        cost_usd=cost, message_count=msgs, agent_id=agent)


def test_build_status_data_merges_and_per_agent(monkeypatch):
    # 头图合并汇总 + per-agent 拆分 + session 合并带 agent_id 且按 cost 倒序（entries 给跨度，避免被 0min 过滤）。
    now = datetime.now(UTC)
    data_map = {
        # 用「现在前 1 小时内」避免 UTC 凌晨跑 CI 时跨日被 _build_status_data 今天过滤掉
        "claude-code": [  # s1 跨度 10min
            _entry("claude-code", "s1", "claude-opus-4-8", now - timedelta(minutes=30), out=500),
            _entry("claude-code", "s1", "claude-opus-4-8", now - timedelta(minutes=20), out=100),
        ],
        "codex": [  # s2 跨度 6min
            _entry("codex", "s2", "gpt-5", now - timedelta(minutes=40), out=20),
            _entry("codex", "s2", "gpt-5", now - timedelta(minutes=34), out=10),
        ],
    }
    monkeypatch.setattr(cli, "_load_entries", lambda aid, hours_back=0: data_map.get(aid, []))
    monkeypatch.setattr(cli, "RATE_LIMIT_LOADERS", {})  # 无额度
    agents = [AgentInfo("claude-code", "Claude Code"), AgentInfo("codex", "Codex")]

    data = cli._build_status_data(agents)
    assert data is not None
    assert data["summary"].session_count == 2          # 2 个有时长会话
    assert data["summary"].message_count == 4          # 4 条 entry
    assert set(data["per_agent"]) == {"claude-code", "codex"}
    assert data["per_agent"]["claude-code"].session_count == 1
    # session 合并、带 agent_id、按 cost 倒序（cc out 大 → cost 高 → 排前）
    assert data["sessions"][0].agent_id == "claude-code"
    assert {s.agent_id for s in data["sessions"]} == {"claude-code", "codex"}


def test_build_status_data_filters_short_sessions(monkeypatch):
    # 5min 以下的短会话（0min 单条 / 3min）从列表 + Sessions 计数中过滤掉。
    now = datetime.now(UTC)
    data_map = {"claude-code": [
        _entry("claude-code", "s1", "claude-opus-4-8", now - timedelta(minutes=30)),
        _entry("claude-code", "s1", "claude-opus-4-8", now - timedelta(minutes=22)),  # s1: 8min → 保留
        _entry("claude-code", "s2", "claude-opus-4-8", now - timedelta(minutes=10)),  # s2: 单条 0min → 过滤
        _entry("claude-code", "s3", "claude-opus-4-8", now - timedelta(minutes=18)),
        _entry("claude-code", "s3", "claude-opus-4-8", now - timedelta(minutes=15)),  # s3: 3min → 过滤（<5）
    ]}
    monkeypatch.setattr(cli, "_load_entries", lambda aid, hours_back=0: data_map.get(aid, []))
    monkeypatch.setattr(cli, "RATE_LIMIT_LOADERS", {})
    data = cli._build_status_data([AgentInfo("claude-code", "Claude Code")])
    assert {s.session_id for s in data["sessions"]} == {"s1"}
    assert data["summary"].session_count == 1


def test_build_status_data_empty(monkeypatch):
    monkeypatch.setattr(cli, "_load_entries", lambda aid, hours_back=0: [])
    monkeypatch.setattr(cli, "RATE_LIMIT_LOADERS", {})
    assert cli._build_status_data([AgentInfo("claude-code", "Claude Code")]) is None


def test_render_status_with_limits(monkeypatch):
    # 有订阅额度 → 额度段（weekly 样式）；session 强制 source 列。
    monkeypatch.setattr("token_tracker.ui.status.forced_color_console", contextlib.nullcontext)
    now = datetime.now(UTC)
    summary = StatusSummary(total_tokens=1000, cost_usd=1.5, message_count=5, session_count=2,
                            models={"claude-opus-4-8": 1000})
    sessions = [
        _session("s1", "claude-code", "claude-opus-4-8", now, 65, 500, 2.0, 3),
        _session("s2", "codex", "gpt-5", now - timedelta(hours=1), 20, 500, 0.5, 2),
    ]
    rl = {"claude-code": RateLimits(five_hour_pct=38.0, seven_day_pct=51.0,
                                    model="Opus 4.8 (1M context)")}
    per_agent = {"claude-code": StatusSummary(total_tokens=2_000_000, cost_usd=12.5)}

    with capture_console(160) as buf:
        render_status(summary, per_agent, rl, sessions, ["Claude Code", "Codex"])
    out = buf.getvalue()

    assert "Today" in out                      # 头图
    # 额度段：agent 头行 Tokens / Cost / Model + 5h/7d 进度条
    assert "Rate Limits" in out and "5h" in out and "7d" in out
    assert "Tokens:" in out and "2.0M" in out  # 当天 tokens
    assert "Cost:" in out and "$12" in out     # 当天 cost
    assert "Model:" in out and "Opus 4.8" in out  # model（去掉 (1M context) 后缀）
    assert "Claude" in out and "Codex" in out  # session Agent 列（短名 Claude / Codex）


def test_render_status_limits_can_show_remaining(monkeypatch):
    # quota_display=remaining 时，额度段展示剩余百分比（100 - used），并用 Remaining 标题避免误读。
    monkeypatch.setattr("token_tracker.ui.status.forced_color_console", contextlib.nullcontext)
    monkeypatch.setattr("token_tracker.ui.status.config.quota_display_mode", lambda: "remaining")
    now = datetime.now(UTC)
    summary = StatusSummary(total_tokens=1000, cost_usd=1.5, message_count=5, session_count=1)
    rl = {"claude-code": RateLimits(five_hour_pct=38.0, seven_day_pct=51.0, model="Opus 4.8")}
    per_agent = {"claude-code": StatusSummary(total_tokens=2_000_000, cost_usd=12.5)}

    with capture_console(160) as buf:
        render_status(summary, per_agent, rl, [_session("s1", "claude-code", "claude-opus-4-8", now, 10, 500, 2.0, 3)],
                      ["Claude Code"])
    out = buf.getvalue()

    assert "Rate Limits: Remaining" in out
    assert "62%" in out and "49%" in out
    assert "38%" not in out and "51%" not in out


def test_render_status_expired_reset_shows_dash(monkeypatch):
    # 久没用 → window 已滚但本次启动没拿到新 rate_limits（normalize_pct 把 pct 归零成 0.0、resets_at 保留旧值）。
    # 旧逻辑会显示「reset at 09:49」误导用户当成未来时间；新逻辑显示 `reset --` 让用户明白这是旧数据，
    # 并保留这行让订阅用户能识别自己是订阅套餐（避免误以为切了 API 计费）。
    monkeypatch.setattr("token_tracker.ui.status.forced_color_console", contextlib.nullcontext)
    now = datetime.now(UTC)
    summary = StatusSummary(total_tokens=0, cost_usd=0.0, session_count=0)
    past_5h = int((now - timedelta(hours=10)).timestamp())   # 5h window 早过期
    past_7d = int((now - timedelta(days=2)).timestamp())     # 7d window 也过期
    rl = {"claude-code": RateLimits(
        five_hour_pct=0.0, five_hour_resets_at=past_5h,
        seven_day_pct=0.0, seven_day_resets_at=past_7d,
        model="Opus 4.8",
    )}
    per_agent = {"claude-code": StatusSummary(total_tokens=0, cost_usd=0.0)}

    with capture_console(160) as buf:
        render_status(summary, per_agent, rl, [], ["Claude Code"])
    out = buf.getvalue()

    assert "Rate Limits" in out                # 整段保留（让订阅用户能识别）
    assert "5h" in out and "7d" in out         # 两行都在
    assert "reset --" in out                   # 过期时间不再误导
    assert "reset at " not in out              # 不显示具体钟点


def test_render_status_no_limits_shows_agent_stats(monkeypatch):
    # 都没订阅额度 → 中间换成 per-agent token/cost/sessions/messages 统计。
    monkeypatch.setattr("token_tracker.ui.status.forced_color_console", contextlib.nullcontext)
    now = datetime.now(UTC)
    summary = StatusSummary(total_tokens=1000, cost_usd=1.5, message_count=5, session_count=1)
    per_agent = {
        "claude-code": StatusSummary(total_tokens=800, cost_usd=1.0, message_count=3, session_count=1),
        "codex": StatusSummary(total_tokens=200, cost_usd=0.5, message_count=2, session_count=1),
    }
    sessions = [_session("s1", "claude-code", "claude-opus-4-8", now, 10, 800, 1.0, 3)]

    with capture_console(160) as buf:
        render_status(summary, per_agent, {}, sessions, ["Claude Code", "Codex"])
    out = buf.getvalue()

    assert "Today by Agent" in out             # 无额度 → per-agent 统计段
    assert "Claude Code" in out and "Codex" in out
    assert "Rate Limits" not in out            # 不显示额度段


def test_system_tz_falls_back_on_errors(monkeypatch):
    # 容错：读不到 /etc/localtime（Windows）/ 软链接指向无效时区名 → 返回 None（回退进程时区，不崩）。
    from token_tracker import tz as tzmod

    def _raise(*a):
        raise OSError("no such file")

    monkeypatch.setattr(tzmod.os, "readlink", _raise)
    assert tzmod.system_tz() is None                                     # readlink 失败
    monkeypatch.setattr(tzmod.os, "readlink", lambda p: "/usr/share/zoneinfo/Not/A/Real/Zone")
    assert tzmod.system_tz() is None                                     # 无效时区名 → ZoneInfoNotFoundError
