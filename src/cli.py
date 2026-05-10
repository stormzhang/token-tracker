import sys
from datetime import datetime, timezone

from .adapters import claude, codex
from .adapters.rate_limits import load_rate_limits as load_claude_rate_limits
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.blocks import analyze_blocks, calculate_p90
from .analyzer.cost import calculate_cost
from .hooks import is_setup, setup, unsetup
from .ui.progress import render_progress, use_color
from .ui.tables import (
    console, render_blocks, render_daily, render_dashboard,
    render_monthly, render_sessions, render_tab_bar, render_weekly,
)

AGENT_ALIASES = {"claude": "claude-code", "codex": "codex"}
AGENT_LOADERS = {"claude-code": claude, "codex": codex}
AGENT_NAMES = {"claude-code": "Claude", "codex": "Codex"}
RATE_LIMIT_LOADERS = {"claude-code": load_claude_rate_limits, "codex": codex.load_rate_limits}


def _load_entries(agent_id: str, hours_back: int = 0):
    loader = AGENT_LOADERS.get(agent_id)
    return loader.load_entries(hours_back=hours_back) if loader else []


def _load_all_entries(hours_back: int = 0):
    entries = []
    for agent_id in AGENT_LOADERS:
        entries += _load_entries(agent_id, hours_back)
    entries.sort(key=lambda e: e.timestamp)
    return entries


def _parse_status_args(args: list[str]) -> dict:
    opts = {
        "agent": None,
        "color": None,
        "fields": {"limits"},
        "format": "compact",
        "resets": False,
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--color":
            opts["color"] = True
        elif arg == "--no-color":
            opts["color"] = False
        elif arg in ("--reset", "--resets"):
            opts["resets"] = True
        elif arg == "--agent" and i + 1 < len(args):
            opts["agent"] = AGENT_ALIASES.get(args[i + 1], args[i + 1])
            i += 1
        elif arg.startswith("--agent="):
            value = arg.split("=", 1)[1]
            opts["agent"] = AGENT_ALIASES.get(value, value)
        elif arg == "--fields" and i + 1 < len(args):
            opts["fields"] = set(filter(None, args[i + 1].split(",")))
            i += 1
        elif arg.startswith("--fields="):
            opts["fields"] = set(filter(None, arg.split("=", 1)[1].split(",")))
        elif arg == "--format" and i + 1 < len(args):
            opts["format"] = args[i + 1]
            i += 1
        elif arg.startswith("--format="):
            opts["format"] = arg.split("=", 1)[1]
        elif arg == "--claude":
            opts["agent"] = "claude-code"
        elif arg == "--codex":
            opts["agent"] = "codex"
        elif arg in AGENT_ALIASES:
            opts["agent"] = AGENT_ALIASES[arg]
        i += 1
    return opts


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}%"


def _fmt_reset(resets_at: int | None) -> str:
    if not resets_at:
        return ""
    remaining = int(resets_at - datetime.now(timezone.utc).timestamp())
    if remaining <= 0:
        return " reset:now"
    days, rem = divmod(remaining, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f" reset:{days}d{hours}h"
    if hours:
        return f" reset:{hours}h{minutes:02d}m"
    return f" reset:{minutes}m"


def _fmt_tokens_compact(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _build_status_item(agent_id: str, fields: set[str]) -> dict:
    rate_loader = RATE_LIMIT_LOADERS.get(agent_id)
    rate_limits = rate_loader() if rate_loader else None
    has_limits = rate_limits and (
        rate_limits.five_hour_pct is not None
        or rate_limits.seven_day_pct is not None
    )
    item = {
        "agent_id": agent_id,
        "name": AGENT_NAMES.get(agent_id, agent_id),
        "limits": {
            "five_hour_pct": rate_limits.five_hour_pct if rate_limits else None,
            "five_hour_resets_at": rate_limits.five_hour_resets_at if rate_limits else None,
            "seven_day_pct": rate_limits.seven_day_pct if rate_limits else None,
            "seven_day_resets_at": rate_limits.seven_day_resets_at if rate_limits else None,
        },
    }

    if "tokens" in fields or "cost" in fields or not has_limits:
        today = datetime.now().astimezone().date()
        total_tokens = 0
        total_cost = 0.0
        for entry in _load_entries(agent_id):
            if entry.timestamp.astimezone().date() == today:
                total_tokens += entry.total_tokens
                total_cost += calculate_cost(entry)
        item["today"] = {
            "tokens": total_tokens,
            "cost_usd": round(total_cost, 6),
        }

    return item


def _render_status_text(
    items: list[dict],
    fields: set[str],
    status_format: str,
    color: bool,
    show_resets: bool,
) -> str:
    parts = []
    for item in items:
        limits = item["limits"]
        has_limits = limits["five_hour_pct"] is not None or limits["seven_day_pct"] is not None
        segment = f"{item['name']}"
        if has_limits:
            five_reset = _fmt_reset(limits["five_hour_resets_at"]) if show_resets else ""
            seven_reset = _fmt_reset(limits["seven_day_resets_at"]) if show_resets else ""
            if status_format == "plain":
                segment += (
                    f" 5h:{_fmt_pct(limits['five_hour_pct'])}{five_reset} "
                    f"7d:{_fmt_pct(limits['seven_day_pct'])}{seven_reset}"
                )
            else:
                segment += (
                    f" 5h:{render_progress(limits['five_hour_pct'], color=color)}{five_reset} "
                    f"7d:{render_progress(limits['seven_day_pct'], color=color)}{seven_reset}"
                )
        today = item.get("today")
        if today and ("tokens" in fields or not has_limits):
            segment += f" tok:{_fmt_tokens_compact(today['tokens'])}"
        if today and ("cost" in fields or not has_limits):
            segment += f" cost:${today['cost_usd']:.2f}"
        parts.append(segment)
    return " | ".join(parts) if parts else "No agents detected"


def _show_status(agents, args: list[str]) -> None:
    opts = _parse_status_args(args)
    selected = agents
    if opts["agent"]:
        selected = [a for a in agents if a.id == opts["agent"]]

    fields = opts["fields"]
    items = [_build_status_item(a.id, fields) for a in selected]
    print(_render_status_text(
        items,
        fields,
        opts["format"],
        use_color(opts["color"]),
        opts["resets"],
    ))


def _show_agent_dashboard(agent_id: str):
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


def _show_interactive_dashboard(agents):
    import tty
    import termios
    from io import StringIO
    from rich.console import Console as RichConsole
    import src.ui.tables as _tables

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
        if command in ("status", "codex-status"):
            print("No agents detected")
        else:
            console.print("[red]未检测到任何 AI Agent[/red]")
        sys.exit(1)

    agent_ids = {a.id for a in agents}

    if command == "status":
        _show_status(agents, args[1:])
        return

    if command == "codex-status":
        _show_status(agents, ["codex", "--resets"] + args[1:])
        return

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
        console.print("[dim]可用命令: dashboard, status, codex-status, daily, weekly, monthly, sessions, blocks, claude, codex, setup, unsetup[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
