import sys

from .adapters import claude
from .adapters.registry import detect_agents
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions
from .analyzer.blocks import analyze_blocks
from .ui.tables import console, render_blocks, render_daily, render_monthly, render_sessions


def main():
    args = sys.argv[1:]
    command = args[0] if args else "daily"

    agents = detect_agents()
    if not agents:
        console.print("[red]未检测到任何 AI Agent[/red]")
        sys.exit(1)

    console.print(f"[dim]检测到: {', '.join(a.name + ' ✓' for a in agents)}[/dim]")

    entries = claude.load_entries()
    if not entries:
        console.print("[yellow]暂无 token 使用数据[/yellow]")
        sys.exit(0)

    if command == "daily":
        stats = aggregate_daily(entries)
        render_daily(stats)
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
        console.print("[dim]可用命令: daily, monthly, sessions, blocks[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
