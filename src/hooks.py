import json
import os
import re
import stat
import tomllib

from .ui.tables import console

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_SCRIPT_PATH = os.path.expanduser("~/.claude/tt-statusline.py")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
CODEX_BACKUP = os.path.expanduser("~/.codex/tt-backup.json")
HOOK_VERSION = "1.1"
_BACKUP_KEY = "tokenTracker"
_PREV_SL_KEY = "previousStatusLine"
_SL_REGEX = re.compile(r'status_line\s*=\s*\[.*?\]', re.DOTALL)

CODEX_STATUS_LINE = [
    "project",
    "five-hour-limit",
    "weekly-limit",
    "context-remaining",
    "model-with-reasoning",
]

HOOK_SCRIPT = r'''#!/usr/bin/env python3
"""Claude Code statusLine — 状态栏显示 + 数据持久化到 tt-status.json"""
__version__ = "1.1"
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
        pct = ctx["used_percentage"]
        cc = C["green"] if pct < 40 else C["yellow"] if pct < 60 else C["red"]
        parts.append(f"{cc}{fmt_tokens(size)} CTX: {pct:.0f}%{C['reset']}")

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


# --- helpers ---

def _status_line_toml(items: list[str]) -> str:
    body = ",\n".join(f'  "{item}"' for item in items)
    return f"status_line = [\n{body},\n]"


def _read_codex_config() -> tuple[str, dict] | None:
    try:
        with open(CODEX_CONFIG, "r", encoding="utf-8") as f:
            content = f.read()
        return content, tomllib.loads(content)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def is_setup() -> bool:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.exists(CODEX_CONFIG)
    if not has_cc and not has_codex:
        return False
    if has_cc:
        try:
            with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
                settings = json.load(f)
            sl = settings.get("statusLine")
            if not isinstance(sl, dict) or "tt-statusline" not in (sl.get("command") or ""):
                return False
        except (OSError, json.JSONDecodeError):
            return False
    if has_codex:
        result = _read_codex_config()
        if not result:
            return False
        _, parsed = result
        if parsed.get("tui", {}).get("status_line") != CODEX_STATUS_LINE:
            return False
    return True


def _installed_hook_version() -> str | None:
    try:
        with open(HOOK_SCRIPT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


def needs_update() -> bool:
    return _installed_hook_version() != HOOK_VERSION


def update_hook() -> None:
    with open(HOOK_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(HOOK_SCRIPT)
    os.chmod(HOOK_SCRIPT_PATH, os.stat(HOOK_SCRIPT_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --- setup ---

def setup(auto: bool = False) -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.exists(CODEX_CONFIG)

    if not has_cc and not has_codex:
        console.print("[red]未检测到 Claude Code 或 Codex，请先安装其中之一[/red]")
        return

    if auto:
        console.print("[dim]首次使用，正在配置状态栏...[/dim]")

    if has_cc:
        _setup_claude()
    else:
        if not auto:
            console.print("[dim]未检测到 Claude Code，跳过[/dim]")

    if has_codex:
        _setup_codex()
    else:
        if not auto:
            console.print("[dim]未检测到 Codex，跳过[/dim]")


def _setup_claude() -> None:
    with open(HOOK_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(HOOK_SCRIPT)
    os.chmod(HOOK_SCRIPT_PATH, os.stat(HOOK_SCRIPT_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    settings: dict = {}
    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)

    existing = settings.get("statusLine")
    if existing and "tt-statusline" not in (existing.get("command") or ""):
        console.print(f"[yellow]检测到已有 statusLine，备份后替换[/yellow]")
        settings.setdefault(_BACKUP_KEY, {})[_PREV_SL_KEY] = existing

    settings["statusLine"] = {"type": "command", "command": HOOK_SCRIPT_PATH}

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    console.print(f"[green]✓[/green] Claude Code statusLine 已配置")
    console.print("[dim]重启 Claude Code 后生效[/dim]")


def _setup_codex() -> None:
    result = _read_codex_config()
    if not result:
        return
    content, parsed = result

    old = parsed.get("tui", {}).get("status_line")
    if old == CODEX_STATUS_LINE:
        console.print("[dim]Codex status_line 已是目标配置，跳过[/dim]")
        return

    if old is not None:
        with open(CODEX_BACKUP, "w", encoding="utf-8") as f:
            json.dump({"status_line": old}, f)
        content = _SL_REGEX.sub(_status_line_toml(CODEX_STATUS_LINE), content)
    elif "[tui]" in content:
        content = content.replace("[tui]", f"[tui]\n{_status_line_toml(CODEX_STATUS_LINE)}")
    else:
        content += f"\n[tui]\n{_status_line_toml(CODEX_STATUS_LINE)}\n"

    with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)

    console.print(f"[green]✓[/green] Codex status_line 已配置")
    if old is not None:
        console.print(f"[dim]原配置已备份到: {CODEX_BACKUP}[/dim]")
    console.print("[dim]重启 Codex 后生效[/dim]")


# --- unsetup ---

def unsetup() -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.exists(CODEX_CONFIG)

    if has_cc:
        _unsetup_claude()
    if has_codex:
        _unsetup_codex()
    if not has_cc and not has_codex:
        console.print("[dim]未检测到 Claude Code 或 Codex[/dim]")


def _unsetup_claude() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
        console.print(f"[green]✓[/green] 已删除: {HOOK_SCRIPT_PATH}")

    if not os.path.exists(CLAUDE_SETTINGS):
        return

    with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
        settings = json.load(f)

    sl = settings.get("statusLine")
    if not isinstance(sl, dict) or "tt-statusline" not in (sl.get("command") or ""):
        console.print("[dim]当前 statusLine 不是 tt-statusline，保留现有配置[/dim]")
        return

    previous = settings.get(_BACKUP_KEY, {}).get(_PREV_SL_KEY)
    if isinstance(previous, dict):
        settings["statusLine"] = previous
        console.print(f"[green]✓[/green] Claude Code statusLine 已恢复原配置")
    else:
        settings.pop("statusLine", None)
        console.print(f"[green]✓[/green] Claude Code statusLine 已移除")

    backup = settings.get(_BACKUP_KEY)
    if isinstance(backup, dict):
        backup.pop(_PREV_SL_KEY, None)
        if not backup:
            del settings[_BACKUP_KEY]

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    status_file = os.path.expanduser("~/.claude/tt-status.json")
    if os.path.exists(status_file):
        os.remove(status_file)
        console.print(f"[green]✓[/green] 已删除缓存: {status_file}")


def _unsetup_codex() -> None:
    result = _read_codex_config()
    if not result:
        return
    content, parsed = result

    if parsed.get("tui", {}).get("status_line") is None:
        return

    if os.path.exists(CODEX_BACKUP):
        with open(CODEX_BACKUP, "r", encoding="utf-8") as f:
            old_items = json.load(f).get("status_line", [])
        content = _SL_REGEX.sub(_status_line_toml(old_items), content)
        os.remove(CODEX_BACKUP)
        console.print(f"[green]✓[/green] Codex status_line 已恢复原配置")
    else:
        content = re.sub(r'status_line\s*=\s*\[.*?\]\n?', '', content, flags=re.DOTALL)
        console.print(f"[green]✓[/green] Codex status_line 已移除")

    with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)
