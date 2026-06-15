import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .adapters import claude, codex
from .adapters.rate_limits import load_rate_limits as load_claude_rate_limits
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.blocks import analyze_blocks, calculate_p90
from .hooks import is_setup, needs_update, setup, unsetup, update_hook
from .i18n import t
from .ui.console import capture_console, get_console
from .ui.tables import (
    AGENT_LABEL,
    render_daily,
    render_dashboard,
    render_monthly,
    render_sessions,
    render_tab_bar,
    render_weekly,
)

AGENT_ALIASES = {"claude": "claude-code", "codex": "codex"}
AGENT_LOADERS = {"claude-code": claude, "codex": codex}
RATE_LIMIT_LOADERS = {"claude-code": load_claude_rate_limits, "codex": codex.load_rate_limits}

# dashboard 数据面板只看最近 N 小时的会话块（活跃/空闲判定）
_RECENT_BLOCK_HOURS = 48

# 交互式 dashboard 的终端控制转义序列
_ALT_ENTER = "\033[?1049h\033[?7l\033[2J\033[3J\033[H\033[?25l"  # 进备用屏 + 隐光标 + 禁换行 + 清屏
_ALT_EXIT = "\033[?7h\033[?25h\033[?1049l"  # 恢复换行 + 显光标 + 退备用屏
_CLEAR_SCREEN = "\033[2J\033[3J\033[H"

# 排序字段 → stats 属性名（单一权威表，dashboard sort cycle 也复用）。
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
    "daily": (aggregate_daily, render_daily, "date", "total_tokens", True),
    "weekly": (aggregate_weekly, render_weekly, "week", "week", True),
    "monthly": (aggregate_monthly, render_monthly, "month", "month", False),
    "sessions": (aggregate_sessions, render_sessions, "start_time", "start_time", True),
}


def _parse_limit(args: list[str], default: int) -> int:
    for a in args:
        try:
            return int(a)
        except ValueError:
            pass
    return default


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


def _show_agent_dashboard(agent_id: str):
    agent_name = AGENT_LABEL.get(agent_id, agent_id)
    data = _build_agent_data(agent_id, agent_name)
    if not data:
        get_console().print(f"[yellow]{t('no_token_data')}[/yellow]")
        return
    render_dashboard(**data)


def _build_agent_data(agent_id: str, agent_name: str) -> dict | None:
    entries = _load_entries(agent_id)
    if not entries:
        return None
    daily = aggregate_daily(entries)
    weekly = aggregate_weekly(entries)
    monthly = aggregate_monthly(entries)
    sessions = aggregate_sessions(entries)
    cutoff = datetime.now(UTC) - timedelta(hours=_RECENT_BLOCK_HOURS)
    recent = [e for e in entries if e.timestamp >= cutoff]
    blocks = analyze_blocks(recent)
    rate_limits = RATE_LIMIT_LOADERS.get(agent_id, lambda: None)()
    p90 = None
    has_limits = rate_limits and (rate_limits.five_hour_pct is not None or rate_limits.seven_day_pct is not None)
    if not has_limits:
        p90 = calculate_p90(daily)
    return dict(
        daily_stats=daily, weekly_stats=weekly, monthly_stats=monthly,
        sessions=sessions, blocks=blocks, rate_limits=rate_limits,
        p90=p90, agents=[agent_name],
    )


def _initial_agent_index(agents) -> int:
    preferred = None
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX"):
        preferred = "codex"
    elif os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDECODE"):
        preferred = "claude-code"

    if preferred:
        for i, agent in enumerate(agents):
            if agent.id == preferred:
                return i
    return 0


def _fit_screen(text: str, height: int, scroll_offset: int) -> tuple[str, int]:
    lines = text.splitlines()
    if not lines:
        return "", 0
    max_body = max(1, height - 1)
    max_scroll = max(0, len(lines) - max_body)
    scroll_offset = max(0, min(scroll_offset, max_scroll))
    visible = lines[:1] + lines[1 + scroll_offset:1 + scroll_offset + max_body - 1]
    return "\n".join(visible), max_scroll


def _dashboard_sort_cycle():
    return [
        ("time", "start_time", t("sort_time")),  # dashboard 始终排 sessions，time→start_time
        ("tokens", SORT_ATTRS["tokens"], t("sort_token")),
        ("cost", SORT_ATTRS["cost"], t("sort_cost")),
        ("messages", SORT_ATTRS["messages"], t("sort_messages")),
    ]


@dataclass
class _DashState:
    current: int = 0
    scroll_offset: int = 0
    sort_idx: int = 0
    sort_desc: bool = True
    session_limit: int = 30


def _apply_key(st: _DashState, key: str, *, num_agents: int, num_sorts: int, max_scroll: int, page: int) -> bool:
    """按键更新 dashboard 状态；返回 False 表示退出循环。纯函数，可单测。"""
    if key == "quit":
        return False
    if key == "left":
        st.current = (st.current - 1) % num_agents
        st.scroll_offset = 0
    elif key == "right":
        st.current = (st.current + 1) % num_agents
        st.scroll_offset = 0
    elif key == "up":
        st.scroll_offset = max(0, st.scroll_offset - 1)
    elif key == "down":
        st.scroll_offset = min(max_scroll, st.scroll_offset + 1)
    elif key == "page_up":
        st.scroll_offset = max(0, st.scroll_offset - page)
    elif key == "page_down":
        st.scroll_offset = min(max_scroll, st.scroll_offset + page)
    elif key == "sort":
        st.sort_idx = (st.sort_idx + 1) % num_sorts
        st.scroll_offset = 0
    elif key == "reverse":
        st.sort_desc = not st.sort_desc
    elif key == "more":
        st.session_limit += 10
    elif key == "less":
        st.session_limit = max(10, st.session_limit - 10)
    return True


@contextlib.contextmanager
def _alt_screen():
    """进入/退出终端备用屏（隐藏光标、禁自动换行），保证异常时也能恢复。"""
    sys.stdout.write(_ALT_ENTER)
    sys.stdout.flush()
    try:
        yield
    finally:
        sys.stdout.write(_ALT_EXIT)
        sys.stdout.flush()


def _render_dashboard_frame(agent_names, current, data, st: _DashState, sort_cycle, width, height) -> tuple[str, int]:
    if data:
        _, sort_attr, sort_label = sort_cycle[st.sort_idx]
        sorted_sessions = sorted(data["sessions"], key=lambda s: getattr(s, sort_attr), reverse=st.sort_desc)
        arrow = "↓" if st.sort_desc else "↑"
        session_title = t("session_title", limit=st.session_limit, label=sort_label, arrow=arrow)
    else:
        sorted_sessions = []
        session_title = None

    with capture_console(width) as buf:
        render_tab_bar(agent_names, current)
        if data:
            render_data = {**data, "sessions": sorted_sessions}
            render_dashboard(**render_data, session_limit=st.session_limit, top_margin=False, session_title=session_title)
        else:
            get_console().print(f"[yellow]{t('no_data')}[/yellow]")

    return _fit_screen(buf.getvalue(), height, st.scroll_offset)


def _show_interactive_dashboard(agents):
    agent_names = [a.name for a in agents]
    st = _DashState(current=_initial_agent_index(agents))
    cache: dict = {}
    sort_cycle = _dashboard_sort_cycle()

    with _alt_screen():
        while True:
            agent = agents[st.current]
            if agent.id not in cache:
                sys.stdout.write(_CLEAR_SCREEN + f"\033[2m{t('loading')}\033[0m")
                sys.stdout.flush()
                cache[agent.id] = _build_agent_data(agent.id, agent.name)

            size = shutil.get_terminal_size((80, 24))
            screen, max_scroll = _render_dashboard_frame(
                agent_names, st.current, cache[agent.id], st, sort_cycle, size.columns, size.lines,
            )
            sys.stdout.write(_CLEAR_SCREEN + screen)
            sys.stdout.flush()

            if not _apply_key(
                st, _read_key(),
                num_agents=len(agents), num_sorts=len(sort_cycle),
                max_scroll=max_scroll, page=max(1, size.lines - 3),
            ):
                break


# 普通字母按键 → 动作，两个平台的 reader 共用（此前 Windows 漏了 sort/reverse/more/less）
KEY_MAP = {
    b"h": "left", b"l": "right", b"k": "up", b"j": "down",
    b"b": "page_up", b"f": "page_down",
    b"s": "sort", b"r": "reverse",
    b"+": "more", b"=": "more", b"-": "less", b"_": "less",
    b"q": "quit", b"Q": "quit", b"\x03": "quit",
}

# 终端方向键的 ESC 序列尾字节 → 动作
_UNIX_ARROW = {b"D": "left", b"C": "right", b"A": "up", b"B": "down"}
_WIN_ARROW = {b"K": "left", b"M": "right", b"H": "up", b"P": "down", b"I": "page_up", b"Q": "page_down"}


def _read_key_unix():
    import os as _os
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = _os.read(fd, 1)
        if ch == b"\x1b":
            if not select.select([fd], [], [], 0.05)[0]:
                return "quit"
            ch2 = _os.read(fd, 1)
            if ch2 == b"[":
                ch3 = _os.read(fd, 1)
                if ch3 in _UNIX_ARROW:
                    return _UNIX_ARROW[ch3]
                if ch3 in (b"5", b"6"):
                    if select.select([fd], [], [], 0.05)[0]:
                        _os.read(fd, 1)
                    return "page_up" if ch3 == b"5" else "page_down"
            return "other"
        return KEY_MAP.get(ch, "other")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key_win():
    import msvcrt
    ch = msvcrt.getch()
    if ch in (b"\xe0", b"\x00"):
        return _WIN_ARROW.get(msvcrt.getch(), "other")
    if ch == b"\x1b":
        return "quit"
    return KEY_MAP.get(ch, "other")


_read_key = _read_key_win if sys.platform == "win32" else _read_key_unix


def _get_version() -> str:
    from importlib.metadata import version
    return version("token-tracker")


def _cmd_quota(args: list[str]) -> None:
    """诊断配额查询状态"""
    from .adapters.rate_limits import _load_official, _load_config, load_rate_limits, CONFIG_FILE
    from .adapters.providers import create_provider

    debug = "--debug" in args or "-d" in args

    console = get_console()
    console.print("[bold]配额诊断[/bold]")
    console.print(f"配置文件: {CONFIG_FILE}")

    # 检查配置文件
    config = _load_config()
    if config:
        console.print(f"  [green]✓[/green] 配置文件存在")
        if debug:
            import json
            masked = dict(config)
            rp = masked.get("rate_provider")
            if rp:
                rp = dict(rp)
                masked["rate_provider"] = rp
                if rp.get("cookie"):
                    rp["cookie"] = rp["cookie"][:20] + "..." + rp["cookie"][-10:]
                if rp.get("command"):
                    import os as _os
                    home = _os.path.expanduser("~")
                    rp["command"] = rp["command"].replace(home, "~")
            console.print(f"  配置内容: {json.dumps(masked, indent=2, ensure_ascii=False)}")
    else:
        console.print(f"  [yellow]○[/yellow] 配置文件不存在")

    # 检查官方数据
    official = _load_official()
    if official:
        has_quota = official.five_hour_pct is not None or official.seven_day_pct is not None
        status = "✓ 含配额数据" if has_quota else "○ 无配额数据"
        console.print(f"官方数据: [{ 'green' if has_quota else 'yellow' }]{status}[/{ 'green' if has_quota else 'yellow' }]")
        if debug:
            console.print(f"  5h: {official.five_hour_pct}%, 7d: {official.seven_day_pct}%")
    else:
        console.print("官方数据: [yellow]○ 无数据[/yellow]")

    # 检查提供者
    if config and "rate_provider" in config:
        provider = create_provider(config["rate_provider"])
        if provider:
            console.print(f"提供者: [green]✓ {provider.__class__.__name__}[/green]")
            result = provider.get_limits()
            if result:
                console.print(f"  查询成功:")
                if result.five_hour_pct is not None:
                    console.print(f"    5h: {result.five_hour_pct}%")
                if result.seven_day_pct is not None:
                    console.print(f"    7d: {result.seven_day_pct}%")
                if result.monthly_pct is not None:
                    console.print(f"    月: {result.monthly_pct}%")
                console.print(f"    来源: {result.source}")
            else:
                console.print(f"  [red]✗ 查询失败[/red]")
        else:
            pt = config["rate_provider"].get("type", "unknown")
            console.print(f"提供者: [red]✗ 不支持的类型 '{pt}'[/red]")
    else:
        console.print("提供者: [yellow]○ 未配置[/yellow]")

    # 最终生效结果
    final = load_rate_limits()
    console.print()
    console.print("[bold]最终生效数据[/bold]")
    if final:
        parts = []
        if final.five_hour_pct is not None:
            parts.append(f"5h={final.five_hour_pct:.0f}%")
        if final.seven_day_pct is not None:
            parts.append(f"7d={final.seven_day_pct:.0f}%")
        if final.monthly_pct is not None:
            parts.append(f"月={final.monthly_pct:.0f}%")
        if parts:
            console.print(f"  [green]✓[/green] {', '.join(parts)}")
        else:
            console.print(f"  [yellow]○[/yellow] 无有效配额数据")
        if final.model:
            console.print(f"  模型: {final.model}")
    else:
        console.print(f"  [yellow]○[/yellow] 无数据")


def main():
    args = sys.argv[1:]
    command = args[0] if args else "dashboard"

    # 版本查询不该触发任何文件读写，放在 auto-update 之前短路返回
    if command in ("--version", "-v", "-V"):
        print(f"tt {_get_version()}")
        return

    # 配额诊断：独立于 setup/auto-update，纯粹查询配置和数据源状态
    if command == "quota":
        _cmd_quota(args[1:])
        return

    # 已配置过的情况下，任意命令都顺带同步状态栏脚本（setup/unsetup 自行处理）
    # 避免升级 pip 包后忘了 tt setup，导致 ~/.claude/tt-statusline.py 停在旧版本
    if command not in ("setup", "unsetup") and is_setup() and needs_update():
        update_hook()

    if command == "setup":
        setup()
        return
    if command == "unsetup":
        unsetup()
        return

    agents = detect_agents()
    if not agents:
        get_console().print(f"[red]{t('no_agent')}[/red]")
        sys.exit(1)

    agent_ids = {a.id for a in agents}

    if command != "dashboard":
        get_console().print(f"[dim]{t('detected', agents=', '.join(a.name + ' ✓' for a in agents))}[/dim]")

    if not is_setup():
        setup(auto=True)

    # tt claude / tt codex
    if command in AGENT_ALIASES:
        agent_id = AGENT_ALIASES[command]
        if agent_id not in agent_ids:
            get_console().print(f"[red]{t('agent_not_found', name=command)}[/red]")
            sys.exit(1)
        _show_agent_dashboard(agent_id)
        return

    if command == "dashboard":
        agent_filter = args[1] if len(args) > 1 and args[1] in AGENT_ALIASES else None
        if agent_filter:
            agent_id = AGENT_ALIASES[agent_filter]
            if agent_id not in agent_ids:
                get_console().print(f"[red]{t('agent_not_found', name=agent_filter)}[/red]")
                sys.exit(1)
            _show_agent_dashboard(agent_id)
        elif len(agents) > 1 and sys.stdin.isatty():
            _show_interactive_dashboard(agents)
        else:
            _show_agent_dashboard(agents[0].id)
        return

    # 其他命令使用合并数据
    agent_names = [a.name for a in agents]
    rest_args, sort_key, sort_desc = _parse_sort_args(args[1:])

    if command not in _REPORT_COMMANDS:
        get_console().print(f"[red]{t('unknown_cmd', cmd=command)}[/red]")
        get_console().print(f"[dim]{t('available_cmds')}[/dim]")
        sys.exit(1)

    agg_fn, render_fn, time_attr, no_sort_attr, default_reverse = _REPORT_COMMANDS[command]
    stats = _aggregate_per_agent(agents, agg_fn)
    default_attr = time_attr if sort_key == "time" else no_sort_attr
    _apply_sort(stats, sort_key, sort_desc, default_attr, default_reverse)

    if command == "sessions":
        render_fn(stats, _parse_limit(rest_args, default=20))
    else:
        render_fn(stats, agents=agent_names)


if __name__ == "__main__":
    main()
