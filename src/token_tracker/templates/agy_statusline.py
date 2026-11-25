"""Read the native AGY statusline JSON and print session usage and model quotas."""

import json
import sys
from datetime import UTC, datetime

from ..adapters import agy_quota
from ..adapters.util import project_from_cwd


def render(data: dict, now: datetime) -> str:
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
            parts.append(f"Ctx: {used:.1f}%")
    lines = [" | ".join(parts)] if parts else []
    for label, limits in agy_quota.quota_groups(agy_quota.known_buckets(data.get("quota")), now).items():
        windows = []
        for period, pct, resets in (
            ("5h", limits.five_hour_pct, limits.five_hour_resets_at),
            ("7d", limits.seven_day_pct, limits.seven_day_resets_at),
        ):
            if pct is None:
                continue
            limit = f"{period} {pct:.1f}%"
            if resets is not None:
                hours, minutes = divmod(max(0, resets - int(now.timestamp())) // 60, 60)
                limit += f" (reset {hours}h{minutes:02d}m)" if hours else f" (reset {minutes}m)"
            windows.append(limit)
        lines.append(f"{label} used: {' | '.join(windows)}")
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
    print(render(data, datetime.now(UTC)))


if __name__ == "__main__":
    main()
