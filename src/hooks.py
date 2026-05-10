import json
import os
import stat

from .ui.tables import console

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_SCRIPT_PATH = os.path.expanduser("~/.claude/tt-statusline.py")
BACKUP_KEY = "tokenTracker"
PREVIOUS_STATUSLINE_KEY = "previousStatusLine"

HOOK_SCRIPT = r'''#!/usr/bin/env python3
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
'''


def is_setup() -> bool:
    if not os.path.exists(CLAUDE_SETTINGS):
        return False
    try:
        with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)
        cmd = settings.get("statusLine", {}).get("command", "")
        return "tt-statusline" in cmd and os.path.exists(HOOK_SCRIPT_PATH)
    except (json.JSONDecodeError, OSError):
        return False


def _is_token_tracker_statusline(status_line: dict | None) -> bool:
    if not isinstance(status_line, dict):
        return False
    return "tt-statusline" in (status_line.get("command") or "")


def setup() -> None:
    with open(HOOK_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(HOOK_SCRIPT)
    st = os.stat(HOOK_SCRIPT_PATH)
    os.chmod(HOOK_SCRIPT_PATH, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    settings: dict = {}
    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)

    existing = settings.get("statusLine")
    if existing:
        cmd = existing.get("command", "")
        if "tt-statusline" not in cmd:
            console.print(f"[yellow]检测到已有 statusLine 配置: {cmd}[/yellow]")
            console.print("[yellow]将替换为 statusLine hook，并备份原配置用于 unsetup 恢复[/yellow]")
            backup = settings.setdefault(BACKUP_KEY, {})
            backup[PREVIOUS_STATUSLINE_KEY] = existing

    settings["statusLine"] = {
        "type": "command",
        "command": HOOK_SCRIPT_PATH,
    }

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    console.print(f"[green]✓[/green] Hook 脚本已写入: {HOOK_SCRIPT_PATH}")
    console.print(f"[green]✓[/green] 已注册到: {CLAUDE_SETTINGS}")
    console.print()
    console.print("[dim]重启 Claude Code 后生效，statusLine 数据将自动采集到:[/dim]")
    console.print(f"[dim]  ~/.claude/tt-status.json[/dim]")


def unsetup() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
        console.print(f"[green]✓[/green] 已删除: {HOOK_SCRIPT_PATH}")

    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)

        sl = settings.get("statusLine", {})
        if _is_token_tracker_statusline(sl):
            backup = settings.get(BACKUP_KEY, {})
            previous = backup.get(PREVIOUS_STATUSLINE_KEY)
            if isinstance(previous, dict):
                settings["statusLine"] = previous
                console.print(f"[green]✓[/green] 已恢复安装前的 statusLine 配置")
            else:
                settings.pop("statusLine", None)
                console.print(f"[green]✓[/green] 已从 settings.json 移除 statusLine 配置")

            if BACKUP_KEY in settings:
                settings[BACKUP_KEY].pop(PREVIOUS_STATUSLINE_KEY, None)
                if not settings[BACKUP_KEY]:
                    del settings[BACKUP_KEY]

            with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        else:
            console.print("[dim]当前 statusLine 不是 tt-statusline，保留现有配置[/dim]")
    else:
        console.print("[dim]settings.json 不存在[/dim]")

    status_file = os.path.expanduser("~/.claude/tt-status.json")
    if os.path.exists(status_file):
        os.remove(status_file)
        console.print(f"[green]✓[/green] 已删除缓存: {status_file}")
