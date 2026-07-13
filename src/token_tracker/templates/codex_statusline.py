#!/usr/bin/env python3
"""token-tracker Codex 伪 statusline（Stop hook）：每次回答后追加两行彩色 status，仿 CC statusline。
L1：[项目](分支 +A -D) | Total: <会话累计 token> | Model: <模型>
L2：Limit: 5h <bar> <%> (reset) | 7d <bar> <%> (reset) | <window> Ctx <bar> <%>
数据：Total = 当前会话 total_token_usage（in+out+reasoning）；5h/7d = codex.load_rate_limits()（账号级准）；
Ctx = last_input ÷ window；Model = Stop payload.model；会话按 transcript_path 精确定位、回退最近文件。
由 `tt setup` 生成，勿手改。"""
__version__ = "__STATUSLINE_HOOK_VERSION__"
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 配色由 tt setup / update_hook / tt theme set 烘焙时注入（跟随当前主题，与 CC statusline / CLI 报表同源）。
# Codex TUI 实测支持 24-bit truecolor，故只注入 truecolor 一套（不像 CC statusline 还需 256 兜底）。
C = __STATUSLINE_TRUECOLOR__
RST = C["reset"]
FAINT, BOLD = "\033[2m", "\033[1m"


def _color(pct):
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


def _quota_display_mode():
    try:
        path = os.path.join(os.path.expanduser("~/.config/token-tracker"), "config.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return "remaining" if data.get("quota_display") == "remaining" else "used"
    except Exception:
        return "used"


def _quota_pct(used_pct, mode):
    pct = max(0.0, min(100.0, float(used_pct)))
    return 100.0 - pct if mode == "remaining" else pct


def _quota_color(pct, mode):
    if mode == "remaining":
        return C["bar_danger"] if pct < 20 else C["bar_warn"] if pct < 50 else C["bar_ok"]
    return _color(pct)


def _fmt_duration(s):
    s = int(s)
    if s >= 86400:
        return f"{s // 86400}d{s % 86400 // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60}m"
    return f"{s // 60}m"


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _bar(pct, width=8, mode="used"):
    """进度条（仿 CC statusline）：█ 填充档位色 + ░ 空槽（>0 也染档位色），尾接 % 档位色。"""
    pct = _quota_pct(pct, mode)
    filled = round(pct / 100 * width)
    empty = width - filled
    color = _quota_color(pct, mode)
    empty_s = f"{color}{'░' * empty}{RST}" if pct > 0 and empty else "░" * empty
    return f"{color}{'█' * filled}{RST}{empty_s} {color}{pct:.0f}%{RST}"


def _total_tokens(info):
    """会话累计 token = total_token_usage 的 input(含 cached) + output + reasoning。"""
    try:
        u = info.get("total_token_usage") or {}
        return u.get("input_tokens", 0) + u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0)
    except Exception:
        return 0


def _parse_session(path):
    """解析 session jsonl → (cwd, 最后一个 token_count 的 info, model, effort)。
    model/effort 取最后一个 turn_context（跟随中途换模型/调 effort）。"""
    from token_tracker.adapters.util import iter_jsonl_dicts
    cwd = ""
    info = None
    model = effort = ""
    for d in iter_jsonl_dicts(path):
        p = d.get("payload", {})
        t = d.get("type")
        if t == "session_meta":
            cwd = p.get("cwd", "")
        elif t == "turn_context":  # 含 model（gpt-5.5）+ effort（high）
            model = p.get("model") or model
            effort = p.get("effort") or effort
        elif p.get("type") == "token_count" and p.get("info"):
            info = p["info"]
    return cwd, info, model, effort


def _current_session(payload):
    """优先按 Stop payload 的 transcript_path 精确定位当前会话；拿不到再回退最近改动文件。"""
    try:
        tp = payload.get("transcript_path")
        if tp and os.path.exists(tp):
            r = _parse_session(Path(tp))
            if r[1]:
                return r
        from token_tracker.adapters import codex
        for f in sorted(Path(codex.SESSIONS_DIR).rglob("*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
            r = _parse_session(f)
            if r[1]:
                return r
    except Exception:
        pass
    return "", None, "", ""


def _ctx_pct(info):
    """当前 context 占用 % = 最后一次请求的 input ÷ 模型上下文窗口。"""
    try:
        win = info.get("model_context_window")
        last = info.get("last_token_usage") or {}
        if win and last.get("input_tokens"):
            return last["input_tokens"] / win * 100
    except Exception:
        pass
    return None


def _git_status(cwd):
    """(branch, +A, -D, ?U)；已跟踪改动按行、未跟踪按文件计数。
    失败/非 git/无 commit 返回 ("", 0, 0, 0)。"""
    if not cwd:
        return "", 0, 0, 0

    def run(args):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()

    try:
        branch = run(["branch", "--show-current"])
    except Exception:
        return "", 0, 0, 0
    if not branch:
        return "", 0, 0, 0
    try:
        if run(["status", "--porcelain", "--untracked-files=no"]):
            branch += "*"
    except Exception:
        pass
    a = d = 0
    try:
        for ln in run(["diff", "HEAD", "--numstat"]).splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                if parts[0].isdigit():
                    a += int(parts[0])
                if parts[1].isdigit():
                    d += int(parts[1])
    except Exception:
        pass
    u = 0
    try:
        u = sum(1 for ln in run(["ls-files", "--others", "--exclude-standard"]).splitlines() if ln.strip())
    except Exception:
        pass
    return branch, a, d, u


def _render_project(cwd):
    if not cwd:
        return ""
    name = os.path.basename(cwd.rstrip("/"))
    branch, a, d, u = _git_status(cwd)
    if not branch:
        return f"{BOLD}{C['project']}[{name}]{RST}"
    inner = f"{C['branch']}{branch}{RST}"
    if a:
        inner += f" {C['added']}+{a}{RST}"
    if d:
        inner += f" {C['deleted']}-{d}{RST}"
    if u:
        inner += f" {C['untracked']}?{u}{RST}"
    return f"{BOLD}{C['project']}[{name}]{RST}({inner})"


def _render_limit(label, pct, resets_at, now_ts, mode):
    s = f"{C['label']}{label} {RST}{_bar(pct, mode=mode)}"
    if resets_at:
        remain = int(resets_at) - now_ts
        if remain > 0:
            s += f" {FAINT}{C['label']}(reset {_fmt_duration(remain)}){RST}"
    return s


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    rl = None
    try:
        from token_tracker.adapters import codex
        rl = codex.load_rate_limits()
    except Exception:
        pass

    cwd, info, model, effort = _current_session(payload)
    cwd = payload.get("cwd") or cwd
    ctx = _ctx_pct(info) if info else None
    now_ts = int(datetime.now(timezone.utc).timestamp())
    quota_mode = _quota_display_mode()

    # L1: 项目(git) | Total | Model
    line1 = []
    proj = _render_project(cwd)
    if proj:
        line1.append(proj)
    total = _total_tokens(info) if info else 0
    if total:
        line1.append(f"{C['tokens']}Total: {fmt_tokens(total)}{RST}")  # 整体取 tokens 槽（mocha=peach/橙）
    model = model or payload.get("model") or ""  # session turn_context 的 model（gpt-5.5）优先
    if model:
        # effort 缺失时显示 default（Codex 默认 reasoning level），与 TUI 的 Current reasoning level 对齐
        label = f"{model} {effort or 'default'}"
        line1.append(f"{C['total']}Model: {label}{RST}")  # 整体取 total 槽（mocha=red/红）

    # L2: Limit: 5h | 7d | <window> Ctx（仿 CC statusline，带进度条 + reset）
    line2 = []
    if rl and rl.five_hour_pct is not None:
        line2.append(_render_limit("5h", rl.five_hour_pct, rl.five_hour_resets_at, now_ts, quota_mode))
    if rl and rl.seven_day_pct is not None:
        line2.append(_render_limit("7d", rl.seven_day_pct, rl.seven_day_resets_at, now_ts, quota_mode))
    if ctx is not None:
        size = (info or {}).get("model_context_window") or 0
        prefix = f"{fmt_tokens(size)} " if size else ""
        line2.append(f"{C['label']}{prefix}Ctx {RST}{_bar(ctx)}")
    if line2:
        prefix = "Left:" if quota_mode == "remaining" else "Limit:"
        line2[0] = f"{C['label']}{prefix}{RST} " + line2[0]

    lines = [" | ".join(x) for x in (line1, line2) if x]
    if lines:
        # 开头加 \n：Codex 把 systemMessage 包成 "warning:" 开头，让 status 内容另起一行、与之分开
        print(json.dumps({"systemMessage": "\n" + "\n".join(lines)}))


if __name__ == "__main__":
    main()
