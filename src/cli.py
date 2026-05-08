import sys

from .adapters import claude
from .adapters.rate_limits import load_rate_limits
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.blocks import analyze_blocks
from .hooks import is_setup, setup, unsetup
from .ui.tables import (
    console, render_blocks, render_daily, render_dashboard,
    render_monthly, render_sessions, render_weekly,
)


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

    console.print(f"[dim]检测到: {', '.join(a.name + ' ✓' for a in agents)}[/dim]")

    entries = claude.load_entries()
    if not entries:
        console.print("[yellow]暂无 token 使用数据[/yellow]")
        sys.exit(0)

    if command == "dashboard":
        if not is_setup():
            console.print("[dim]首次运行，自动配置 statusLine hook...[/dim]")
            setup()
            console.print()
        daily = aggregate_daily(entries)
        weekly = aggregate_weekly(entries)
        monthly = aggregate_monthly(entries)
        sessions = aggregate_sessions(entries)
        recent = claude.load_entries(hours_back=48)
        blocks = analyze_blocks(recent)
        rate_limits = load_rate_limits()
        render_dashboard(daily, weekly, monthly, sessions, blocks, rate_limits)
    elif command == "daily":
        stats = aggregate_daily(entries)
        render_daily(stats)
    elif command == "weekly":
        stats = aggregate_weekly(entries)
        render_weekly(stats)
    elif command == "monthly":
        stats = aggregate_monthly(entries)
        render_monthly(stats)
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
        recent = claude.load_entries(hours_back=hours)
        blocks = analyze_blocks(recent)
        render_blocks(blocks)
    else:
        console.print(f"[red]未知命令: {command}[/red]")
        console.print("[dim]可用命令: dashboard, daily, weekly, monthly, sessions, blocks, setup, unsetup[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
