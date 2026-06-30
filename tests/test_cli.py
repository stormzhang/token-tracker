from token_tracker import cli
from token_tracker.adapters.types import DailyStats


def test_status_command_does_not_require_setup(monkeypatch):
    from types import SimpleNamespace

    calls: dict = {}
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr(cli, "_run_setup_flow", lambda: calls.__setitem__("setup", True))
    monkeypatch.setattr(cli, "detect_agents", lambda: [SimpleNamespace(id="claude-code", name="Claude Code")])
    monkeypatch.setattr(cli, "_build_status_data", lambda _agents: {"payload": True})
    monkeypatch.setattr(cli, "render_status", lambda **_kwargs: calls.__setitem__("render", True))
    monkeypatch.setattr("sys.argv", ["tt", "status"])

    cli.main()

    assert calls == {"render": True}


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
