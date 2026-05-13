import sys

from .adapters import claude, codex
from .adapters.rate_limits import load_rate_limits as load_claude_rate_limits
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.blocks import analyze_blocks, calculate_p90
from .hooks import is_setup, needs_update, setup, unsetup, update_hook
from .ui.tables import (
    AGENT_LABEL, console, render_daily, render_dashboard,
    render_monthly, render_sessions, render_tab_bar, render_weekly,
)

AGENT_ALIASES = {"claude": "claude-code", "codex": "codex"}
AGENT_LOADERS = {"claude-code": claude, "codex": codex}
RATE_LIMIT_LOADERS = {"claude-code": load_claude_rate_limits, "codex": codex.load_rate_limits}


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
        console.print(f"[yellow]暂无 token 使用数据[/yellow]")
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
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
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
    import os

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


def _show_interactive_dashboard(agents):
    import tty
    import termios
    import shutil
    from io import StringIO
    from rich.console import Console as RichConsole
    import src.ui.tables as _tables

    agent_names = [a.name for a in agents]
    current = _initial_agent_index(agents)
    scroll_offset = 0
    orig = _tables.console

    sys.stdout.write("\033[?1049h\033[?7l\033[2J\033[3J\033[H\033[?25l")
    cache = {}

    try:
        while True:
            agent = agents[current]
            if agent.id not in cache:
                sys.stdout.write("\033[2J\033[3J\033[H\033[2m加载数据...\033[0m")
                sys.stdout.flush()
                cache[agent.id] = _build_agent_data(agent.id, agent.name)

            size = shutil.get_terminal_size((80, 24))
            width = size.columns
            height = size.lines

            buf = StringIO()
            _tables.console = RichConsole(
                file=buf, width=width, force_terminal=True,
            )
            render_tab_bar(agent_names, current)
            data = cache[agent.id]
            if data:
                render_dashboard(**data, session_limit=10, top_margin=False)
            else:
                _tables.console.print(f"[yellow]暂无数据[/yellow]")
            _tables.console = orig

            screen, max_scroll = _fit_screen(buf.getvalue(), height, scroll_offset)
            sys.stdout.write("\033[2J\033[3J\033[H" + screen)
            sys.stdout.flush()

            key = _read_key(tty, termios)
            if key == "left":
                current = (current - 1) % len(agents)
                scroll_offset = 0
            elif key == "right":
                current = (current + 1) % len(agents)
                scroll_offset = 0
            elif key == "up":
                scroll_offset = max(0, scroll_offset - 1)
            elif key == "down":
                scroll_offset = min(max_scroll, scroll_offset + 1)
            elif key == "page_up":
                scroll_offset = max(0, scroll_offset - max(1, height - 3))
            elif key == "page_down":
                scroll_offset = min(max_scroll, scroll_offset + max(1, height - 3))
            elif key == "quit":
                break
    finally:
        sys.stdout.write("\033[?7h\033[?25h\033[?1049l")
        sys.stdout.flush()
        _tables.console = orig


def _read_key(tty, termios):
    import os as _os
    import select
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
                if ch3 == b"D":
                    return "left"
                if ch3 == b"C":
                    return "right"
                if ch3 == b"A":
                    return "up"
                if ch3 == b"B":
                    return "down"
                if ch3 in (b"5", b"6"):
                    if select.select([fd], [], [], 0.05)[0]:
                        _os.read(fd, 1)
                    return "page_up" if ch3 == b"5" else "page_down"
            return "other"
        if ch == b"h":
            return "left"
        if ch == b"l":
            return "right"
        if ch == b"k":
            return "up"
        if ch == b"j":
            return "down"
        if ch == b"b":
            return "page_up"
        if ch == b"f":
            return "page_down"
        if ch in (b"q", b"Q", b"\x03"):
            return "quit"
        return "other"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _get_version() -> str:
    from importlib.metadata import version
    return version("token-tracker")


def main():
    args = sys.argv[1:]
    command = args[0] if args else "dashboard"

    if command in ("--version", "-v", "-V"):
        print(f"tt {_get_version()}")
        return
    if command == "setup":
        setup()
        return
    if command == "unsetup":
        unsetup()
        return

    agents = detect_agents()
    if not agents:
        console.print("[red]未检测到任何 AI Agent[/red]")
        sys.exit(1)

    agent_ids = {a.id for a in agents}

    if command != "dashboard":
        console.print(f"[dim]检测到: {', '.join(a.name + ' ✓' for a in agents)}[/dim]")

    if not is_setup():
        setup(auto=True)
    elif needs_update():
        update_hook()

    # tt claude / tt codex
    if command in AGENT_ALIASES:
        agent_id = AGENT_ALIASES[command]
        if agent_id not in agent_ids:
            console.print(f"[red]未检测到 {command}[/red]")
            sys.exit(1)
        _show_agent_dashboard(agent_id)
        return

    if command == "dashboard":
        agent_filter = args[1] if len(args) > 1 and args[1] in AGENT_ALIASES else None
        if agent_filter:
            agent_id = AGENT_ALIASES[agent_filter]
            if agent_id not in agent_ids:
                console.print(f"[red]未检测到 {agent_filter}[/red]")
                sys.exit(1)
            _show_agent_dashboard(agent_id)
        elif len(agents) > 1 and sys.stdin.isatty() and sys.platform != "win32":
            _show_interactive_dashboard(agents)
        else:
            _show_agent_dashboard(agents[0].id)
        return

    # 其他命令使用合并数据
    agent_names = [a.name for a in agents]

    if command == "daily":
        stats = _aggregate_per_agent(agents, aggregate_daily)
        stats.sort(key=lambda s: s.total_tokens, reverse=True)
        render_daily(stats, agents=agent_names)
    elif command == "weekly":
        stats = _aggregate_per_agent(agents, aggregate_weekly)
        stats.sort(key=lambda s: s.week, reverse=True)
        render_weekly(stats, agents=agent_names)
    elif command == "monthly":
        stats = _aggregate_per_agent(agents, aggregate_monthly)
        stats.sort(key=lambda s: s.month)
        render_monthly(stats, agents=agent_names)
    elif command == "sessions":
        limit = 20
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError:
                pass
        stats = _aggregate_per_agent(agents, aggregate_sessions)
        stats.sort(key=lambda s: s.start_time, reverse=True)
        render_sessions(stats, limit)
    else:
        console.print(f"[red]未知命令: {command}[/red]")
        console.print("[dim]可用命令: dashboard, daily, weekly, monthly, sessions, claude, codex, setup, unsetup, --version[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
