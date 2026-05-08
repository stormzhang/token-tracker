import sys

from .adapters import claude, codex
from .adapters.rate_limits import load_rate_limits as load_claude_rate_limits
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.blocks import analyze_blocks, calculate_p90
from .hooks import is_setup, setup, unsetup
from .ui.tables import (
    console, render_blocks, render_daily, render_dashboard,
    render_monthly, render_sessions, render_tab_bar, render_weekly,
)

AGENT_ALIASES = {"claude": "claude-code", "codex": "codex"}
AGENT_LOADERS = {"claude-code": claude, "codex": codex}


def _load_entries(agent_id: str, hours_back: int = 0):
    loader = AGENT_LOADERS.get(agent_id)
    return loader.load_entries(hours_back=hours_back) if loader else []


def _load_all_entries(hours_back: int = 0):
    entries = []
    for agent_id in AGENT_LOADERS:
        entries += _load_entries(agent_id, hours_back)
    entries.sort(key=lambda e: e.timestamp)
    return entries


def _show_agent_dashboard(agent_id: str):
    if agent_id == "claude-code" and not is_setup():
        console.print("[dim]首次运行，自动配置 statusLine hook...[/dim]")
        setup()
        console.print()

    agent_name = "Claude Code" if agent_id == "claude-code" else "Codex"
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
    recent = _load_entries(agent_id, hours_back=48)
    blocks = analyze_blocks(recent)
    rate_loaders = {"claude-code": load_claude_rate_limits, "codex": codex.load_rate_limits}
    rate_limits = rate_loaders.get(agent_id, lambda: None)()
    p90 = None
    has_limits = rate_limits and (rate_limits.five_hour_pct is not None or rate_limits.seven_day_pct is not None)
    if not has_limits:
        p90 = calculate_p90(daily)
    return dict(
        daily_stats=daily, weekly_stats=weekly, monthly_stats=monthly,
        sessions=sessions, blocks=blocks, rate_limits=rate_limits,
        p90=p90, agents=[agent_name],
    )


def _show_interactive_dashboard(agents):
    import tty
    import termios
    from io import StringIO
    from rich.console import Console as RichConsole
    import src.ui.tables as _tables

    if any(a.id == "claude-code" for a in agents) and not is_setup():
        console.print("[dim]首次运行，自动配置 statusLine hook...[/dim]")
        setup()

    agent_names = [a.name for a in agents]
    console.print(f"[dim]加载数据...[/dim]")
    cache = {a.id: _build_agent_data(a.id, a.name) for a in agents}

    current = 0
    orig = _tables.console

    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            buf = StringIO()
            _tables.console = RichConsole(
                file=buf, width=orig.width, force_terminal=True,
            )
            render_tab_bar(agent_names, current)
            data = cache[agents[current].id]
            if data:
                render_dashboard(**data)
            else:
                _tables.console.print(f"[yellow]暂无数据[/yellow]")
            _tables.console = orig

            sys.stdout.write("\033[2J\033[H" + buf.getvalue())
            sys.stdout.flush()

            key = _read_key(tty, termios)
            if key == "left":
                current = (current - 1) % len(agents)
            elif key == "right":
                current = (current + 1) % len(agents)
            elif key == "quit":
                break
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
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
            return "other"
        if ch in (b"h", b"k"):
            return "left"
        if ch in (b"l", b"j"):
            return "right"
        if ch in (b"q", b"Q", b"\x03"):
            return "quit"
        return "other"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    args = sys.argv[1:]
    command = args[0] if args else "dashboard"

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
    console.print(f"[dim]检测到: {', '.join(a.name + ' ✓' for a in agents)}[/dim]")

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
        elif len(agents) > 1 and sys.stdin.isatty():
            _show_interactive_dashboard(agents)
        else:
            _show_agent_dashboard(agents[0].id)
        return

    # 其他命令使用合并数据
    agent_names = [a.name for a in agents]
    entries = _load_all_entries()
    if not entries:
        console.print("[yellow]暂无 token 使用数据[/yellow]")
        sys.exit(0)

    if command == "daily":
        stats = aggregate_daily(entries)
        render_daily(stats, agents=agent_names)
    elif command == "weekly":
        stats = aggregate_weekly(entries)
        render_weekly(stats, agents=agent_names)
    elif command == "monthly":
        stats = aggregate_monthly(entries)
        render_monthly(stats, agents=agent_names)
    elif command == "sessions":
        limit = 20
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError:
                pass
        stats = aggregate_sessions(entries)
        render_sessions(stats, limit)
    elif command == "blocks":
        hours = 48
        if len(args) > 1:
            try:
                hours = int(args[1])
            except ValueError:
                pass
        recent = _load_all_entries(hours_back=hours)
        blocks = analyze_blocks(recent)
        render_blocks(blocks)
    else:
        console.print(f"[red]未知命令: {command}[/red]")
        console.print("[dim]可用命令: dashboard, daily, weekly, monthly, sessions, blocks, claude, codex, setup, unsetup[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
