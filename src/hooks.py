import json
import os
import re
import stat
import sys
import tomllib

from .i18n import t
from .ui.tables import console

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_SCRIPT_PATH = os.path.expanduser("~/.claude/tt-statusline.py")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
CODEX_BACKUP = os.path.expanduser("~/.codex/tt-backup.json")
HOOK_VERSION = "1.6"
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
__version__ = "1.6"
import json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone

STATUS_FILE = os.path.expanduser("~/.claude/tt-status.json")
ANSI_RE = re.compile(r'\033\[[0-9;]*m')
THEME = "mocha"
THEMES = {
    "default": {
        "project": "\033[32m", "branch": "\033[35m", "label": "\033[34m",
        "bar_ok": "\033[32m", "bar_warn": "\033[33m", "bar_danger": "\033[31m",
        "tokens": "\033[33m", "duration": "\033[2;35m", "model": "\033[2;35m",
        "reset": "\033[0m",
    },
    "mocha": {
        "project": "\033[38;5;120m", "branch": "\033[38;5;111m", "label": "\033[38;5;218m",
        "bar_ok": "\033[38;5;151m", "bar_warn": "\033[38;5;229m", "bar_danger": "\033[38;5;217m",
        "tokens": "\033[38;5;223m", "duration": "\033[38;5;116m", "model": "\033[38;5;116m",
        "reset": "\033[0m",
    },
    "dracula": {
        "project": "\033[38;5;84m", "branch": "\033[38;5;75m", "label": "\033[38;5;212m",
        "bar_ok": "\033[38;5;151m", "bar_warn": "\033[38;5;229m", "bar_danger": "\033[38;5;217m",
        "tokens": "\033[38;5;215m", "duration": "\033[38;5;117m", "model": "\033[38;5;117m",
        "reset": "\033[0m",
    },
}
def _supports_256color():
    if os.environ.get("COLORTERM", "") in ("truecolor", "24bit"):
        return True
    if "256color" in os.environ.get("TERM", ""):
        return True
    return False

C = THEMES.get(THEME, THEMES["default"]) if _supports_256color() or THEME == "default" else THEMES["default"]

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def vlen(s):
    return len(ANSI_RE.sub("", s))


def get_width():
    try:
        return max(1, os.get_terminal_size(2).columns - 4)
    except Exception:
        pass
    if os.name != "nt":
        try:
            import fcntl, struct, termios
            with open('/dev/tty', 'r') as tty:
                res = fcntl.ioctl(tty, termios.TIOCGWINSZ, b'\x00' * 8)
                return max(1, struct.unpack('hh', res[:4])[1] - 4)
        except Exception:
            pass
    return 116


def color_by_pct(pct):
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


def fmt_tokens(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.0f}k"
    return str(n)


def progress_bar(value, bar_width=8):
    filled_char, empty_char = "█", "░"
    if value is None:
        return empty_char * bar_width + " n/a"
    pct = max(0.0, min(100.0, float(value)))
    filled = round(pct / 100 * bar_width)
    return f"{color_by_pct(pct)}{filled_char * filled}{C['reset']}{empty_char * (bar_width - filled)} {C['label']}{pct:.0f}%{C['reset']}"


def fmt_duration(seconds):
    if seconds >= 86400:
        d, rem = int(seconds // 86400), int(seconds % 86400)
        return f"{d}d{rem // 3600}h"
    if seconds >= 3600:
        h, m = int(seconds // 3600), int((seconds % 3600) // 60)
        return f"{h}h{m}m"
    if seconds >= 60:
        return f"{int(seconds // 60)}min"
    return f"{int(seconds)}s"


def git_branch(cwd):
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
    except Exception:
        return ""
    if not branch:
        return ""
    try:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
        if dirty:
            branch += "*"
    except Exception:
        pass
    return branch


def save_data(data, now):
    data["_received_at"] = now.isoformat()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATUS_FILE), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def render(data, now):
    W = get_width()
    ctx = data.get("context_window") or {}
    bar_w = 8 if W >= 100 else 6 if W >= 60 else 4

    # --- Line 1: Project | 5h | 7d | CTX ---
    line1 = []

    project = (data.get("workspace") or {}).get("project_dir", "")
    if project:
        name = os.path.basename(project)
        branch = git_branch(project)
        if branch:
            line1.append(f"{C['project']}{name}{C['reset']}({C['branch']}{branch}{C['reset']})")
        else:
            line1.append(f"{C['project']}{name}{C['reset']}")

    rl = data.get("rate_limits") or {}
    rl_parts = []
    for key, label in [("five_hour", "5h"), ("seven_day", "7d")]:
        entry = rl.get(key) or {}
        pct = entry.get("used_percentage")
        if pct is not None:
            reset_str = ""
            resets_at = entry.get("resets_at")
            if resets_at:
                remain = int(resets_at) - int(now.timestamp())
                if remain > 0:
                    reset_str = f" \033[2m{C['label']}({fmt_duration(remain)}){C['reset']}"
            rl_parts.append((
                f"{C['label']}{label}:{C['reset']}{progress_bar(pct, bar_w)}{reset_str}",
                f"{C['label']}{label}:{C['reset']}{progress_bar(pct, bar_w)}",
                f"{C['label']}{label}:{pct:.0f}%{C['reset']}",
            ))

    ctx_parts = []
    if ctx.get("used_percentage") is not None:
        size = ctx.get("context_window_size", 0)
        ctx_parts = [
            f"{C['label']}{fmt_tokens(size)} Context:{C['reset']}{progress_bar(ctx['used_percentage'], bar_w)}",
            f"{C['label']}{fmt_tokens(size)} CTX:{ctx['used_percentage']:.0f}%{C['reset']}",
        ]

    # 尝试完整版（带进度条+reset time）
    full = line1 + [p[0] for p in rl_parts] + (ctx_parts[:1] if ctx_parts else [])
    candidate = " | ".join(full)
    if vlen(candidate) <= W:
        line1 = full
    else:
        # 去掉 reset time
        no_reset = line1 + [p[1] for p in rl_parts] + (ctx_parts[:1] if ctx_parts else [])
        candidate = " | ".join(no_reset)
        if vlen(candidate) <= W:
            line1 = no_reset
        else:
            # 去掉进度条，只留百分比
            minimal = line1 + [p[2] for p in rl_parts] + (ctx_parts[1:2] if ctx_parts else [])
            line1 = minimal

    # --- Line 2: Tokens + Cache + Cost ---
    line2 = []

    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    curr_usage = (ctx.get("current_usage") or {})
    turn_in_total = curr_usage.get("input_tokens", 0) + curr_usage.get("cache_creation_input_tokens", 0)
    turn_out = curr_usage.get("output_tokens", 0)
    turn_str = f" \033[2m{C['tokens']}(本轮: in {fmt_tokens(turn_in_total)}, out {fmt_tokens(turn_out)}){C['reset']}"
    if total_in or total_out:
        tok_full = f"{C['tokens']}Tokens: in {fmt_tokens(total_in)}, out {fmt_tokens(total_out)}{turn_str}"
        tok_short = f"{C['tokens']}Tokens: in {fmt_tokens(total_in)}, out {fmt_tokens(total_out)}{C['reset']}"
        line2.append(tok_full)
    cache_read = curr_usage.get("cache_read_input_tokens", 0)
    if cache_read > 0:
        line2.append(f"{C['tokens']}Cached: {fmt_tokens(cache_read)}{C['reset']}")

    cost = data.get("cost") or {}
    usd = cost.get("total_cost_usd")
    if usd is not None:
        line2.append(f"{C['tokens']}Cost: ${usd:.2f}{C['reset']}")

    # 宽度不够时隐藏本轮数据
    if vlen(" | ".join(line2)) > W and (total_in or total_out):
        line2[0] = tok_short
        if vlen(" | ".join(line2)) > W:
            line2 = line2[1:]

    # --- Line 3: Model | Duration ---
    line3 = []

    model_name = (data.get("model") or {}).get("display_name", "")
    if model_name:
        model_name = re.sub(r'\s*\(.*?\)', '', model_name)
        effort = (data.get("effort") or {}).get("level", "")
        if effort:
            model_name += f"/{effort}"
        fast = data.get("fast_mode")
        model_name += f"/{'fast' if fast else 'nofast'}"
        line3.append(f"{C['model']}Model: {model_name}{C['reset']}")

    duration_ms = cost.get("total_duration_ms")
    duration_part = ""
    if duration_ms and duration_ms > 0:
        duration_part = f"{C['duration']}Duration: {fmt_duration(duration_ms / 1000)}{C['reset']}"
        line3.append(duration_part)

    # 宽度不够时隐藏会话时长
    if vlen(" | ".join(line3)) > W and duration_part:
        line3 = [p for p in line3 if p != duration_part]

    output = [" | ".join(line) for line in (line1, line2, line3) if line]
    if output:
        print("\n".join(output))
        sys.stdout.flush()


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return

    now = datetime.now(timezone.utc)
    save_data(data, now)
    render(data, now)


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
    if not os.path.isdir(os.path.dirname(HOOK_SCRIPT_PATH)):
        return False
    return _installed_hook_version() != HOOK_VERSION


def update_hook() -> None:
    if not os.path.isdir(os.path.dirname(HOOK_SCRIPT_PATH)):
        return
    with open(HOOK_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(HOOK_SCRIPT)
    if os.name != "nt":
        os.chmod(HOOK_SCRIPT_PATH, os.stat(HOOK_SCRIPT_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --- setup ---

def setup(auto: bool = False) -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.exists(CODEX_CONFIG)

    if not has_cc and not has_codex:
        console.print(f"[red]{t('no_agent_install')}[/red]")
        return

    if auto:
        console.print(f"[dim]{t('first_setup')}[/dim]")

    if has_cc:
        _setup_claude()
    else:
        if not auto:
            console.print(f"[dim]{t('cc_not_found')}[/dim]")

    if has_codex:
        _setup_codex()
    else:
        if not auto:
            console.print(f"[dim]{t('codex_not_found')}[/dim]")


def _setup_claude() -> None:
    update_hook()

    settings: dict = {}
    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)

    existing = settings.get("statusLine")
    if existing and "tt-statusline" not in (existing.get("command") or ""):
        console.print(f"[yellow]{t('sl_backup_replace')}[/yellow]")
        settings.setdefault(_BACKUP_KEY, {})[_PREV_SL_KEY] = existing

    python = sys.executable or "python3"
    settings["statusLine"] = {"type": "command", "command": f"{python} {HOOK_SCRIPT_PATH}"}

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    console.print(f"[green]✓[/green] {t('cc_configured')}")
    console.print(f"[dim]{t('restart_cc')}[/dim]")


def _setup_codex() -> None:
    result = _read_codex_config()
    if not result:
        return
    content, parsed = result

    old = parsed.get("tui", {}).get("status_line")
    if old == CODEX_STATUS_LINE:
        console.print(f"[dim]{t('codex_already')}[/dim]")
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

    console.print(f"[green]✓[/green] {t('codex_configured')}")
    if old is not None:
        console.print(f"[dim]{t('codex_backup', path=CODEX_BACKUP)}[/dim]")
    console.print(f"[dim]{t('restart_codex')}[/dim]")


# --- unsetup ---

def unsetup() -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.exists(CODEX_CONFIG)

    if has_cc:
        _unsetup_claude()
    if has_codex:
        _unsetup_codex()
    if not has_cc and not has_codex:
        console.print(f"[dim]{t('no_agent_detected')}[/dim]")


def _unsetup_claude() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
        console.print(f"[green]✓[/green] {t('deleted_file', path=HOOK_SCRIPT_PATH)}")

    if not os.path.exists(CLAUDE_SETTINGS):
        return

    with open(CLAUDE_SETTINGS, "r", encoding="utf-8") as f:
        settings = json.load(f)

    sl = settings.get("statusLine")
    if not isinstance(sl, dict) or "tt-statusline" not in (sl.get("command") or ""):
        console.print(f"[dim]{t('sl_not_tt')}[/dim]")
        return

    previous = settings.get(_BACKUP_KEY, {}).get(_PREV_SL_KEY)
    if isinstance(previous, dict):
        settings["statusLine"] = previous
        console.print(f"[green]✓[/green] {t('cc_restored')}")
    else:
        settings.pop("statusLine", None)
        console.print(f"[green]✓[/green] {t('cc_removed')}")

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
        console.print(f"[green]✓[/green] {t('deleted_cache', path=status_file)}")


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
        console.print(f"[green]✓[/green] {t('codex_restored')}")
    else:
        content = re.sub(r'status_line\s*=\s*\[.*?\]\n?', '', content, flags=re.DOTALL)
        console.print(f"[green]✓[/green] {t('codex_removed')}")

    with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)
