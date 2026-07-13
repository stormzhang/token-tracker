from token_tracker import cli
from token_tracker.adapters.types import DailyStats


def test_apply_sort_by_tokens_uses_authoritative_attr():
    stats = [
        DailyStats(date="2026-01-01", total_tokens=10),
        DailyStats(date="2026-01-02", total_tokens=30),
    ]
    cli._apply_sort(stats, "tokens", descending=True, default_attr="date", default_reverse=True)
    assert [s.total_tokens for s in stats] == [30, 10]


def test_apply_sort_time_falls_back_to_default_attr():
    stats = [
        DailyStats(date="2026-01-01", total_tokens=99),
        DailyStats(date="2026-01-03", total_tokens=1),
    ]
    # "time" 不在 SORT_ATTRS，应按 default_attr=date 排
    cli._apply_sort(stats, "time", descending=True, default_attr="date", default_reverse=True)
    assert [s.date for s in stats] == ["2026-01-03", "2026-01-01"]


def test_apply_sort_unknown_key_falls_back():
    stats = [
        DailyStats(date="2026-01-02", total_tokens=5),
        DailyStats(date="2026-01-01", total_tokens=99),
    ]
    cli._apply_sort(stats, "bogus", descending=True, default_attr="date", default_reverse=True)
    assert [s.date for s in stats] == ["2026-01-02", "2026-01-01"]


def test_extract_theme_arg():
    # --theme NAME 从任意位置提取并从 args 移除；未给则 None；缺值不崩
    assert cli._extract_theme_arg(["monthly", "--theme", "dracula"]) == (["monthly"], "dracula")
    assert cli._extract_theme_arg(["--theme", "nord", "weekly"]) == (["weekly"], "nord")
    assert cli._extract_theme_arg(["monthly"]) == (["monthly"], None)
    assert cli._extract_theme_arg(["--theme"]) == (["--theme"], None)  # 缺值：原样留，不消耗


def test_extract_agent_arg_maps_flags_to_ids():
    # --claude / --codex 从任意位置提取并移除；映射到 adapter id；未给 → None
    assert cli._extract_agent_arg(["daily", "--claude"]) == (["daily"], "claude-code")
    assert cli._extract_agent_arg(["--codex", "weekly"]) == (["weekly"], "codex")
    assert cli._extract_agent_arg(["monthly"]) == (["monthly"], None)
    # 重复相同 flag 幂等；不同 flag 混用退出（下一用例覆盖）
    assert cli._extract_agent_arg(["--claude", "--claude", "status"]) == (["status"], "claude-code")


def test_extract_agent_arg_conflict_exits(monkeypatch, capsys):
    # --claude 与 --codex 同时给 → 直接 sys.exit(1) + 中文/英文提示
    import pytest
    with pytest.raises(SystemExit) as e:
        cli._extract_agent_arg(["--claude", "--codex", "daily"])
    assert e.value.code == 1


def test_cmd_quota_set_writes_config_and_updates_hook(tmp_path, monkeypatch):
    from token_tracker import config

    calls: dict = {}
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "update_hook", lambda: calls.__setitem__("updated", True))

    cli.cmd_quota(["remaining"])

    assert config.quota_display_mode() == "remaining"
    assert calls == {"updated": True}


def test_cmd_quota_invalid_prints_usage(capsys):
    cli.cmd_quota(["bogus"])
    assert "quota" in capsys.readouterr().out


def test_cli_agent_flag_filters_agents(monkeypatch):
    # `tt daily --codex` 显式指定 → agents 收窄到 codex，会话内自动识别不再生效
    from types import SimpleNamespace

    from token_tracker import config
    captured: dict = {}

    def fake_load(agents_arg):
        captured["agents"] = [a.id for a in agents_arg]
        return []

    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr(config, "setup_version", lambda: config.SETUP_VERSION)
    monkeypatch.setattr(cli, "detect_agents", lambda: [
        SimpleNamespace(id="claude-code", name="Claude Code"),
        SimpleNamespace(id="codex", name="Codex"),
    ])
    monkeypatch.setattr(cli, "_load_per_agent", fake_load)
    monkeypatch.setattr(cli, "_aggregate_per_agent", lambda *a, **k: [])
    monkeypatch.setattr(cli, "render_daily_heatmap", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_current_session_agent", lambda: "claude-code")  # 假装在 CC 会话里
    monkeypatch.setattr("sys.argv", ["tt", "daily", "--codex"])
    cli.main()
    # 显式 --codex 覆盖了会话自动识别 → 只加载 codex（不是 CC）
    assert captured["agents"] == ["codex"]


def test_cli_agent_flag_missing_agent_exits(monkeypatch):
    # 显式 --codex 但环境里没装 Codex（只检测到 CC）→ sys.exit(1) + 友好错误
    from types import SimpleNamespace

    import pytest

    from token_tracker import config
    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr(config, "setup_version", lambda: config.SETUP_VERSION)
    monkeypatch.setattr(cli, "detect_agents",
                        lambda: [SimpleNamespace(id="claude-code", name="Claude Code")])
    monkeypatch.setattr("sys.argv", ["tt", "daily", "--codex"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1


def test_asc_without_sort_respected():
    # 回归：`tt daily --asc`（不带 --sort）此前被静默忽略；显式方向必须覆盖各命令默认方向。
    args, sort_key, descending = cli._parse_sort_args(["--asc"])
    assert (args, sort_key, descending) == ([], None, False)
    stats = [
        DailyStats(date="2026-01-01", total_tokens=30),
        DailyStats(date="2026-01-02", total_tokens=10),
    ]
    cli._apply_sort(stats, None, descending, default_attr="total_tokens", default_reverse=True)
    assert [s.total_tokens for s in stats] == [10, 30]  # --asc 生效（默认应是降序）
    # 没显式给方向（None）→ 仍走命令默认方向
    args2, key2, desc2 = cli._parse_sort_args([])
    assert desc2 is None
    cli._apply_sort(stats, None, desc2, default_attr="total_tokens", default_reverse=True)
    assert [s.total_tokens for s in stats] == [30, 10]


def test_current_session_agent_ignores_claude_config_dir(monkeypatch):
    # 回归：CLAUDE_CONFIG_DIR 是用户级配置变量（shell profile 长期 export 挪目录），
    # 不能当会话信号——否则独立终端被误判会话内（daily/weekly 被过滤、wizard 永不出现）。
    for var in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDECODE", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert cli._current_session_agent() is None
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/claude")  # 仅配置变量 → 仍是独立终端
    assert cli._current_session_agent() is None
    monkeypatch.setenv("CLAUDECODE", "1")                      # 真会话信号
    assert cli._current_session_agent() == "claude-code"
    monkeypatch.setenv("CODEX_THREAD_ID", "t1")                # Codex 信号优先级在前
    assert cli._current_session_agent() == "codex"
