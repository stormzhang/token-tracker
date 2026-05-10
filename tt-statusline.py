#!/usr/bin/env python3
"""Claude Code statusLine — 状态栏显示 + 数据持久化到 tt-status.json"""
import json, os, sys, tempfile
from datetime import datetime, timezone

STATUS_FILE = os.path.expanduser("~/.claude/tt-status.json")
BAR = ("█", "░", 8)
C = {
    "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
    "cyan": "\033[36m", "blue": "\033[34m", "magenta": "\033[35m",
    "peach": "\033[38;5;216m", "dim": "\033[2m", "reset": "\033[0m",
}


def color_by_pct(pct):
    return C["green"] if pct < 50 else C["yellow"] if pct < 80 else C["red"]


def fmt_tokens(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.0f}k"
    return str(n)


def progress_bar(value):
    filled_char, empty_char, width = BAR
    if value is None:
        return empty_char * width + " n/a"
    pct = max(0.0, min(100.0, float(value)))
    filled = round(pct / 100 * width)
    return f"{color_by_pct(pct)}{filled_char * filled}{C['reset']}{empty_char * (width - filled)} {pct:.0f}%"


def save_data(data):
    data["_received_at"] = datetime.now(timezone.utc).isoformat()
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATUS_FILE), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def render(data):
    parts = []

    project = data.get("workspace", {}).get("project_dir", "")
    if project:
        parts.append(f"{C['cyan']}{os.path.basename(project)}{C['reset']}")

    rl = data.get("rate_limits", {})
    has_rl = False
    for key, label in [("five_hour", "5h"), ("seven_day", "7d")]:
        pct = rl.get(key, {}).get("used_percentage")
        if pct is not None:
            has_rl = True
            parts.append(f"{C['blue']}{label}:{C['reset']}{progress_bar(pct)}")

    if not has_rl:
        cost = data.get("cost", {})
        usd = cost.get("total_cost_usd")
        if usd is not None:
            parts.append(f"{C['blue']}Cost:{C['reset']}{C['peach']}${usd:.2f}{C['reset']}")

    ctx = data.get("context_window", {})
    if ctx.get("used_percentage") is not None:
        size = ctx.get("context_window_size", 0)
        parts.append(f"{C['yellow']}{fmt_tokens(size)} CTX: {ctx['used_percentage']:.0f}%{C['reset']}")

    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    cache = ctx.get("current_usage", {}).get("cache_read_input_tokens", 0)
    if total_in or total_out:
        parts.append(f"{C['peach']}Tokens: {fmt_tokens(total_in)}↑ {fmt_tokens(total_out)}↓ cached:{fmt_tokens(cache)}{C['reset']}")

    model_name = data.get("model", {}).get("display_name", "")
    if model_name:
        effort = data.get("effort", {}).get("level", "")
        if effort:
            model_name += f"{C['dim']}/{effort}{C['reset']}"
        parts.append(f"{C['magenta']}{model_name}{C['reset']}")

    if parts:
        print(" | ".join(parts))


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return
    save_data(data)
    render(data)


if __name__ == "__main__":
    main()
