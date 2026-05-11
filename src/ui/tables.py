import os
from collections import defaultdict
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from ..adapters.types import DailyStats, MonthlyStats, P90Limits, RateLimits, SessionBlock, SessionStats, WeeklyStats

console = Console()


def _is_light_theme() -> bool:
    theme = os.environ.get("TT_THEME", "").lower()
    if theme == "light":
        return True
    if theme == "dark":
        return False
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        try:
            return int(parts[-1]) > 8
        except (ValueError, IndexError):
            pass
    return False


class _S:
    """语义化样式，根据终端主题自动切换"""
    light = _is_light_theme()
    dim = "grey50" if light else "dim"
    token = "dark_cyan" if light else "dim cyan"
    token_bold = "bold dark_cyan" if light else "bold cyan"
    cost = "rgb(180,130,0)" if light else "dim yellow"
    cost_bold = "bold rgb(180,130,0)" if light else "bold yellow"
    accent = "bold dark_green" if light else "bold green"
    bar_low = "dark_green" if light else "green"
    bar_mid = "rgb(200,150,0)" if light else "yellow"
    bar_high = "red"
    good = "dark_green" if light else "green"
    warn = "rgb(200,150,0)" if light else "yellow"
    bad = "red"


def _width_mode() -> str:
    w = console.width
    if w < 100:
        return "compact"
    if w < 120:
        return "medium"
    return "wide"


AGENT_SHORT = {"claude-code": "CC", "codex": "Codex"}

AGENT_LABEL = {"claude-code": "Claude Code", "codex": "Codex"}


def _is_multi_agent(stats) -> bool:
    return len(set(s.agent_id for s in stats if s.agent_id)) > 1


def _group_by_agent(stats) -> dict[str, list]:
    by_agent: dict[str, list] = defaultdict(list)
    for s in stats:
        by_agent[s.agent_id].append(s)
    return by_agent


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


def _display_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x7F else 1
    return w


def _append_bar(lines: Text, label: str, pct: float,
                bar_width: int, suffix: str = "") -> None:
    filled = int(pct / 100 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    bar_style = _S.bar_high if pct > 80 else _S.bar_mid if pct > 50 else _S.bar_low
    lines.append(label, style=_S.dim)
    lines.append(bar, style=bar_style)
    lines.append(f"  {pct:.0f}%", style=bar_style)
    if suffix:
        lines.append(suffix, style=_S.dim)
    lines.append("\n")


def _append_trend(lines: Text, current: float, previous: float) -> None:
    arrow = "↑" if current >= previous else "↓"
    style = _S.bad if current >= previous else _S.good
    lines.append(f"{arrow}", style=style)


def _token_heat_style(ratio: float) -> str:
    if ratio > 0.8:
        return f"bold {_S.bad}"
    if ratio > 0.5:
        return f"bold {_S.warn}"
    return "bold"


def _pct_style(pct: float) -> str:
    return _S.bar_high if pct > 80 else _S.bar_mid if pct > 50 else _S.bar_low


def _render_rate_bar(lines: Text, label: str, pct: float,
                     resets_at: int | None, bar_width: int,
                     date_fmt: str = "%H:%M") -> None:
    reset_suffix = ""
    if resets_at:
        reset_dt = datetime.fromtimestamp(resets_at, tz=timezone.utc)
        reset_suffix = f"  重置于 {reset_dt.strftime(date_fmt)}"
    _append_bar(lines, f"  {label}    ", pct, bar_width, reset_suffix)


def _render_week_section(lines: Text, week: WeeklyStats,
                         last_week: WeeklyStats | None = None) -> None:
    now = datetime.now(timezone.utc)
    elapsed_days = now.weekday() + 1
    daily_avg_cost = week.cost_usd / elapsed_days if elapsed_days > 0 else 0
    lines.append(f"  Token     {_fmt_tokens(week.total_tokens)}", style=_S.token)
    if last_week:
        _append_trend(lines, week.total_tokens, last_week.total_tokens)
    lines.append(f"  Output: {_fmt_tokens(week.output_tokens)}", style=_S.dim)
    lines.append(f"  速率: {_fmt_tokens(week.total_tokens // elapsed_days)}/天\n", style=_S.dim)
    lines.append(f"  等效成本  {_fmt_cost(week.cost_usd)}", style=_S.cost)
    lines.append(f"  日均: {_fmt_cost(daily_avg_cost)}", style=_S.dim)
    lines.append("\n")
    lines.append(f"  消息      {week.message_count} 条  会话: {week.session_count}", style=_S.dim)


def render_tab_bar(agent_names: list[str], current: int) -> None:
    line = Text()
    line.append("  ")
    for i, name in enumerate(agent_names):
        if i > 0:
            line.append(" │ ", style=_S.dim)
        if i == current:
            line.append(f" {name} ", style="bold reverse")
        else:
            line.append(f" {name} ", style=_S.dim)
    line.append("          ← → 切换  q / ESC 退出", style=_S.dim)
    console.print(line)


def _project_short(project: str) -> str:
    return project if project else "unknown"


def _render_header(agents: list[str], total_tokens: int, total_cost: float,
                   total_sessions: int, total_messages: int, days: int) -> None:
    agent_text = " ".join(f"[{_S.good}]●[/{_S.good}] {a}" for a in agents)
    console.print()
    console.print(Panel(
        f"[bold]Token Tracker[/bold]  {agent_text}",
        border_style="blue",
        padding=(0, 1),
    ))

    lines = Text()
    lines.append("历史总览", style="bold")
    lines.append(f"  Token: ", style=_S.dim)
    lines.append(f"{_fmt_tokens(total_tokens)}", style=_S.token_bold)
    lines.append(f"  等效成本: ", style=_S.dim)
    lines.append(f"{_fmt_cost(total_cost)}", style=_S.cost_bold)
    lines.append(f"  会话: ", style=_S.dim)
    lines.append(f"{total_sessions}", style="bold")
    lines.append(f"  消息: ", style=_S.dim)
    lines.append(f"{total_messages}", style="bold")
    lines.append(f"  天数: ", style=_S.dim)
    lines.append(f"{days}", style=_S.accent)
    console.print(lines)


def _render_agent_summaries(stats_list, multi_agent: bool) -> None:
    if not multi_agent:
        return
    by_agent: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "sessions": 0, "messages": 0})
    for s in stats_list:
        if not s.agent_id:
            continue
        a = by_agent[s.agent_id]
        a["tokens"] += s.total_tokens
        a["cost"] += s.cost_usd
        a["sessions"] += s.session_count
        a["messages"] += s.message_count
    if len(by_agent) < 2:
        return
    for agent_id, d in sorted(by_agent.items()):
        lines = Text()
        label = AGENT_LABEL.get(agent_id, agent_id)
        lines.append(f"{label}", style="bold")
        lines.append(f"  Token: ", style=_S.dim)
        lines.append(f"{_fmt_tokens(d['tokens'])}", style=_S.token_bold)
        lines.append(f"  等效成本: ", style=_S.dim)
        lines.append(f"{_fmt_cost(d['cost'])}", style=_S.cost_bold)
        lines.append(f"  会话: ", style=_S.dim)
        lines.append(f"{d['sessions']}", style="bold")
        lines.append(f"  消息: ", style=_S.dim)
        lines.append(f"{d['messages']}", style="bold")
        console.print(lines)


def render_dashboard(
    daily_stats: list[DailyStats],
    weekly_stats: list[WeeklyStats],
    monthly_stats: list[MonthlyStats],
    sessions: list[SessionStats],
    blocks: list[SessionBlock],
    rate_limits: RateLimits | None = None,
    p90: P90Limits | None = None,
    agents: list[str] | None = None,
) -> None:
    if not daily_stats:
        console.print(f"[{_S.warn}]暂无数据[/{_S.warn}]")
        return

    total_tokens = sum(s.total_tokens for s in daily_stats)
    total_cost = sum(s.cost_usd for s in daily_stats)
    total_msgs = sum(s.message_count for s in daily_stats)
    total_sessions = sum(s.session_count for s in daily_stats)

    _render_header(agents or ["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, len(daily_stats))

    # --- 本月概览 ---
    if monthly_stats:
        last_month = monthly_stats[-2] if len(monthly_stats) >= 2 else None
        _render_month_overview(monthly_stats[-1], last_month)

    # --- 数据面板 ---
    cur_week = weekly_stats[-1] if weekly_stats else None
    last_week = weekly_stats[-2] if len(weekly_stats) >= 2 else None

    has_limits = rate_limits and (rate_limits.five_hour_pct is not None or rate_limits.seven_day_pct is not None)
    if p90 and not has_limits:
        today = daily_stats[-1] if daily_stats else None
        yesterday = daily_stats[-2] if len(daily_stats) >= 2 else None
        if today:
            _render_daily_panel(today, yesterday, p90, cur_week, last_week)
    else:
        active_blocks = [b for b in blocks if not b.is_gap and b.is_active]
        finished_blocks = [b for b in blocks if not b.is_gap and not b.is_active]
        last_block = finished_blocks[-1] if finished_blocks else None
        if active_blocks:
            for b in active_blocks:
                _render_active_block(b, rate_limits, cur_week, last_block, last_week)
        elif rate_limits:
            _render_idle_panel(rate_limits, cur_week, last_week)

    # --- 最近十条会话 ---
    if sessions:
        _render_recent_sessions(sessions[:10])

    console.print()


def _render_month_overview(month: MonthlyStats, last_month: MonthlyStats | None = None) -> None:
    now = datetime.now(timezone.utc)
    elapsed_days = now.day
    daily_avg_cost = month.cost_usd / elapsed_days if elapsed_days > 0 else 0

    lines = Text()
    lines.append("本月概览", style="bold")

    lines.append(f"  Token: ", style=_S.dim)
    lines.append(f"{_fmt_tokens(month.total_tokens)}", style=_S.token_bold)
    if last_month:
        _append_trend(lines, month.total_tokens, last_month.total_tokens)

    lines.append(f"  等效成本: ", style=_S.dim)
    lines.append(f"{_fmt_cost(month.cost_usd)}", style=_S.cost_bold)

    lines.append(f"  会话: ", style=_S.dim)
    lines.append(f"{month.session_count}", style="bold")
    lines.append(f"  消息: ", style=_S.dim)
    lines.append(f"{month.message_count}", style="bold")
    lines.append(f"  日均: ", style=_S.dim)
    lines.append(f"{_fmt_cost(daily_avg_cost)}", style=_S.cost)

    console.print(lines)


def _render_recent_sessions(stats: list[SessionStats]) -> None:
    multi_agent = _is_multi_agent(stats)
    mode = _width_mode()
    table = Table(
        title="最近十条会话",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("时间", style=_S.token, no_wrap=True)
    if multi_agent:
        table.add_column("来源", no_wrap=True)
    table.add_column("项目", no_wrap=True, max_width=14)
    if mode != "compact":
        table.add_column("模型", style=_S.cost, no_wrap=True)
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("总Token", justify="right", style=_S.token_bold)
    table.add_column("等效成本", justify="right", style=_S.good)
    table.add_column("消息", justify="right", style=_S.dim)

    for s in stats:
        row: list = [s.start_time.strftime("%m-%d %H:%M")]
        if multi_agent:
            row.append(AGENT_SHORT.get(s.agent_id, s.agent_id))
        row.append(_project_short(s.project))
        if mode != "compact":
            row.append(_model_short(s.model))
        row += [
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            Text(_fmt_tokens(s.total_tokens), style=_S.token_bold),
            _fmt_cost(s.cost_usd),
            str(s.message_count),
        ]
        table.add_row(*row)

    console.print(table)


def render_daily(stats: list[DailyStats], agents: list[str] | None = None) -> None:
    if not stats:
        console.print(f"[{_S.warn}]暂无数据[/{_S.warn}]")
        return

    multi_agent = _is_multi_agent(stats)
    dates = set(s.date for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_msgs = sum(s.message_count for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(agents or ["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, len(dates))
    _render_agent_summaries(stats, multi_agent)

    mode = _width_mode()
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1), expand=True)
    table.add_column("日期", style=_S.token, no_wrap=True)
    if multi_agent:
        table.add_column("来源", no_wrap=True)
    if mode != "compact":
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
    if mode == "wide":
        table.add_column("Cache", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("等效成本", justify="right", style=_S.good)
    table.add_column("会话", justify="right", style=_S.dim)
    table.add_column("消息", justify="right", style=_S.dim)

    max_tokens = max(s.total_tokens for s in stats) if stats else 1

    for s in stats:
        cache_total = s.cache_creation_tokens + s.cache_read_tokens
        row: list = [s.date]
        if multi_agent:
            row.append(AGENT_SHORT.get(s.agent_id, s.agent_id))
        if mode != "compact":
            row += [_fmt_tokens(s.input_tokens), _fmt_tokens(s.output_tokens)]
        if mode == "wide":
            row.append(_fmt_tokens(cache_total))
        row += [
            Text(_fmt_tokens(s.total_tokens), style=_token_heat_style(s.total_tokens / max_tokens)),
            _fmt_cost(s.cost_usd),
            str(s.session_count),
            str(s.message_count),
        ]
        table.add_row(*row)

    console.print(table)
    console.print()


def _render_weekly_table(stats: list[WeeklyStats], title: str | None = None) -> None:
    mode = _width_mode()
    table = Table(
        title=title, title_style="bold", box=box.SIMPLE_HEAVY,
        header_style="bold", padding=(0, 1), expand=True,
    )
    table.add_column("周", style=_S.token, no_wrap=True)
    if mode != "compact":
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
    if mode == "wide":
        table.add_column("Cache", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("等效成本", justify="right", style=_S.good)
    table.add_column("会话", justify="right", style=_S.dim)
    table.add_column("消息", justify="right", style=_S.dim)

    max_tokens = max(s.total_tokens for s in stats) if stats else 1

    for s in stats:
        cache_total = s.cache_creation_tokens + s.cache_read_tokens
        week_label = f"{s.week_start} ~ {s.week_end}"
        row: list = [week_label]
        if mode != "compact":
            row += [_fmt_tokens(s.input_tokens), _fmt_tokens(s.output_tokens)]
        if mode == "wide":
            row.append(_fmt_tokens(cache_total))
        row += [
            Text(_fmt_tokens(s.total_tokens), style=_token_heat_style(s.total_tokens / max_tokens)),
            _fmt_cost(s.cost_usd),
            str(s.session_count),
            str(s.message_count),
        ]
        table.add_row(*row)

    table.add_section()
    total_row: list = ["[bold]合计[/bold]"]
    if mode != "compact":
        total_row += [
            _fmt_tokens(sum(s.input_tokens for s in stats)),
            _fmt_tokens(sum(s.output_tokens for s in stats)),
        ]
    if mode == "wide":
        total_row.append(_fmt_tokens(sum(s.cache_creation_tokens + s.cache_read_tokens for s in stats)))
    total_row += [
        f"[{_S.token_bold}]{_fmt_tokens(sum(s.total_tokens for s in stats))}[/{_S.token_bold}]",
        f"[{_S.cost_bold}]{_fmt_cost(sum(s.cost_usd for s in stats))}[/{_S.cost_bold}]",
        str(sum(s.session_count for s in stats)),
        str(sum(s.message_count for s in stats)),
    ]
    table.add_row(*total_row)

    console.print(table)


def render_weekly(stats: list[WeeklyStats], agents: list[str] | None = None) -> None:
    if not stats:
        console.print(f"[{_S.warn}]暂无数据[/{_S.warn}]")
        return

    multi_agent = _is_multi_agent(stats)
    weeks = set(s.week for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_msgs = sum(s.message_count for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(agents or ["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, len(weeks) * 7)

    if multi_agent:
        for agent_id, group in sorted(_group_by_agent(stats).items()):
            _render_weekly_table(group, title=AGENT_LABEL.get(agent_id, agent_id))
    else:
        _render_weekly_table(stats)

    console.print()


def _render_monthly_table(stats: list[MonthlyStats], title: str | None = None) -> None:
    mode = _width_mode()
    table = Table(
        title=title, title_style="bold", box=box.SIMPLE_HEAVY,
        header_style="bold", padding=(0, 1), expand=True,
    )
    table.add_column("月份", style=_S.token, no_wrap=True)
    if mode != "compact":
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
    if mode == "wide":
        table.add_column("Cache创建", justify="right")
        table.add_column("Cache读取", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("等效成本", justify="right", style=_S.good)
    table.add_column("会话", justify="right", style=_S.dim)
    table.add_column("消息", justify="right", style=_S.dim)

    for s in stats:
        row: list = [s.month]
        if mode != "compact":
            row += [_fmt_tokens(s.input_tokens), _fmt_tokens(s.output_tokens)]
        if mode == "wide":
            row += [_fmt_tokens(s.cache_creation_tokens), _fmt_tokens(s.cache_read_tokens)]
        row += [
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
            str(s.session_count),
            str(s.message_count),
        ]
        table.add_row(*row)

    table.add_section()
    total_row: list = ["[bold]合计[/bold]"]
    if mode != "compact":
        total_row += [
            _fmt_tokens(sum(s.input_tokens for s in stats)),
            _fmt_tokens(sum(s.output_tokens for s in stats)),
        ]
    if mode == "wide":
        total_row += [
            _fmt_tokens(sum(s.cache_creation_tokens for s in stats)),
            _fmt_tokens(sum(s.cache_read_tokens for s in stats)),
        ]
    total_row += [
        f"[{_S.token_bold}]{_fmt_tokens(sum(s.total_tokens for s in stats))}[/{_S.token_bold}]",
        f"[{_S.cost_bold}]{_fmt_cost(sum(s.cost_usd for s in stats))}[/{_S.cost_bold}]",
        str(sum(s.session_count for s in stats)),
        str(sum(s.message_count for s in stats)),
    ]
    table.add_row(*total_row)

    console.print(table)


def render_monthly(stats: list[MonthlyStats], agents: list[str] | None = None) -> None:
    if not stats:
        console.print(f"[{_S.warn}]暂无数据[/{_S.warn}]")
        return

    multi_agent = _is_multi_agent(stats)
    months = set(s.month for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_msgs = sum(s.message_count for s in stats)
    total_sessions = sum(s.session_count for s in stats)
    days = len(months) * 30

    _render_header(agents or ["Claude Code"], total_tokens, total_cost, total_sessions, total_msgs, days)

    if multi_agent:
        for agent_id, group in sorted(_group_by_agent(stats).items()):
            _render_monthly_table(group, title=AGENT_LABEL.get(agent_id, agent_id))
    else:
        _render_monthly_table(stats)

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
    table.add_column("模型", style=_S.cost, no_wrap=True)
    table.add_column("Token", justify="right")
    table.add_column("占比", justify="right")
    table.add_column("", min_width=20)

    for model, tokens in sorted_models[:8]:
        pct = tokens / total * 100 if total > 0 else 0
        bar_width = int(pct / 100 * 20)
        bar_text = "█" * bar_width + "░" * (20 - bar_width)

        if pct > 50:
            bar_style = _S.token_bold
        elif pct > 20:
            bar_style = "blue"
        else:
            bar_style = _S.dim

        table.add_row(
            _model_short(model),
            _fmt_tokens(tokens),
            f"{pct:.1f}%",
            Text(bar_text, style=bar_style),
        )

    console.print(table)


def render_sessions(stats: list[SessionStats], limit: int = 20) -> None:
    if not stats:
        console.print(f"[{_S.warn}]暂无数据[/{_S.warn}]")
        return

    multi_agent = _is_multi_agent(stats)
    shown = stats[:limit]
    total_tokens = sum(s.total_tokens for s in shown)
    total_cost = sum(s.cost_usd for s in shown)

    console.print()
    console.print(Panel(
        f"[bold]Token Tracker[/bold]  最近 {len(shown)} / {len(stats)} 个会话  "
        f"Token: [{_S.token_bold}]{_fmt_tokens(total_tokens)}[/{_S.token_bold}]  "
        f"等效成本: [{_S.cost_bold}]{_fmt_cost(total_cost)}[/{_S.cost_bold}]",
        border_style="blue",
        padding=(0, 1),
    ))

    mode = _width_mode()
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", padding=(0, 1))
    table.add_column("时间", style=_S.token, no_wrap=True)
    if multi_agent:
        table.add_column("来源", no_wrap=True)
    table.add_column("项目", no_wrap=True, max_width=14)
    if mode != "compact":
        table.add_column("模型", style=_S.cost, no_wrap=True)
        table.add_column("时长", justify="right")
    if mode == "wide":
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
    table.add_column("总Token", justify="right", style="bold")
    table.add_column("等效成本", justify="right", style=_S.good)
    table.add_column("消息", justify="right", style=_S.dim)

    max_tokens = max(s.total_tokens for s in shown) if shown else 1

    for s in shown:
        row: list = [s.start_time.strftime("%m-%d %H:%M")]
        if multi_agent:
            row.append(AGENT_SHORT.get(s.agent_id, s.agent_id))
        row.append(_project_short(s.project))
        if mode != "compact":
            row += [_model_short(s.model), _fmt_duration(s.duration_minutes)]
        if mode == "wide":
            row += [_fmt_tokens(s.input_tokens), _fmt_tokens(s.output_tokens)]
        row += [
            Text(_fmt_tokens(s.total_tokens), style=_token_heat_style(s.total_tokens / max_tokens)),
            _fmt_cost(s.cost_usd),
            str(s.message_count),
        ]
        table.add_row(*row)

    console.print(table)
    console.print()


def _render_daily_panel(
    today: DailyStats,
    yesterday: DailyStats | None,
    p90: P90Limits,
    week: WeeklyStats | None = None,
    last_week: WeeklyStats | None = None,
) -> None:
    bar_width = 20 if _width_mode() == "compact" else 30
    lines = Text()
    lines.append("当日数据面板 (P90)\n\n", style="bold")

    p90_items = [
        ("Token Usage", today.total_tokens, p90.token_limit, _fmt_tokens),
        ("Cost Usage", today.cost_usd, p90.cost_limit, _fmt_cost),
        ("Msg Usage", today.message_count, p90.message_limit, lambda x: f"{x} 条"),
    ]
    max_pct = 0.0
    for label, current, limit, unit_fmt in p90_items:
        pct = min(current / limit * 100, 100) if limit > 0 else 0
        max_pct = max(max_pct, pct)
        display_label = f"  {label}" + " " * (14 - _display_width(label))
        suffix = f"  {unit_fmt(current)} / {unit_fmt(limit)}"
        _append_bar(lines, display_label, pct, bar_width, suffix)
        lines.append("\n")

    lines.append(f"  Token     {_fmt_tokens(today.total_tokens)}", style=_S.token)
    if yesterday:
        _append_trend(lines, today.total_tokens, yesterday.total_tokens)
    lines.append(f"  Output: {_fmt_tokens(today.output_tokens)}", style=_S.dim)
    lines.append(f"  Cache: {_fmt_tokens(today.cache_creation_tokens + today.cache_read_tokens)}\n", style=_S.dim)
    lines.append(f"  等效成本  {_fmt_cost(today.cost_usd)}", style=_S.cost)
    if yesterday:
        _append_trend(lines, today.cost_usd, yesterday.cost_usd)
    lines.append(f"  会话: {today.session_count}  消息: {today.message_count}", style=_S.dim)
    if today.message_count > 0:
        tokens_per_msg = today.total_tokens // today.message_count
        lines.append(f"  速率: {_fmt_tokens(tokens_per_msg)}/条", style=_S.dim)

    if week:
        now = datetime.now(timezone.utc)
        elapsed_days = now.weekday() + 1
        daily_avg_cost = week.cost_usd / elapsed_days if elapsed_days > 0 else 0

        lines.append(f"\n\n  本周 Token {_fmt_tokens(week.total_tokens)}", style=_S.token)
        if last_week:
            _append_trend(lines, week.total_tokens, last_week.total_tokens)
        lines.append(f"  Output: {_fmt_tokens(week.output_tokens)}", style=_S.dim)
        lines.append(f"  速率: {_fmt_tokens(week.total_tokens // elapsed_days)}/天\n", style=_S.dim)
        lines.append(f"  本周成本  {_fmt_cost(week.cost_usd)}", style=_S.cost)
        if last_week:
            _append_trend(lines, week.cost_usd, last_week.cost_usd)
        lines.append(f"  日均: {_fmt_cost(daily_avg_cost)}", style=_S.dim)
        lines.append(f"  会话: {week.session_count}  消息: {week.message_count}", style=_S.dim)

    lines.append("\n")

    console.print(Panel(lines, border_style=_pct_style(max_pct), padding=(0, 1)))


def _render_active_block(
    b: SessionBlock,
    rate_limits: RateLimits | None = None,
    week: WeeklyStats | None = None,
    last_block: SessionBlock | None = None,
    last_week: WeeklyStats | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    elapsed = (now - b.start_time).total_seconds()
    remaining = (b.end_time - now).total_seconds()

    elapsed_min = int(elapsed / 60)
    remaining_min = int(remaining / 60)
    remaining_h = remaining_min // 60
    remaining_m = remaining_min % 60

    bar_width = 20 if _width_mode() == "compact" else 30

    lines = Text()
    lines.append("当前 5h&7d 数据面板\n\n", style="bold")

    if rate_limits and rate_limits.five_hour_pct is not None:
        _render_rate_bar(lines, "5h 限额", rate_limits.five_hour_pct,
                         rate_limits.five_hour_resets_at, bar_width)

    lines.append(f"  时间      ", style=_S.dim)
    lines.append(f"已用 {elapsed_min}min / 剩余 {remaining_h}h{remaining_m:02d}m\n", style=_S.dim)

    lines.append(f"  Token     {_fmt_tokens(b.total_tokens)}", style=_S.token)
    if last_block:
        _append_trend(lines, b.total_tokens, last_block.total_tokens)
    lines.append(f"  Output: {_fmt_tokens(b.output_tokens)}", style=_S.dim)
    lines.append(f"  速率: {_fmt_tokens(int(b.burn_rate))}/min\n", style=_S.dim)
    lines.append(f"  等效成本  {_fmt_cost(b.cost_usd)}", style=_S.cost)
    if rate_limits and rate_limits.model:
        lines.append(f"  模型: {rate_limits.model}", style=_S.dim)
    lines.append("\n")
    lines.append(f"  消息      {len(b.entries)} 条", style=_S.dim)

    if rate_limits and rate_limits.seven_day_pct is not None:
        lines.append("\n\n")
        _render_rate_bar(lines, "7d 限额", rate_limits.seven_day_pct,
                         rate_limits.seven_day_resets_at, bar_width, "%m-%d %H:%M")
        if week:
            _render_week_section(lines, week, last_week)

    lines.append("\n")

    pct = rate_limits.five_hour_pct if rate_limits and rate_limits.five_hour_pct is not None else 0
    console.print(Panel(lines, border_style=_pct_style(pct), padding=(0, 1)))


def _render_idle_panel(
    rate_limits: RateLimits,
    week: WeeklyStats | None = None,
    last_week: WeeklyStats | None = None,
) -> None:
    bar_width = 20 if _width_mode() == "compact" else 30
    lines = Text()
    lines.append("限额数据面板\n\n", style="bold")

    if rate_limits.five_hour_pct is not None:
        _render_rate_bar(lines, "5h 限额", rate_limits.five_hour_pct,
                         rate_limits.five_hour_resets_at, bar_width)

    if rate_limits.seven_day_pct is not None:
        if rate_limits.five_hour_pct is not None:
            lines.append("\n")
        _render_rate_bar(lines, "7d 限额", rate_limits.seven_day_pct,
                         rate_limits.seven_day_resets_at, bar_width, "%m-%d %H:%M")
        if week:
            _render_week_section(lines, week, last_week)

    if rate_limits.model:
        lines.append(f"\n  模型: {rate_limits.model}", style=_S.dim)

    lines.append("\n")

    max_pct = max(rate_limits.five_hour_pct or 0, rate_limits.seven_day_pct or 0)
    console.print(Panel(lines, border_style=_pct_style(max_pct), padding=(0, 1)))
