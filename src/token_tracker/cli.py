import os
import sys
from collections.abc import Callable
from datetime import datetime

from rich.text import Text

from . import config, i18n
from .adapters import claude, codex, kimi, pi
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
from .hooks import is_setup, needs_update, setup, unsetup, update_hook
from .i18n import t
from .sidebar import registry_update_hint, scan_sessions
from .tz import system_tz
from .ui import theme, themes
from .ui.console import forced_color_console, get_console
from .ui.heatmap import render_daily_heatmap
from .ui.sidebar import render_sidebar
from .ui.status import render_sessions_view, render_status
from .ui.tables import (
    render_monthly,
    render_weekly,
)

AGENT_LOADERS = {"claude-code": claude, "codex": codex, "kimi": kimi, "pi": pi}


def _load_codex_rate_limits():
    """CLI 面板的 Codex 额度：按最近活跃会话的 provider 过滤，
    多账号 / 多 model_provider（如 codex --profile deepseek）混跑时不显示错账号配额。"""
    return codex.load_rate_limits(provider=codex.latest_session_provider() or None)


RATE_LIMIT_LOADERS = {"claude-code": load_claude_rate_limits, "codex": _load_codex_rate_limits}

# agent 过滤 flag → agent_id（issue #19；_extract_agent_arg 解析、_select_agents 反查共用）
_FLAG_TO_ID = {"--claude": "claude-code", "--codex": "codex", "--kimi": "kimi", "--pi": "pi"}

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
_REPORT_COMMANDS: dict[str, tuple[Callable, Callable | None, str, str, bool]] = {
    "daily": (aggregate_daily, render_daily_heatmap, "date", "total_tokens", True),
    "weekly": (aggregate_weekly, render_weekly, "week", "week", True),
    "monthly": (aggregate_monthly, render_monthly, "month", "month", False),
    # sessions 走专门分支调 render_sessions_view（顶部+底部仿 status、无额度段），
    # 不经通用 render_fn（None 占位）；默认按 cost 倒序
    "sessions": (aggregate_sessions, None, "start_time", "cost_usd", True),
}


def _parse_limit(args: list[str], default: int) -> int:
    if not args:
        return default
    try:
        value = int(args[0])
    except ValueError as exc:
        raise ValueError(args[0]) from exc
    if value <= 0:
        raise ValueError(args[0])
    return value


def _extract_agent_arg(args: list[str]) -> tuple[list[str], str | None]:
    """提取 `--claude` / `--codex` / `--kimi` / `--pi`，返回 (剩余 args, agent_id | None)。互斥（同时给报错退出）；
    未给返回 None（走默认合并 + 会话自动识别）。用于多 agent 环境按需只看一个 agent（issue #19）。"""
    remaining: list[str] = []
    agent_id: str | None = None
    for a in args:
        target = _FLAG_TO_ID.get(a)
        if target is None:
            remaining.append(a)
            continue
        if agent_id is not None and agent_id != target:
            get_console().print(f"[red]{t('agent_filter_conflict')}[/red]")
            sys.exit(1)
        agent_id = target
    return remaining, agent_id


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


def _parse_sort_args(args: list[str]) -> tuple[list[str], str | None, bool | None]:
    """Extract --sort KEY and --asc/--desc from args, return (remaining, sort_key, descending).
    descending=None 表示用户没显式给方向（让 _apply_sort 落各命令默认方向）。"""
    remaining = []
    sort_key = None
    descending: bool | None = None
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


def _apply_sort(stats, sort_key: str | None, descending: bool | None,
                default_attr: str, default_reverse: bool):
    # 用户显式给了 --asc/--desc 就尊重（即便没配 --sort，如 `tt sessions --asc`）；
    # 没给方向时：带 --sort 默认降序、无 --sort 落各命令默认方向
    if sort_key is None:
        reverse = default_reverse if descending is None else descending
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=reverse)
        return
    if sort_key not in VALID_SORT_KEYS:
        valid = ", ".join(VALID_SORT_KEYS)
        get_console().print(f"[yellow]{t('unknown_sort_field', key=sort_key, valid=valid)}[/yellow]")
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=default_reverse)
        return
    # "time" 不在 SORT_ATTRS → 退回 default_attr（各命令的时间字段）
    attr = SORT_ATTRS.get(sort_key, default_attr)
    stats.sort(key=lambda s: getattr(s, attr), reverse=True if descending is None else descending)


def _load_entries(agent_id: str, hours_back: int = 0):
    loader = AGENT_LOADERS.get(agent_id)
    return loader.load_entries(hours_back=hours_back) if loader else []


def _load_per_agent(agents) -> list[tuple]:
    """(agent, 全量 entries) 列表：每个 agent 只从磁盘解析一次 JSONL（全量扫描很重），
    daily/weekly/monthly 多种聚合复用同一份——避免 tt daily 这类命令重复扫 2~3 遍。"""
    return [(a, _load_entries(a.id)) for a in agents]


def _aggregate_per_agent(loaded, agg_fn):
    stats = []
    for a, entries in loaded:
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


def _cmd_sidebar(agents, args: list[str]) -> None:
    """常驻侧边栏（窄窗格用）：活跃会话 + 各自最近提示词。live 模式跑 Textual app
    （备用屏 + 滚动 + 5s 定时刷新，q / Ctrl+C 退出）；`--once` 或非 tty 打一帧
    Rich 快照即退（脚本 / `!tt sidebar` / 测试用）。只读 transcript 与心跳文件，
    不写任何产物；不跟随会话收窄 agent——侧边栏本职是「总览所有会话」，
    显式 --claude / --codex / --kimi / --pi 才过滤。"""
    agent_ids = {a.id for a in agents}
    if "--once" in args or not sys.stdout.isatty():
        sessions = scan_sessions(agent_ids=agent_ids)
        with forced_color_console():
            get_console().print(render_sidebar(sessions, update_hint=registry_update_hint(sessions)))
        return
    from .ui.sidebar_app import SidebarApp  # 延迟 import：textual 仅 live 模式加载
    SidebarApp(agent_ids=agent_ids).run()


def _current_session_agent() -> str | None:
    """识别当前所在的 agent 会话：Codex / Claude Code 靠**会话内才有**的环境变量；Kimi 无环境变量，
    回退「state.json cwd/workDir == 当前 cwd 且 updatedAt 30 分钟内」的目录探测（新鲜度门控避免把
    「项目目录里曾有过 kimi 会话」的独立终端误判成会话内）；独立终端返回 None。
    不能用 CLAUDE_CONFIG_DIR 判断——那是用户级配置变量（可长期 export 在 shell profile 里挪配置目录，
    tt 自己也支持它），拿它当会话信号会让独立终端被误判成会话内（报表被过滤、首次运行进不了 wizard）。"""
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    if os.environ.get("CLAUDECODE"):
        return "claude-code"
    if os.environ.get("PI_CODING_AGENT"):  # pi 进程标记（child processes 继承，同 CLAUDECODE 语义）
        return "pi"
    if kimi.current_session_id_for_cwd(fresh_within_s=30 * 60):
        return "kimi"
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

def _in_agent_session_env() -> bool:
    """精确的会话内硬信号（环境变量）。wizard 判定只用这个：kimi 的「state cwd/workDir==当前 cwd」目录
    启发式无法区分「kimi 会话内」与「同项目目录的独立终端」，拿来压向导会把独立终端
    误判成会话内（tt setup 不出向导直接默认全装）——启发式只用于 daily/weekly 报表收窄。"""
    return bool(
        os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CODEX_SANDBOX")
        or os.environ.get("CLAUDECODE")
        or os.environ.get("PI_CODING_AGENT")
    )


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _should_run_wizard() -> bool:
    """是否进交互向导：必须双 tty 且不在 AI 会话内（否则走 _auto_setup 非交互全装）。"""
    return _is_tty() and not _in_agent_session_env()


def _run_setup_flow() -> None:
    """配置流程**单一入口**：先确认装了至少一个 agent（detect_agents 守卫只此一处），
    再按环境分流——双 tty 非会话内进交互向导，否则非交互默认全装。
    `tt setup` 与首次运行「没配过」时都走这里。"""
    if not detect_agents():
        get_console().print(f"[red]{t('no_agent_install')}[/red]")
        return
    if _should_run_wizard():
        from .wizard import run_wizard
        run_wizard()  # wizard 欢迎行下会显示检测到的 agent
    else:
        _auto_setup()


def _auto_setup() -> None:
    """非交互环境（非 tty / CI / 会话内）：语言跟随**系统设置**（绕过 CLI LANG）、主题 mocha、
    组件按推荐默认（hooks.recommended_components：已有意图优先、不替换已有自定义 statusLine）。
    仅当用户从未配置过语言/主题时落默认（不覆盖已有选择）。
    agent 守卫在 _run_setup_flow 已做，这里假设至少有一个 agent。"""
    if config.resolve_lang() is None:
        sys_lang = i18n._detect_system_lang()
        config.save_lang(sys_lang)
        i18n.set_lang(sys_lang)
    if not config.load_config().get("theme"):
        config.save_theme("mocha")
    setup(auto=True)  # 组件走推荐默认（setup 内部 components=None → recommended_components）
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
    if is_setup():
        update_hook()
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


def _apply_theme_override(name: str | None) -> None:
    if name is None:
        return
    if name not in themes.THEMES:
        get_console().print(f"[red]{t('theme_unknown', name=name)}[/red]")
        get_console().print(f"[dim]{t('theme_options', names=', '.join(themes.THEME_NAMES))}[/dim]")
        sys.exit(1)
    theme.set_active_theme(name)


def _handle_non_data_command(command: str, args: list[str]) -> bool:
    """处理不需要 Agent 数据的命令；已处理返回 True。"""
    if command in ("--version", "-v", "-V"):
        print(f"token-tracker {_get_version()}")
        print("by stormzhang · https://github.com/stormzhang/token-tracker")
        return True
    if command == "theme":
        cmd_theme(args)
        return True
    if command == "setup":
        _run_setup_flow()
        return True
    if command == "unsetup":
        unsetup()
        return True
    return False


def _ensure_data_ready() -> None:
    """数据命令运行前同步脚本、处理升级引导，并确保至少完成过一次 setup。"""
    configured = is_setup()
    if configured and needs_update():
        update_hook()
    if configured and config.setup_version() < config.SETUP_VERSION:
        _run_setup_flow()
    if not is_setup():
        _run_setup_flow()
        if not is_setup():
            sys.exit(1)


def _select_agents(filter_agent: str | None):
    agents = detect_agents()
    if filter_agent is None:
        return agents
    matched = [agent for agent in agents if agent.id == filter_agent]
    if matched:
        return matched
    flag = next((f for f, agent_id in _FLAG_TO_ID.items() if agent_id == filter_agent), filter_agent)
    get_console().print(f"[red]{t('agent_not_detected', flag=flag)}[/red]")
    sys.exit(1)


def _report_agents(agents, command: str, filter_agent: str | None):
    """daily/weekly 在 Agent 会话内自动收窄；显式 filter 优先。"""
    if filter_agent is not None or command not in ("daily", "weekly"):
        return agents
    session_agent = _current_session_agent()
    if not session_agent or session_agent not in {agent.id for agent in agents}:
        return agents
    return [agent for agent in agents if agent.id == session_agent]


def _run_report_command(command: str, args: list[str], agents, filter_agent: str | None) -> None:
    rest_args, sort_key, sort_desc = _parse_sort_args(args)
    if command not in _REPORT_COMMANDS:
        get_console().print(f"[red]{t('unknown_cmd', cmd=command)}[/red]")
        get_console().print(f"[dim]{t('available_cmds')}[/dim]")
        sys.exit(1)

    selected = _report_agents(agents, command, filter_agent)
    agent_names = [agent.name for agent in selected]
    agg_fn, render_fn, time_attr, no_sort_attr, default_reverse = _REPORT_COMMANDS[command]
    loaded = _load_per_agent(selected)
    stats = _aggregate_per_agent(loaded, agg_fn)
    default_attr = time_attr if sort_key == "time" else no_sort_attr

    if command == "sessions":
        _render_session_report(stats, rest_args, sort_key, sort_desc,
                               default_attr, default_reverse, agent_names)
        return

    assert render_fn is not None  # sessions（render_fn=None）已在上面 return，其余命令都有渲染函数
    _apply_sort(stats, sort_key, sort_desc, default_attr, default_reverse)
    if command == "daily":
        render_fn(stats, agents=agent_names,
                  weekly=_aggregate_per_agent(loaded, aggregate_weekly),
                  monthly=_aggregate_per_agent(loaded, aggregate_monthly))
    elif command == "weekly":
        render_weekly(stats, agents=agent_names, daily=_aggregate_per_agent(loaded, aggregate_daily))
    else:  # monthly
        render_monthly(stats, agents=agent_names,
                       daily=_aggregate_per_agent(loaded, aggregate_daily),
                       weekly=_aggregate_per_agent(loaded, aggregate_weekly))


def _render_session_report(stats, rest_args: list[str], sort_key: str | None,
                           sort_desc: bool | None, default_attr: str,
                           default_reverse: bool, agent_names: list[str]) -> None:
    # 先按时间取最近 N 条，再按用户指定字段展示；避免历史高 cost 会话长期霸榜。
    kept = [session for session in stats if session.active_minutes >= 5]
    kept.sort(key=lambda session: session.start_time, reverse=True)
    try:
        limit = _parse_limit(rest_args, default=20)
    except ValueError as exc:
        get_console().print(f"[red]{t('sessions_limit_invalid', value=exc.args[0])}[/red]")
        sys.exit(1)
    shown = kept[:limit]
    _apply_sort(shown, sort_key, sort_desc, default_attr, default_reverse)
    render_sessions_view(_summary_from_sessions(shown), shown, agent_names)


def main():
    # --mock：本地开发演示，加载 mock/ 假数据再走正常报表流程（mock/ 在 .gitignore）
    if "--mock" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mock"]
        _load_local_mock()

    args = sys.argv[1:]
    # --claude / --codex：多 agent 环境按需只看一个 agent 的报表（issue #19），互斥；不给则走默认
    args, filter_agent = _extract_agent_arg(args)
    # --theme NAME：临时覆盖主题（仅本次进程、不落配置/不重烘焙状态栏），对所有报表 + status 生效
    args, theme_override = _extract_theme_arg(args)
    _apply_theme_override(theme_override)
    command = args[0] if args else "daily"

    if _handle_non_data_command(command, args[1:]):
        return

    _ensure_data_ready()
    agents = _select_agents(filter_agent)

    if command in ("status", "dashboard"):
        data = _build_status_data(agents)
        if not data:
            get_console().print(f"[yellow]{t('no_token_data')}[/yellow]")
            return
        render_status(**data)
        return

    if command == "sidebar":
        _cmd_sidebar(agents, args[1:])
        return
    _run_report_command(command, args[1:], agents, filter_agent)


if __name__ == "__main__":
    main()
