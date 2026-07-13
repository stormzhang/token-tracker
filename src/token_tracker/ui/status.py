"""tt status 面板：当天合并概览 + 额度/per-agent 统计 + 合并 session 列表。

配色跟随当前主题（`_S` 运行时代理）；头图仿 daily 品牌面板，额度条仿 weekly trend 样式。
"""

from datetime import UTC, datetime

from rich import box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .. import config
from ..i18n import t
from ..tz import system_tz
from .console import forced_color_console, get_console
from .format import (
    AGENT_LABEL,
    AGENT_SHORT,
    _fmt_cost,
    _fmt_tokens,
    _model_short,
    _project_short,
    _width_mode,
    brand_line,
    emit_metrics,
)
from .tables import _bar_text
from .theme import _S, _pct_style


def _quota_display(pct: float) -> float:
    if config.quota_display_mode() == config.QUOTA_DISPLAY_REMAINING:
        return max(0.0, min(100.0, 100.0 - pct))
    return pct


def _quota_style(pct: float) -> str:
    shown = _quota_display(pct)
    if config.quota_display_mode() == config.QUOTA_DISPLAY_REMAINING:
        return _S.bar_high if shown < 20 else _S.bar_mid if shown < 50 else _S.bar_low
    return _pct_style(pct)


def render_status(summary, per_agent, rate_limits, sessions, agents) -> None:
    """三段：合并头图概览；有订阅额度→额度条，否则→per-agent 统计；合并 session 列表。"""
    with forced_color_console():
        _render_summary(summary, agents, title="Today",
                        subtitle=datetime.now(system_tz()).strftime("%m-%d %H:%M"))
        if rate_limits:
            _render_limits(rate_limits, per_agent)
        else:
            _render_agent_stats(per_agent)
        if sessions:
            _render_sessions(sessions)
        get_console().print()


def render_sessions_view(summary, sessions, agents) -> None:
    """tt sessions：复用 status 的头图概览 + session 列表两段（无额度段）；
    顶部统计与副标题均以展示出的 session 为主。"""
    with forced_color_console():
        subtitle = None
        if sessions:
            times = [s.start_time.astimezone(system_tz()) for s in sessions]
            subtitle = (f"{min(times).strftime('%m-%d %H:%M')} ~ {max(times).strftime('%m-%d %H:%M')}"
                        f"  ·  {len(sessions)} sessions")
        _render_summary(summary, agents, title="Recent sessions", subtitle=subtitle)
        if sessions:
            _render_sessions(sessions)
            get_console().print(Padding(Text(t("sessions_tips"), style=_S.dim), (0, 0, 0, 3)))
        get_console().print()


def _render_summary(summary, agents: list[str], title: str, subtitle: str | None = None) -> None:
    """头图品牌面板：仿 daily `_render_summary`。title/subtitle 由调用方给
    （status=Today + 日期；sessions=Recent sessions + 时间跨度）。"""
    brand = brand_line(agents)
    avail = max(40, get_console().width - 6)
    body = Text()
    body.append(title, style=f"bold {_S.good}")
    if subtitle:
        body.append(f"  {subtitle}", style=f"dim {_S.good}")
    body.append("\n")
    metrics = [
        ("Tokens", _fmt_tokens(summary.total_tokens)),
        ("Cost", _fmt_cost(summary.cost_usd)),
        ("Sessions", str(summary.session_count)),
        ("Messages", str(summary.message_count)),
    ]
    if summary.models:
        metrics.append(("Top Model", _model_short(max(summary.models, key=lambda m: summary.models[m]))))
    emit_metrics(body, metrics, _S.peach, avail)
    get_console().print(Padding(Panel(Group(brand, Rule(style=f"bold {_S.red}"), body),
                                      expand=False, border_style=_S.blue, padding=(0, 1)),
                                (0, 0, 0, 2), expand=False))
    get_console().print()


def _render_limits(rate_limits: dict, per_agent: dict) -> None:
    """订阅额度：每 agent 一段——头行（当天 Tokens / Cost / Model）+ 5h/7d 进度条。"""
    title = "Rate Limits: Remaining" if config.quota_display_mode() == config.QUOTA_DISPLAY_REMAINING else "Rate Limits"
    blocks: list = [Text(f"[{title}]", style=f"bold {_S.good}")]
    for i, (agent_id, rl) in enumerate(rate_limits.items()):
        if i:  # agent 块之间空一行，避免贴太紧
            blocks.append(Text(""))
        head = Text("  ")
        head.append(AGENT_LABEL.get(agent_id, agent_id), style=f"bold {_S.good}")
        st = per_agent.get(agent_id)
        head.append("    Tokens: ", style=_S.dim)
        head.append(_fmt_tokens(st.total_tokens if st else 0), style=_S.token_bold)
        head.append("  Cost: ", style=_S.dim)
        head.append(_fmt_cost(st.cost_usd if st else 0.0), style=_S.good)
        if rl.model:
            head.append("  Model: ", style=_S.dim)
            head.append(_model_short(rl.model.split(" (")[0]), style=_S.cost)  # CC 名带 (1M context)，去括号
        blocks.append(head)

        table = Table(box=box.SIMPLE, show_header=False, show_edge=False, padding=(0, 1), expand=False)
        table.add_column("", style=_S.good, no_wrap=True)
        table.add_column("", justify="right")
        table.add_column("", min_width=20)
        table.add_column("", justify="right", style=_S.dim)
        now_ts = datetime.now(UTC).timestamp()
        for label, pct, resets in (("5h", rl.five_hour_pct, rl.five_hour_resets_at),
                                   ("7d", rl.seven_day_pct, rl.seven_day_resets_at)):
            if pct is None:
                continue
            # reset 已过期（window 已滚但本次启动没拿到新数据）→ 显示 `reset --`，避免误导
            # 用户「上次旧 reset 时间」当未来时间；保留整行让订阅用户能识别自己是订阅套餐（非 API 计费）。
            if not resets:
                reset_str = ""
            elif resets < now_ts:
                reset_str = "reset --"
            else:
                reset_str = f"reset at {datetime.fromtimestamp(resets, tz=system_tz()).strftime('%H:%M')}"
            shown = _quota_display(pct)
            table.add_row(f"  {label}", f"{shown:.0f}%", _bar_text(shown / 100, _quota_style(pct)), reset_str)
        blocks.append(table)
    get_console().print(Padding(Group(*blocks), (0, 0, 0, 2), expand=False))
    get_console().print()


def _render_agent_stats(per_agent: dict) -> None:
    """都没订阅额度时（API 模式等）：每个 agent 当天的 token/cost/sessions/messages。"""
    rows = [(aid, s) for aid, s in per_agent.items() if s.total_tokens or s.message_count]
    if not rows:
        return
    table = Table(title=Text("[Today by Agent]", style=f"bold {_S.peach}"), title_justify="left",
                  box=box.SIMPLE, header_style="bold", padding=(0, 1), expand=False, border_style=_S.peach)
    table.add_column("Agent", style=_S.peach, no_wrap=True)
    table.add_column("Tokens", justify="right", style=_S.token_bold)
    table.add_column("Cost", justify="right", style=_S.good)
    table.add_column("Sessions", justify="right", style=_S.dim)
    table.add_column("Messages", justify="right", style=_S.dim)
    for agent_id, s in rows:
        table.add_row(AGENT_LABEL.get(agent_id, agent_id), _fmt_tokens(s.total_tokens),
                      _fmt_cost(s.cost_usd), str(s.session_count), str(s.message_count))
    get_console().print(Padding(table, (0, 0, 0, 2), expand=False))
    get_console().print()


def _render_sessions(sessions) -> None:
    """合并 session 列表：按 cost 倒序；Time 蓝 / Project 绿 / Tokens 橙 / Cost 黄；
    Tokens·Cost 各自前三（第一红、二三粉）；列序 Time·Project·Tokens·Cost·Msgs·Model·Agent。"""
    mode = _width_mode()
    table = Table(title=f"{t('recent_sessions')} ({len(sessions)})", box=box.SIMPLE_HEAVY,
                  header_style="bold", padding=(0, 1), expand=False)
    table.add_column(t("col_time"), style=_S.blue, no_wrap=True)
    table.add_column(t("col_project"), style=_S.good, no_wrap=True, max_width=14)
    table.add_column(t("col_total_tokens"), justify="right", style=_S.peach)
    table.add_column(t("col_cost"), justify="right", style=_S.warn)
    table.add_column(t("col_messages"), justify="right")
    if mode != "compact":
        table.add_column(t("col_model"), no_wrap=True)
    table.add_column(t("col_agent"), no_wrap=True)
    # Tokens / Cost 各自前三名突出：第一红、第二三粉（各按本列值排、不依赖行序）
    top_styles = (f"bold {_S.bad}", f"bold {_S.pink}", f"bold {_S.pink}")

    def _top3(key):
        ranked = sorted(range(len(sessions)), key=lambda i: key(sessions[i]), reverse=True)[:3]
        return {i: r for r, i in enumerate(ranked)}

    tok_top, cost_top = _top3(lambda s: s.total_tokens), _top3(lambda s: s.cost_usd)
    for idx, s in enumerate(sessions):
        tok, cost = _fmt_tokens(s.total_tokens), _fmt_cost(s.cost_usd)
        tok_cell = Text(tok, style=top_styles[tok_top[idx]]) if idx in tok_top else tok
        cost_cell = Text(cost, style=top_styles[cost_top[idx]]) if idx in cost_top else cost
        row: list = [
            s.start_time.astimezone(system_tz()).strftime("%m-%d %H:%M"),
            _project_short(s.project),
            tok_cell,
            cost_cell,
            str(s.message_count),
        ]
        if mode != "compact":
            names = list(s.models) or [s.model]
            row.append(_model_short(names[0]))
        row.append(AGENT_SHORT.get(s.agent_id, s.agent_id))
        table.add_row(*row)
    get_console().print(Padding(table, (0, 0, 0, 2), expand=False))
