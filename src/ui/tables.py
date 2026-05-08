from datetime import datetime, timezone

from rich.bar import Bar
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from ..adapters.types import DailyStats, MonthlyStats, SessionBlock, SessionStats

console = Console()

MODEL_SHORT = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-7": "Opus 4.7",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet": "Sonnet",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-haiku": "Haiku",
}


def _model_short(model: str) -> str:
    if model in MODEL_SHORT:
        return MODEL_SHORT[model]
    if "/" in model:
        return model.split("/")[-1][:16]
    return model[:16]


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(usd: float) -> str:
    if usd >= 100:
        return f"${usd:.0f}"
    if usd >= 1:
        return f"${usd:.2f}"
    if usd > 0:
        return f"${usd:.3f}"
    return "$0"


def _fmt_duration(minutes: float) -> str:
    if minutes >= 60:
        h = int(minutes // 60)
        m = int(minutes % 60)
        return f"{h}h{m:02d}m"
    return f"{int(minutes)}min"


def _project_short(project: str) -> str:
    return project if project else "unknown"


def _make_metric(label: str, value: str, style: str = "bold white") -> Panel:
    content = Text()
    content.append(f"{value}\n", style=style)
    content.append(label, style="dim")
    return Panel(content, expand=True, border_style="dim", padding=(0, 1))


def _render_header(agents: list[str], total_tokens: int, total_cost: float,
                   total_sessions: int, total_messages: int, days: int) -> None:
    agent_text = " ".join(f"[green]●[/green] {a}" for a in agents)
    console.print()
    console.print(Panel(
        f"[bold]Token Tracker[/bold]  {agent_text}",
        border_style="blue",
        padding=(0, 1),
    ))

    metrics = [
        _make_metric("总 Token", _fmt_tokens(total_tokens), "bold cyan"),
        _make_metric("等效成本", _fmt_cost(total_cost), "bold yellow"),
        _make_metric("会话数", str(total_sessions), "bold magenta"),
        _make_metric("消息数", str(total_messages), "bold white"),
        _make_metric("使用天数", f"{days}天", "bold green"),
    ]
    console.print(Columns(metrics, equal=True, expand=True))


def render_daily(stats: list[DailyStats]) -> None:
    if not stats:
        console.print("[yellow]暂无数据[/yellow]")
        return

    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_msgs = sum(s.message_count for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, len(stats))

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("日期", style="cyan", no_wrap=True)
    table.add_column("Output", justify="right")
    table.add_column("Cache", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("成本", justify="right", style="green")
    table.add_column("会话", justify="right", style="dim")
    table.add_column("消息", justify="right", style="dim")

    max_tokens = max(s.total_tokens for s in stats) if stats else 1

    for s in stats:
        ratio = s.total_tokens / max_tokens if max_tokens > 0 else 0
        if ratio > 0.8:
            token_style = "bold red"
        elif ratio > 0.5:
            token_style = "bold yellow"
        else:
            token_style = "bold"

        cache_total = s.cache_creation_tokens + s.cache_read_tokens

        table.add_row(
            s.date,
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(cache_total),
            Text(_fmt_tokens(s.total_tokens), style=token_style),
            _fmt_cost(s.cost_usd),
            str(s.session_count),
            str(s.message_count),
        )

    console.print(table)
    console.print()


def render_monthly(stats: list[MonthlyStats]) -> None:
    if not stats:
        console.print("[yellow]暂无数据[/yellow]")
        return

    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_msgs = sum(s.message_count for s in stats)
    total_sessions = sum(s.session_count for s in stats)
    days = len(set(s.month for s in stats)) * 30

    _render_header(["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, days)

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("月份", style="cyan", no_wrap=True)
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Cache创建", justify="right")
    table.add_column("Cache读取", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("成本", justify="right", style="green")
    table.add_column("会话", justify="right", style="dim")
    table.add_column("消息", justify="right", style="dim")

    for s in stats:
        table.add_row(
            s.month,
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(s.cache_creation_tokens),
            _fmt_tokens(s.cache_read_tokens),
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
            str(s.session_count),
            str(s.message_count),
        )

    table.add_section()
    table.add_row(
        "[bold]合计[/bold]",
        _fmt_tokens(sum(s.input_tokens for s in stats)),
        _fmt_tokens(sum(s.output_tokens for s in stats)),
        _fmt_tokens(sum(s.cache_creation_tokens for s in stats)),
        _fmt_tokens(sum(s.cache_read_tokens for s in stats)),
        f"[bold cyan]{_fmt_tokens(total_tokens)}[/bold cyan]",
        f"[bold yellow]{_fmt_cost(total_cost)}[/bold yellow]",
        str(total_sessions),
        str(total_msgs),
    )

    console.print(table)

    if len(stats) > 1:
        console.print()
        _render_model_breakdown(stats)

    console.print()


def _render_model_breakdown(stats: list[MonthlyStats]) -> None:
    all_models: dict[str, int] = {}
    for s in stats:
        for model, tokens in s.models.items():
            all_models[model] = all_models.get(model, 0) + tokens

    if not all_models:
        return

    total = sum(all_models.values())
    sorted_models = sorted(all_models.items(), key=lambda x: x[1], reverse=True)

    table = Table(
        title="模型分布",
        box=box.SIMPLE,
        header_style="bold",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("模型", style="yellow", no_wrap=True)
    table.add_column("Token", justify="right")
    table.add_column("占比", justify="right")
    table.add_column("", min_width=20)

    for model, tokens in sorted_models[:8]:
        pct = tokens / total * 100 if total > 0 else 0
        bar_width = int(pct / 100 * 20)
        bar_text = "█" * bar_width + "░" * (20 - bar_width)

        if pct > 50:
            bar_style = "cyan"
        elif pct > 20:
            bar_style = "blue"
        else:
            bar_style = "dim"

        table.add_row(
            _model_short(model),
            _fmt_tokens(tokens),
            f"{pct:.1f}%",
            Text(bar_text, style=bar_style),
        )

    console.print(table)


def render_sessions(stats: list[SessionStats], limit: int = 20) -> None:
    if not stats:
        console.print("[yellow]暂无数据[/yellow]")
        return

    shown = stats[:limit]
    total_tokens = sum(s.total_tokens for s in shown)
    total_cost = sum(s.cost_usd for s in shown)

    console.print()
    console.print(Panel(
        f"[bold]Token Tracker[/bold]  最近 {len(shown)} / {len(stats)} 个会话  "
        f"Token: [cyan]{_fmt_tokens(total_tokens)}[/cyan]  "
        f"成本: [yellow]{_fmt_cost(total_cost)}[/yellow]",
        border_style="blue",
        padding=(0, 1),
    ))

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1))
    table.add_column("时间", style="cyan", no_wrap=True)
    table.add_column("项目", no_wrap=True)
    table.add_column("模型", style="yellow", no_wrap=True)
    table.add_column("时长", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("成本", justify="right", style="green")
    table.add_column("消息", justify="right", style="dim")

    max_tokens = max(s.total_tokens for s in shown) if shown else 1

    for s in shown:
        ratio = s.total_tokens / max_tokens if max_tokens > 0 else 0
        if ratio > 0.8:
            token_style = "bold red"
        elif ratio > 0.5:
            token_style = "bold yellow"
        else:
            token_style = "bold"

        table.add_row(
            s.start_time.strftime("%m-%d %H:%M"),
            _project_short(s.project),
            _model_short(s.model),
            _fmt_duration(s.duration_minutes),
            Text(_fmt_tokens(s.total_tokens), style=token_style),
            _fmt_cost(s.cost_usd),
            str(s.message_count),
        )

    console.print(table)
    console.print()


def render_blocks(blocks: list[SessionBlock]) -> None:
    if not blocks:
        console.print("[yellow]暂无数据[/yellow]")
        return

    active_blocks = [b for b in blocks if not b.is_gap]
    if not active_blocks:
        console.print("[yellow]暂无计费块数据[/yellow]")
        return

    active = [b for b in active_blocks if b.is_active]

    console.print()

    if active:
        for b in active:
            _render_active_block(b)

    table = Table(
        title="历史计费块",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("时间段", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("Output", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("成本", justify="right", style="green")
    table.add_column("速率", justify="right")
    table.add_column("消息", justify="right", style="dim")

    for b in active_blocks:
        start = b.start_time.strftime("%m-%d %H:%M")
        end = b.end_time.strftime("%H:%M")
        time_range = f"{start} → {end}"

        if b.is_active:
            status = Text("● 活跃", style="bold green")
            elapsed = (datetime.now(timezone.utc) - b.start_time).total_seconds() / 60
            rate = f"{_fmt_tokens(int(b.burn_rate))}/min" if b.burn_rate > 0 else "-"
        else:
            status = Text("  结束", style="dim")
            rate = "-"

        table.add_row(
            time_range,
            status,
            _fmt_tokens(b.output_tokens),
            _fmt_tokens(b.total_tokens),
            _fmt_cost(b.cost_usd),
            rate,
            str(len(b.entries)),
        )

    console.print(table)
    console.print()


def _render_active_block(b: SessionBlock) -> None:
    now = datetime.now(timezone.utc)
    elapsed = (now - b.start_time).total_seconds()
    remaining = (b.end_time - now).total_seconds()
    progress = elapsed / (5 * 3600)

    elapsed_min = int(elapsed / 60)
    remaining_min = int(remaining / 60)
    remaining_h = remaining_min // 60
    remaining_m = remaining_min % 60

    bar_width = 30
    filled = int(progress * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)

    if progress > 0.8:
        bar_style = "red"
    elif progress > 0.5:
        bar_style = "yellow"
    else:
        bar_style = "green"

    projected = int(b.total_tokens / progress) if progress > 0 else 0

    lines = Text()
    lines.append("当前活跃计费块\n\n", style="bold")
    lines.append(f"  时间  ", style="dim")
    lines.append(bar, style=bar_style)
    lines.append(f"  {int(progress * 100)}%\n")
    lines.append(f"         已用 {elapsed_min}min / 剩余 {remaining_h}h{remaining_m:02d}m\n\n", style="dim")
    lines.append(f"  Token ", style="dim")
    lines.append(f"{_fmt_tokens(b.total_tokens)}", style="bold cyan")
    lines.append(f"  Output: {_fmt_tokens(b.output_tokens)}", style="dim")
    lines.append(f"  速率: {_fmt_tokens(int(b.burn_rate))}/min\n", style="dim")
    lines.append(f"  成本  ", style="dim")
    lines.append(f"{_fmt_cost(b.cost_usd)}", style="bold yellow")
    lines.append(f"  预计本窗口: {_fmt_cost(b.cost_usd / progress if progress > 0 else 0)}\n", style="dim")
    lines.append(f"  消息  ", style="dim")
    lines.append(f"{len(b.entries)} 条\n", style="bold")

    console.print(Panel(lines, border_style=bar_style, padding=(0, 1)))
