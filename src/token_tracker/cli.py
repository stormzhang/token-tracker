import os
import sys
from datetime import datetime

from rich.text import Text

from . import config, i18n
from .adapters import claude, codex
from .adapters.rate_limits import load_rate_limits as load_claude_rate_limits
from .adapters.registry import detect_agents
from .adapters.types import StatusSummary
from .analyzer.aggregator import (
    add_token_fields,
    aggregate_daily,
    aggregate_monthly,
    aggregate_sessions,
    aggregate_weekly,
)
from .analyzer.cost import calculate_cost
from .hooks import needs_update, setup, unsetup, update_hook
from .i18n import t
from .ui import theme, themes
from .ui.console import forced_color_console, get_console
from .ui.format import system_tz
from .ui.heatmap import render_daily_heatmap
from .ui.status import render_sessions_view, render_status
from .ui.tables import (
    render_monthly,
    render_weekly,
)

AGENT_LOADERS = {"claude-code": claude, "codex": codex}
RATE_LIMIT_LOADERS = {"claude-code": load_claude_rate_limits, "codex": codex.load_rate_limits}

# 排序字段 → stats 属性名（单一权威表）。
# "time" 的属性因命令而异（daily=date / weekly=week / sessions=start_time），不在此表，走 default_attr。
SORT_ATTRS = {
    "tokens": "total_tokens",
    "cost": "cost_usd",
    "messages": "message_count",
    "sessions": "session_count",
    "input": "input_tokens",
    "output": "output_tokens",
}
VALID_SORT_KEYS = (*SORT_ATTRS.keys(), "time")


# 数据报表命令分发表：命令 → (聚合函数, 渲染函数, time 排序的属性, 无 --sort 时的默认属性, 默认降序)
# time_attr 与 no_sort_attr 仅 daily 不同（默认按 token 排，--sort time 才按日期）
_REPORT_COMMANDS = {
    "daily": (aggregate_daily, render_daily_heatmap, "date", "total_tokens", True),
    "weekly": (aggregate_weekly, render_weekly, "week", "week", True),
    "monthly": (aggregate_monthly, render_monthly, "month", "month", False),
    # sessions 走专门分支调 render_sessions_view（顶部+底部仿 status、无额度段）；render_fn 槽
    # 用 render_monthly 占位（仅为保持表项 callable、不经通用 render_fn 调用）；默认按 cost 倒序
    "sessions": (aggregate_sessions, render_monthly, "start_time", "cost_usd", True),
}


def _parse_limit(args: list[str], default: int) -> int:
    for a in args:
        try:
            return int(a)
        except ValueError:
            pass
    return default


def _extract_theme_arg(args: list[str]) -> tuple[list[str], str | None]:
    """提取 --theme NAME，返回 (剩余 args, theme_name)；未给则 name=None。用于报表临时切主题、不落配置。"""
    remaining: list[str] = []
    name = None
    i = 0
    while i < len(args):
        if args[i] == "--theme" and i + 1 < len(args):
            name = args[i + 1].lower()
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return remaining, name


def _parse_sort_args(args: list[str]) -> tuple[list[str], str | None, bool]:
    """Extract --sort KEY and --asc from args, return (remaining, sort_key, descending)."""
    remaining = []
    sort_key = None
    descending = True
    i = 0
    while i < len(args):
        if args[i] == "--sort" and i + 1 < len(args):
            sort_key = args[i + 1].lower()
            i += 2
        elif args[i] == "--asc":
            descending = False
            i += 1
        elif args[i] == "--desc":
            descending = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1
    return remaining, sort_key, descending


def _apply_sort(stats, sort_key: str | None, descending: bool, default_attr: str, default_reverse: bool):
    if sort_key is None:
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=default_reverse)
        return
    if sort_key not in VALID_SORT_KEYS:
        valid = ", ".join(VALID_SORT_KEYS)
        get_console().print(f"[yellow]{t('unknown_sort_field', key=sort_key, valid=valid)}[/yellow]")
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=default_reverse)
        return
    # "time" 不在 SORT_ATTRS → 退回 default_attr（各命令的时间字段）
    attr = SORT_ATTRS.get(sort_key, default_attr)
    stats.sort(key=lambda s: getattr(s, attr), reverse=descending)


def _load_entries(agent_id: str, hours_back: int = 0):
    loader = AGENT_LOADERS.get(agent_id)
    return loader.load_entries(hours_back=hours_back) if loader else []


def _aggregate_per_agent(agents, agg_fn):
    stats = []
    for a in agents:
        entries = _load_entries(a.id)
        for s in agg_fn(entries):
            s.agent_id = a.id
            stats.append(s)
    return stats


def _build_status_data(agents) -> dict | None:
    """当天 status 数据（系统时区今天 00:00 起）：合并汇总 + per-agent 汇总 + 分 agent 额度
    + 合并 session（按 cost 倒序）。session 列表过滤掉 5min 以下的短会话。
    """
    tz = system_tz()
    today = datetime.now(tz).date()
    summary = StatusSummary()
    per_agent: dict = {}
    sessions = []
    rate_limits: dict = {}
    for a in agents:
        # 当天 entries：load 过去 25h（覆盖当天最长 + 时区缓冲）后按系统时区今天过滤
        entries = [e for e in _load_entries(a.id, hours_back=25)
                   if e.timestamp.astimezone(tz).date() == today]
        a_sum = StatusSummary()
        for e in entries:
            cost = calculate_cost(e)
            add_token_fields(summary, e, cost)
            add_token_fields(a_sum, e, cost)
            summary.message_count += e.message_count
            a_sum.message_count += e.message_count
            summary.models[e.model] = summary.models.get(e.model, 0) + e.total_tokens
        a_sessions = [s for s in aggregate_sessions(entries) if s.active_minutes >= 5]
        for s in a_sessions:
            s.agent_id = a.id
        a_sum.session_count = len(a_sessions)
        per_agent[a.id] = a_sum
        sessions.extend(a_sessions)
        rl = RATE_LIMIT_LOADERS.get(a.id, lambda: None)()
        if rl and (rl.five_hour_pct is not None or rl.seven_day_pct is not None):
            rate_limits[a.id] = rl
    if not sessions and summary.total_tokens == 0:
        return None
    summary.session_count = len(sessions)
    sessions.sort(key=lambda s: s.cost_usd, reverse=True)
    return dict(summary=summary, per_agent=per_agent, rate_limits=rate_limits,
                sessions=sessions, agents=[a.name for a in agents])


def _summary_from_sessions(sessions) -> StatusSummary:
    """tt sessions 顶部汇总：以展示出的 session 为口径累加（非全量、非时间窗）。"""
    sm = StatusSummary()
    for s in sessions:
        sm.input_tokens += s.input_tokens
        sm.output_tokens += s.output_tokens
        sm.cache_creation_tokens += s.cache_creation_tokens
        sm.cache_read_tokens += s.cache_read_tokens
        sm.total_tokens += s.total_tokens
        sm.cost_usd += s.cost_usd
        sm.message_count += s.message_count
        sm.models[s.model] = sm.models.get(s.model, 0) + s.total_tokens
    sm.session_count = len(sessions)
    return sm


def _current_session_agent() -> str | None:
    """识别当前所在的 agent 会话（靠环境变量）：Codex / Claude Code；独立终端返回 None。"""
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    if os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDECODE"):
        return "claude-code"
    return None


def _get_version() -> str:
    from importlib.metadata import version
    return version("token-tracker")


def _load_local_mock() -> None:
    """`--mock`：加载本地 `mock/run.py` 演示数据（monkeypatch 数据源 + 概览渲染）。
    `mock/` 在 .gitignore、不随包发布；发布环境找不到就友好提示并退出（普通用户用不到）。"""
    import importlib.util
    run_py = os.path.join(os.path.dirname(__file__), "..", "..", "mock", "run.py")
    if not os.path.isfile(run_py):
        get_console().print("[yellow]--mock is for local dev only (mock/ not found)[/yellow]")
        sys.exit(0)
    sys.path.insert(0, os.path.dirname(run_py))  # 让 run.py 的 `import mockdata` 生效
    spec = importlib.util.spec_from_file_location("_tt_mock_run", run_py)
    assert spec and spec.loader  # run_py 上面已 isfile 校验，spec/loader 必非 None
    spec.loader.exec_module(importlib.util.module_from_spec(spec))  # 模块级即完成 patch


# --- 首次运行交互向导判定 ---

def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _should_run_wizard() -> bool:
    """是否进交互向导：必须双 tty 且不在 AI 会话内（否则走 _auto_setup 非交互初始化）。"""
    return _is_tty() and not _current_session_agent()


def _run_setup_flow() -> None:
    """配置流程**单一入口**：先确认装了至少一个 agent（detect_agents 守卫只此一处），
    再按环境分流——双 tty 非会话内进交互向导，否则非交互默认纯报表模式。"""
    if not detect_agents():
        get_console().print(f"[red]{t('no_agent_install')}[/red]")
        return
    if _should_run_wizard():
        from .wizard import run_wizard
        run_wizard()  # wizard 欢迎行下会显示检测到的 agent
    else:
        _auto_setup()


def _auto_setup() -> None:
    """非交互环境（非 tty / CI / 会话内）：默认只初始化配置——语言跟随**系统设置**（绕过 CLI LANG）、
    主题 mocha、状态栏组件全关。仅当用户从未配置过语言/主题时落默认（不覆盖已有选择）。
    agent 守卫在 _run_setup_flow 已做，这里假设至少有一个 agent。"""
    if config.resolve_lang() is None:
        sys_lang = i18n._detect_system_lang()
        config.save_lang(sys_lang)
        i18n.set_lang(sys_lang)
    if not config.load_config().get("theme"):
        config.save_theme("mocha")
    setup(auto=True)  # 组件默认全关，避免非交互安装接管用户状态栏
    get_console().print(f"[dim]{t('auto_setup_hint')}[/dim]")


# --- theme 命令 ---

def _theme_source() -> str:
    if os.environ.get("TT_THEME", "").strip():
        return t("theme_src_env")
    if config.load_config().get("theme"):
        return t("theme_src_config")
    return t("theme_src_auto")


def _theme_show() -> None:
    get_console().print(t("theme_current", name=config.resolve_theme(), src=_theme_source()))


def _theme_list() -> None:
    current = config.resolve_theme()
    slots = ("green", "yellow", "peach", "red", "blue", "sapphire", "mauve", "pink")
    with forced_color_console():
        console = get_console()
        for name in themes.THEME_NAMES:
            base = themes.get_theme(name)["base"]
            marker = "●" if name == current else " "
            line = Text(f"  {marker} {name:<11}", style="bold" if name == current else "")
            for slot in slots:
                line.append("■ ", style=base[slot])
            console.print(line)


def _render_theme_sample(name: str) -> None:
    """渲染某主题配色示例（CLI 语义色 + 进度条 + 热力阶 + statusline 行），preview 与向导复用。"""
    with forced_color_console(), theme.preview_theme(name):
        console = get_console()
        text = Text("  ")
        text.append("Tokens 1.2M  ", style=theme._S.token)
        text.append("Cost $3.45  ", style=theme._S.cost)
        text.append("good  ", style=theme._S.good)
        text.append("warn  ", style=theme._S.warn)
        text.append("bad", style=theme._S.bad)
        console.print(text)
        bar = Text("  ")
        bar.append("██████", style=theme._S.bar_low)
        bar.append("█████", style=theme._S.bar_mid)
        bar.append("███", style=theme._S.bar_high)
        bar.append("  80%", style=theme._S.dim)
        console.print(bar)
        heat = Text("  ")
        for c in theme.heat_greens():
            heat.append("■ ", style=c)
        console.print(heat)
    sl = themes.theme_to_statusline_ansi(name)
    print(
        f"  {sl['project']}[project]{sl['reset']}({sl['branch']}main{sl['reset']})  "
        f"{sl['bar_ok']}██░{sl['reset']}  {sl['tokens']}Tokens 1.2M{sl['reset']}  "
        f"{sl['model']}Opus 4.8{sl['reset']}"
    )


def _theme_set(name: str) -> None:
    console = get_console()
    if name not in themes.THEMES:
        console.print(f"[red]{t('theme_unknown', name=name)}[/red]")
        console.print(f"[dim]{t('theme_options', names=', '.join(themes.THEME_NAMES))}[/dim]")
        sys.exit(1)
    config.save_theme(name)
    theme.set_active_theme(name)
    console.print(t("theme_set_ok", name=name))
    if update_hook():
        console.print(f"[dim]{t('theme_set_statusline')}[/dim]")
    if config.resolve_theme() != name:
        console.print(f"[yellow]{t('theme_env_override')}[/yellow]")


def _theme_preview(name: str) -> None:
    console = get_console()
    if name not in themes.THEMES:
        console.print(f"[red]{t('theme_unknown', name=name)}[/red]")
        console.print(f"[dim]{t('theme_options', names=', '.join(themes.THEME_NAMES))}[/dim]")
        sys.exit(1)
    console.print(f"[bold]{name}[/bold]")
    _render_theme_sample(name)


def cmd_theme(args: list[str]) -> None:
    sub = args[0] if args else "show"
    if sub == "show":
        _theme_show()
    elif sub == "list":
        _theme_list()
    elif sub == "set" and len(args) >= 2:
        _theme_set(args[1])
    elif sub == "preview" and len(args) >= 2:
        _theme_preview(args[1])
    elif sub in themes.THEMES:
        _theme_set(sub)
    else:
        get_console().print(f"[dim]{t('theme_usage')}[/dim]")


def main():
    # --mock：本地开发演示，加载 mock/ 假数据再走正常报表流程（mock/ 在 .gitignore）
    if "--mock" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mock"]
        _load_local_mock()

    args = sys.argv[1:]
    # --theme NAME：临时覆盖主题（仅本次进程、不落配置/不重烘焙状态栏），对所有报表 + status 生效
    args, theme_override = _extract_theme_arg(args)
    if theme_override is not None:
        if theme_override not in themes.THEMES:
            get_console().print(f"[red]{t('theme_unknown', name=theme_override)}[/red]")
            get_console().print(f"[dim]{t('theme_options', names=', '.join(themes.THEME_NAMES))}[/dim]")
            sys.exit(1)
        theme.set_active_theme(theme_override)
    command = args[0] if args else "daily"

    # 版本查询不该触发任何文件读写，放在 auto-update 之前短路返回
    if command in ("--version", "-v", "-V"):
        print(f"token-tracker {_get_version()}")
        print("by stormzhang · https://github.com/stormzhang/token-tracker")
        return

    if command == "theme":
        cmd_theme(args[1:])
        return

    # 任意命令都可顺带同步已接管的状态栏脚本；未接管时 needs_update() 不会主动写配置。
    if command not in ("setup", "unsetup") and needs_update():
        update_hook()

    if command == "setup":
        _run_setup_flow()
        return
    if command == "unsetup":
        unsetup()
        return

    if command not in ("status", "dashboard") and command not in _REPORT_COMMANDS:
        get_console().print(f"[red]{t('unknown_cmd', cmd=command)}[/red]")
        get_console().print(f"[dim]{t('available_cmds')}[/dim]")
        sys.exit(1)

    agents = detect_agents()
    if not agents:
        get_console().print(f"[red]{t('no_agent_install')}[/red]")
        sys.exit(1)
    agent_ids = {a.id for a in agents}

    if command in ("status", "dashboard"):
        data = _build_status_data(agents)
        if not data:
            get_console().print(f"[yellow]{t('no_token_data')}[/yellow]")
            return
        render_status(**data)
        return

    rest_args, sort_key, sort_desc = _parse_sort_args(args[1:])

    # daily / weekly 跟随当前会话：CC 会话只看 CC、Codex 会话只看 Codex；
    # 独立终端（识别不到会话）保持合并所有 agent。
    report_agents = agents
    if command in ("daily", "weekly"):
        session_agent = _current_session_agent()
        if session_agent and session_agent in agent_ids:
            report_agents = [a for a in agents if a.id == session_agent]
    agent_names = [a.name for a in report_agents]

    agg_fn, render_fn, time_attr, no_sort_attr, default_reverse = _REPORT_COMMANDS[command]
    stats = _aggregate_per_agent(report_agents, agg_fn)
    default_attr = time_attr if sort_key == "time" else no_sort_attr

    if command == "sessions":
        # sessions 看「最近的会话」：先过滤掉跨度 <5min 的碎片会话，再按时间取最近 N 条
        #（否则史上高 cost 会话恒久霸榜、新会话和低成本 agent 永远进不了榜），这 N 条再按 cost（或 --sort）展示
        kept = [s for s in stats if s.duration_minutes >= 5]
        kept.sort(key=lambda s: s.start_time, reverse=True)
        shown = kept[:_parse_limit(rest_args, default=20)]
        _apply_sort(shown, sort_key, sort_desc, default_attr, default_reverse)
        render_sessions_view(_summary_from_sessions(shown), shown, agent_names)
        return

    _apply_sort(stats, sort_key, sort_desc, default_attr, default_reverse)
    if command == "daily":
        # daily 顶部三卡片：Last 12 months + This Month + This Week，跟 weekly/monthly 同款样式、
        # 复用 _render_month_summary / _render_week_summary。多算两份 weekly/monthly 聚合传进去。
        render_fn(stats, agents=agent_names,  # type: ignore[operator]
                  weekly=_aggregate_per_agent(report_agents, aggregate_weekly),
                  monthly=_aggregate_per_agent(report_agents, aggregate_monthly))
    elif command == "weekly":
        render_weekly(stats, agents=agent_names, daily=_aggregate_per_agent(report_agents, aggregate_daily))
    elif command == "monthly":
        render_monthly(stats, agents=agent_names,
                       daily=_aggregate_per_agent(report_agents, aggregate_daily),
                       weekly=_aggregate_per_agent(report_agents, aggregate_weekly))
    else:
        render_fn(stats, agents=agent_names)


if __name__ == "__main__":
    main()
