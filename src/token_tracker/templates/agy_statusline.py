"""Read the native AGY statusline JSON and print session usage and model quotas."""

import json
import os
import sys
from datetime import UTC, datetime

from rich.text import Text

from .. import config
from ..adapters import agy_quota
from ..adapters.util import project_from_cwd
from ..ui.themes import theme_to_statusline_ansi


def _progress(pct: float, width: int, colors: dict[str, str]) -> str:
    filled = round(pct / 100 * width)
    level = "bar_ok" if pct < 50 else "bar_warn" if pct < 80 else "bar_danger"
    color, label, reset = colors.get(level, ""), colors.get("label", ""), colors.get("reset", "")
    empty = "░" * (width - filled)
    # Match Claude's shaded track. At zero usage the track uses the terminal foreground.
    if pct > 0 and empty:
        empty = f"{color}{empty}{reset}"
    return f"{color}{'█' * filled}{reset}{empty} {label}{pct:.0f}%{reset}"


def _status_rows(parts: list[str], prefix: str, columns: int) -> list[str]:
    rows = []
    current = ""
    for part in parts:
        candidate = f"{current} | {part}" if current else prefix + part
        if current and Text.from_ansi(candidate).cell_len > columns:
            rows.append(current)
            current = " " * len(prefix) + part
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows


def render(data: dict, now: datetime, colors: dict[str, str] | None = None) -> str:
    colors = colors or {}
    label_color, reset_color = colors.get("label", ""), colors.get("reset", "")
    width = data.get("terminal_width")
    columns = max(1, width - 4) if type(width) is int and width > 0 else 100
    bar_width = 8 if columns >= 100 else 6 if columns >= 60 else 4
    cwd = data.get("cwd")
    model = data.get("model")
    context = data.get("context_window")
    parts = []
    if isinstance(cwd, str) and cwd:
        parts.append(f"[{project_from_cwd(cwd)}]")
    if isinstance(model, dict):
        name = model.get("display_name") or model.get("id")
        if isinstance(name, str) and name:
            parts.append(name)
    if isinstance(context, dict):
        counts = (context.get("total_input_tokens"), context.get("total_output_tokens"))
        if all(type(count) is int and count >= 0 for count in counts):
            parts.append(f"In: {counts[0]:,} · Out: {counts[1]:,}")
        used = context.get("used_percentage")
        if type(used) in (int, float) and 0 <= used <= 100:
            parts.append(f"{label_color}Ctx:{reset_color} {_progress(used, bar_width, colors)}")
    lines = _status_rows(parts, "", columns)
    for label, limits in agy_quota.quota_groups(agy_quota.known_buckets(data.get("quota")), now).items():
        windows = []
        for period, pct, resets in (
            ("5h", limits.five_hour_pct, limits.five_hour_resets_at),
            ("7d", limits.seven_day_pct, limits.seven_day_resets_at),
        ):
            if pct is None:
                continue
            limit = f"{label_color}{period}{reset_color} {_progress(pct, bar_width, colors)}"
            if resets is not None:
                hours, minutes = divmod(max(0, resets - int(now.timestamp())) // 60, 60)
                timer = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
                dim = "\x1b[2m" if colors else ""
                limit += f" {dim}{label_color}(reset {timer}){reset_color}"
            windows.append(limit)
        lines.extend(_status_rows(windows, f"{label} used: ", columns))
    return "\n".join(lines)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    agy_quota.save_snapshot(agy_quota.known_buckets(data.get("quota")))
    colors = None
    if not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb":
        depth = "truecolor" if os.environ.get("COLORTERM") in ("truecolor", "24bit") else "256"
        colors = theme_to_statusline_ansi(config.resolve_theme(), depth)
    print(render(data, datetime.now(UTC), colors))


if __name__ == "__main__":
    main()
