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
    color, reset = colors.get(level, ""), colors.get("reset", "")
    fill = f"{color}{'█' * filled}{reset}" if filled else ""
    return f"{fill}{'░' * (width - filled)} {color}{pct:.1f}%{reset}"


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


def render(data: dict, now: datetime, theme: str | None = None) -> str:
    colors = theme_to_statusline_ansi(theme) if theme else {}
    width = data.get("terminal_width")
    columns = max(1, width - 4) if type(width) is int and width > 0 else 100
    bar_width = 8 if columns >= 96 else 5
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
            parts.append(f"Ctx: {_progress(used, bar_width, colors)}")
    lines = _status_rows(parts, "", columns)
    for label, limits in agy_quota.quota_groups(agy_quota.known_buckets(data.get("quota")), now).items():
        windows = []
        for period, pct, resets in (
            ("5h", limits.five_hour_pct, limits.five_hour_resets_at),
            ("7d", limits.seven_day_pct, limits.seven_day_resets_at),
        ):
            if pct is None:
                continue
            limit = f"{period} {_progress(pct, bar_width, colors)}"
            if resets is not None:
                hours, minutes = divmod(max(0, resets - int(now.timestamp())) // 60, 60)
                limit += f" (reset {hours}h{minutes:02d}m)" if hours else f" (reset {minutes}m)"
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
    theme = None if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb" else config.resolve_theme()
    print(render(data, datetime.now(UTC), theme))


if __name__ == "__main__":
    main()
