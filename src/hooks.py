import json
import os
import stat

from .ui.tables import console

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_SCRIPT_PATH = os.path.expanduser("~/.claude/tt-statusline.py")

HOOK_SCRIPT = r'''#!/usr/bin/env python3
"""Token Tracker statusLine hook — captures full statusLine data from Claude Code."""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

STATUS_FILE = os.path.expanduser("~/.claude/tt-status.json")


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return

    data["_received_at"] = datetime.now(timezone.utc).isoformat()

    try:
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(STATUS_FILE), suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass

    parts = []
    rl = data.get("rate_limits", {})
    five = rl.get("five_hour", {})
    seven = rl.get("seven_day", {})
    if five.get("used_percentage") is not None:
        parts.append(f"5h:{five['used_percentage']:.0f}%")
    if seven.get("used_percentage") is not None:
        parts.append(f"7d:{seven['used_percentage']:.0f}%")
    if parts:
        print(" ".join(parts))


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
            console.print("[yellow]将替换为 Token Tracker hook，原配置将失效[/yellow]")

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
        if "tt-statusline" in sl.get("command", ""):
            del settings["statusLine"]
            with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            console.print(f"[green]✓[/green] 已从 settings.json 移除 statusLine 配置")
        else:
            console.print("[dim]settings.json 中无 Token Tracker 的 statusLine 配置[/dim]")
    else:
        console.print("[dim]settings.json 不存在[/dim]")

    status_file = os.path.expanduser("~/.claude/tt-status.json")
    if os.path.exists(status_file):
        os.remove(status_file)
        console.print(f"[green]✓[/green] 已删除缓存: {status_file}")
