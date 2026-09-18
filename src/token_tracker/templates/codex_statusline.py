#!/usr/bin/env python3
"""token-tracker Codex 伪 statusline（Stop hook）：每次回答后追加两行彩色 status，仿 CC statusline。
L1：[项目](分支 +A -D) | Total: <会话累计 token> | Cost: $<第三方 provider 会话成本> | Model: <模型>
L2：Limit: 5h <bar> <%> (reset) | 7d <bar> <%> (reset) | <window> Ctx <bar> <%>
数据：单次扫描当前会话同时取得 Total、逐请求成本和 5h/7d 限额；当前会话没有标准限额时，按
model_provider 回退最近同 provider 快照——同 CODEX_HOME 多账号/多 provider 混跑时不串配额；Ctx = last_input ÷ window；
Model = Stop payload.model；会话按 transcript_path 精确定位、回退最近文件。
由 `tt setup` 生成，勿手改。"""
__version__ = "__STATUSLINE_HOOK_VERSION__"
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TERMINAL_MAP_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-terminal-map.json")
MAX_TERMINAL_MAPPINGS = 20

# 配色由 tt setup / update_hook / tt theme set 烘焙时注入（跟随当前主题，与 CC statusline / CLI 报表同源）。
# Codex TUI 实测支持 24-bit truecolor，故只注入 truecolor 一套（不像 CC statusline 还需 256 兜底）。
C = __STATUSLINE_TRUECOLOR__
RST = C["reset"]
FAINT, BOLD = "\033[2m", "\033[1m"


def _color(pct):
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


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


def _bar(pct, width=8):
    """进度条（仿 CC statusline）：█ 填充档位色 + ░ 空槽（>0 也染档位色），尾接 % 档位色。"""
    pct = max(0.0, min(100.0, float(pct)))
    filled = round(pct / 100 * width)
    empty = width - filled
    color = _color(pct)
    empty_s = f"{color}{'░' * empty}{RST}" if pct > 0 and empty else "░" * empty
    return f"{color}{'█' * filled}{RST}{empty_s} {color}{pct:.0f}%{RST}"


def _total_tokens(info):
    """会话累计 token：优先 API 返回的 total_tokens（= input + output，实测逐会话相等）；
    reasoning_output_tokens 是 output 的子集拆分，加它会重复计数。"""
    try:
        u = info.get("total_token_usage") or {}
        return u.get("total_tokens", 0) or u.get("input_tokens", 0) + u.get("output_tokens", 0)
    except Exception:
        return 0


def _parse_session(path):
    """单次扫描会话，得到元数据、用量、限额与逐请求计价数据。"""
    from token_tracker.adapters.codex import load_session_snapshot
    return load_session_snapshot(path)


def _current_session(payload):
    """返回会话解析结果 + 是否由 Stop payload 的 transcript_path 精确定位。

    最近文件回退只供伪 statusline 尽力显示；终端映射不能用回退结果，否则多 Codex 会话
    并发时可能把当前窗格错误绑定到别的会话。
    """
    try:
        tp = payload.get("transcript_path")
        if tp and os.path.exists(tp):
            snapshot = _parse_session(Path(tp))
            if snapshot.session_id or snapshot.info:
                return snapshot, True
        from token_tracker.adapters import codex
        for f in sorted(Path(codex.SESSIONS_DIR).rglob("*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
            snapshot = _parse_session(f)
            if snapshot.info:
                return snapshot, False
    except Exception:
        pass
    return None, False


def _record_terminal_map(session_id):
    """记录当前 Codex 会话所在窗格，供普通 `tt sidebar` 点击项目名跳转。

    单独落文件，不和 CC statusline 的心跳/status JSON 竞争写；同一文件的多个 Codex Stop
    hook 用 flock 串行合并（Windows 无 iTerm/tmux，缺 fcntl 时仍保留原子替换兜底）。
    """
    term = {}
    if os.environ.get("ITERM_SESSION_ID"):
        term["iterm"] = os.environ["ITERM_SESSION_ID"]
    if os.environ.get("TMUX_PANE"):
        term["tmux"] = os.environ["TMUX_PANE"]
    if not session_id or not term:
        return

    tmp = None
    lock = None
    try:
        parent = os.path.dirname(TERMINAL_MAP_FILE)
        os.makedirs(parent, exist_ok=True)
        lock = open(TERMINAL_MAP_FILE + ".lock", "a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            with open(TERMINAL_MAP_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        term_map = data.get("_terminal_map") if isinstance(data, dict) else None
        if not isinstance(term_map, dict):
            term_map = {}
        term_map.pop(session_id, None)
        term_map[session_id] = term
        for key in list(term_map)[:-MAX_TERMINAL_MAPPINGS]:
            del term_map[key]
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"_terminal_map": term_map}, f)
        os.replace(tmp, TERMINAL_MAP_FILE)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if lock:
            lock.close()


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


def _session_cost(info, model, usage_entry=None):
    """第三方 API provider（deepseek 等）没有账号配额，改按会话 token 用量估成本（cost.py 定价）。
    解析不到定价 / 零用量返回 None（宁缺毋假 $0）。"""
    try:
        from token_tracker.analyzer.cost import calculate_cost
        if usage_entry is not None:
            usage_entry.model = model
            cost = calculate_cost(usage_entry)
            return cost if cost > 0 else None

        u = (info or {}).get("total_token_usage") or {}
        from token_tracker.adapters.codex import _usage_tokens
        counts = _usage_tokens(u)
        if not model or counts is None or not any(counts):
            return None
        ordinary_in, total_out, written, cached = counts
        # reasoning_output_tokens 是 output_tokens 的子集拆分，output 价已含 reasoning，不能再加
        from token_tracker.adapters.types import UsageEntry
        entry = UsageEntry(
            timestamp=datetime.now(timezone.utc),
            session_id="", message_id="", request_id="", model=model,
            input_tokens=ordinary_in, output_tokens=total_out,
            cache_creation_tokens=written, cache_read_tokens=cached,
            cost_usd=None, project="", agent_id="codex",
        )
        cost = calculate_cost(entry)
        return cost if cost > 0 else None
    except Exception:
        return None


def _git_status(cwd):
    """(branch, +A, -D, ?U)；已跟踪改动按行、未跟踪按文件计数。
    失败/非 git/无 commit 返回 ("", 0, 0, 0)。"""
    if not cwd:
        return "", 0, 0, 0

    def run(args):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", timeout=2,
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


def _render_limit(label, pct, resets_at, now_ts):
    s = f"{C['label']}{label} {RST}{_bar(pct)}"
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

    snapshot, exact_session = _current_session(payload)
    session_id = snapshot.session_id if snapshot else ""
    cwd = snapshot.cwd if snapshot else ""
    info = snapshot.info if snapshot else None
    model = snapshot.model if snapshot else ""
    effort = snapshot.effort if snapshot else ""
    provider = snapshot.provider if snapshot else ""

    # Limit：优先当前会话自己的限额快照（多账号/多 provider 混跑不串数据）；
    # 会话还没产生 token_count 时回退到同 provider 的最近会话。
    rl = None
    try:
        from token_tracker.adapters import codex
        if exact_session and snapshot:
            rl = snapshot.rate_limits
        if rl is None:
            rl = codex.load_rate_limits(provider=provider or None)
    except Exception:
        pass

    payload_session_id = payload.get("session_id") or payload.get("thread_id")
    terminal_session_id = session_id if exact_session else ""
    if isinstance(payload_session_id, str) and payload_session_id:
        session_id = payload_session_id
        terminal_session_id = payload_session_id
    _record_terminal_map(terminal_session_id)
    cwd = payload.get("cwd") or cwd
    ctx = _ctx_pct(info) if info else None
    now_ts = int(datetime.now(timezone.utc).timestamp())

    # L1: 项目(git) | Total | Model
    line1 = []
    proj = _render_project(cwd)
    if proj:
        line1.append(proj)
    total = _total_tokens(info) if info else 0
    if total:
        line1.append(f"{C['tokens']}Total: {fmt_tokens(total)}{RST}")  # 整体取 tokens 槽（mocha=peach/橙）
    model = model or payload.get("model") or ""  # session turn_context 的 model（gpt-5.5）优先
    # 第三方 API provider（deepseek 等）无账号配额：L1 补会话成本（仿 CC 的 Cost 槽位）
    usage_entry = snapshot.usage_entry if snapshot else None
    cost = _session_cost(info, model, usage_entry) if provider and provider != "openai" else None
    if cost is not None:
        cost_s = f"${cost:.2f}" if cost >= 0.01 else f"${cost:.4f}"
        line1.append(f"{C['total']}Cost: {cost_s}{RST}")
    if model:
        # effort 缺失时显示 default（Codex 默认 reasoning level），与 TUI 的 Current reasoning level 对齐
        label = f"{model} {effort or 'default'}"
        line1.append(f"{C['total']}Model: {label}{RST}")  # 整体取 total 槽（mocha=red/红）

    # L2: Limit: 5h | 7d | <window> Ctx（仿 CC statusline，带进度条 + reset）
    line2 = []
    has_limit = False
    if rl and rl.five_hour_pct is not None:
        line2.append(_render_limit("5h", rl.five_hour_pct, rl.five_hour_resets_at, now_ts))
        has_limit = True
    if rl and rl.seven_day_pct is not None:
        line2.append(_render_limit("7d", rl.seven_day_pct, rl.seven_day_resets_at, now_ts))
        has_limit = True
    if ctx is not None:
        size = (info or {}).get("model_context_window") or 0
        prefix = f"{fmt_tokens(size)} " if size else ""
        line2.append(f"{C['label']}{prefix}Ctx {RST}{_bar(ctx)}")
    if line2 and has_limit:  # 第三方 provider 只有 Ctx 时不挂 Limit: 前缀
        line2[0] = f"{C['label']}Limit:{RST} " + line2[0]

    lines = [" | ".join(x) for x in (line1, line2) if x]
    if lines:
        # 开头加 \n：Codex 把 systemMessage 包成 "warning:" 开头，让 status 内容另起一行、与之分开
        print(json.dumps({"systemMessage": "\n" + "\n".join(lines)}))


if __name__ == "__main__":
    main()
