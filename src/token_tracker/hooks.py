import json
import os
import re
import stat
import sys
import tomllib
from dataclasses import dataclass

from . import config
from .adapters.util import claude_home, codex_home
from .i18n import t
from .ui import themes
from .ui.console import get_console

_CLAUDE = claude_home()  # CLAUDE_CONFIG_DIR 覆盖 / ~/.claude
_CODEX = codex_home()    # CODEX_HOME 覆盖 / ~/.codex


@dataclass
class SetupComponents:
    """组件开关。状态栏总装（不可关，是 setup 的核心目的）；可选项为 Codex 伪 statusline（Stop hook）。"""
    codex_faux_statusline: bool = True

    @classmethod
    def all_on(cls) -> "SetupComponents":
        return cls(codex_faux_statusline=True)

# tt 自己的产物（statusline 脚本 + 缓存 + 备份）集中放 ~/.config/token-tracker（XDG，跟 theme/lang 同处）；
# settings.json / config.toml 是「改 agent 自己的配置」、必须留 agent 目录。statusLine/hook 的 command
# 是绝对路径，脚本放 agent 目录外照样跑（实测 + ccstatusline 等业界用 npx 全局脚本同理）。
_TT = config.CONFIG_DIR  # ~/.config/token-tracker

CLAUDE_SETTINGS = os.path.join(_CLAUDE, "settings.json")  # 改 Claude Code 配置，留 agent 目录
HOOK_SCRIPT_PATH = os.path.join(_TT, "claude-statusline.py")
CODEX_DIR = _CODEX
CODEX_CONFIG = os.path.join(CODEX_DIR, "config.toml")     # 改 Codex 配置，留 agent 目录
CODEX_STATUSLINE_HOOK_PATH = os.path.join(_TT, "codex-statusline.py")
STATUS_FILE = os.path.join(_TT, "tt-status.json")         # CC statusline 缓存（脚本写、tt status 读）
HOOK_VERSION = "1.9"
STATUSLINE_HOOK_VERSION = "1.0"

CC_BACKUP_PATH = os.path.join(_TT, "cc-backup.json")
CODEX_BACKUP_LEGACY = os.path.join(_TT, "codex-backup.json")  # 老用户残留，unsetup 时还能恢复

# 旧位置（agent 根目录）文件，迁移时删——老用户从 ~/.claude/~/.codex 迁到 ~/.config/token-tracker
_LEGACY_PATHS = [
    os.path.join(_CLAUDE, "tt-statusline.py"), os.path.join(_CLAUDE, "tt-status.json"),
    os.path.join(_CODEX, "tt-statusline.py"), os.path.join(_CODEX, "tt-backup.json"),
]

# HOOK_VERSION 是唯一版本来源；__HOOK_VERSION__ 占位符在 _render_hook_script() 里注入。
# 不要用 f-string：HOOK_SCRIPT 是含 \033 与正则的 r-string，f-string 会破坏花括号。
HOOK_SCRIPT = r'''#!/usr/bin/env python3
"""Claude Code statusLine — 状态栏显示 + 数据持久化到 tt-status.json"""
__version__ = "__HOOK_VERSION__"
import json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone

STATUS_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-status.json")
ANSI_RE = re.compile(r'\033\[[0-9;]*m')
# 配色在 tt setup / update_hook 烘焙时由 themes.theme_to_statusline_ansi(当前主题) 注入：
# THEME_COLORS 为当前主题 truecolor，THEME_COLORS_256 为同主题的 256 色近似（兜底不支持
# truecolor 的终端，如 macOS Terminal.app）。只认 COLORTERM=truecolor/24bit 走真彩，否则降 256。
THEME_COLORS = __STATUSLINE_TRUECOLOR__
THEME_COLORS_256 = __STATUSLINE_COLOR256__
def _supports_truecolor():
    return os.environ.get("COLORTERM", "") in ("truecolor", "24bit")

C = THEME_COLORS if _supports_truecolor() else THEME_COLORS_256

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
    empty = bar_width - filled
    color = color_by_pct(pct)
    # 未填充网格也染当前档位色（░ 字形天然更淡 → 同色暗格）；pct=0 时不动、保持灰
    empty_str = f"{color}{empty_char * empty}{C['reset']}" if pct > 0 and empty else empty_char * empty
    return f"{color}{filled_char * filled}{C['reset']}{empty_str} {C['label']}{pct:.0f}%{C['reset']}"


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


def git_diff_stat(cwd):
    """相对 HEAD 的未提交增删行数（暂存+未暂存，不含未跟踪文件）。失败/无 commit 返回 (0, 0)。"""
    try:
        out = subprocess.check_output(
            ["git", "diff", "HEAD", "--numstat"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        )
    except Exception:
        return 0, 0
    added = deleted = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        a, d = parts[0], parts[1]
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            deleted += int(d)
    return added, deleted


def save_data(data, now):
    data["_received_at"] = now.isoformat()
    tmp = None
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
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


def _read_prev(session_id):
    """读旧 tt-status.json：本会话 (api_duration_ms, last_tps) + 全量 _tps_state（供合并写回）。

    tt-status.json 是多会话共享单文件、会被其它会话覆盖；TPS 差分按 session_id 存进
    _tps_state dict、各会话互不干扰。返回 state 让本帧把自己的状态并回去、不丢别的会话。
    """
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return None, None, {}
    state = old.get("_tps_state")
    if not isinstance(state, dict):
        state = {}
    if session_id and session_id in state:
        s = state.get(session_id) or {}
        return s.get("api"), s.get("tps"), state
    if session_id and old.get("session_id") == session_id:  # 兼容升级前的旧单会话帧
        return (old.get("cost") or {}).get("total_api_duration_ms"), old.get("_last_tps"), state
    return None, None, state


def _compute_tps(data, prev_api_ms, prev_tps):
    """本轮 TPS = 本轮 output / Δapi_duration；数据缺失/中间帧/算出会显示为 0 时都沿用上次值、不刷新。"""
    cur_api_ms = (data.get("cost") or {}).get("total_api_duration_ms")
    out = ((data.get("context_window") or {}).get("current_usage") or {}).get("output_tokens", 0)
    if prev_api_ms is not None and cur_api_ms is not None:
        delta_ms = cur_api_ms - prev_api_ms
        if delta_ms >= 500 and out >= 20:
            tps = out / (delta_ms / 1000)
            if round(tps) > 0:  # 算出会显示成 0 的（output 小 / Δ 很大），不刷新、保持上次值
                return tps
    return prev_tps


def _read_transcript_totals(path):
    """解析会话 transcript 的累计 (input, output, cache) token，按 message_id:requestId 去重。

    数据来自 CC 源文件（stdin 的 transcript_path），约 1.8MB / 800 行长会话解析约 6ms；失败返回 (0,0,0)。
    """
    inp = out = cache = 0
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                if x.get("type") != "assistant":
                    continue
                k = f"{x.get('message', {}).get('id')}:{x.get('requestId')}"
                if k in seen:
                    continue
                seen.add(k)
                u = x.get("message", {}).get("usage", {})
                inp += u.get("input_tokens", 0)
                out += u.get("output_tokens", 0)
                cache += u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    except Exception:
        return 0, 0, 0
    return inp, out, cache


def render(data, now, tps=None):
    W = get_width()
    ctx = data.get("context_window") or {}
    cost = data.get("cost") or {}
    bar_w = 8 if W >= 100 else 6 if W >= 60 else 4

    # --- Line 1: Project | Total | Cost | Code（项目名原色，消耗/产出指标统一青色）---
    line1 = []

    project = (data.get("workspace") or {}).get("project_dir", "")
    if project:
        name = os.path.basename(project)
        branch = git_branch(project)
        if branch:
            inner = f"{C['branch']}{branch}{C['reset']}"
            added, deleted = git_diff_stat(project)
            if added:
                inner += f" {C['added']}+{added}{C['reset']}"
            if deleted:
                inner += f" {C['deleted']}-{deleted}{C['reset']}"
            line1.append(f"\033[1m{C['project']}[{name}]{C['reset']}({inner})")
        else:
            line1.append(f"\033[1m{C['project']}[{name}]{C['reset']}")

    # Total：会话累计（解析 transcript，CC 源数据，长会话约 6ms）；Total = in+out+cache
    tpath = data.get("transcript_path")
    if tpath:
        tin, tout, tcache = _read_transcript_totals(tpath)
        if tin or tout or tcache:
            line1.append(f"{C['total']}Total: {fmt_tokens(tin + tout + tcache)}{C['reset']}")

    # Cost（CC 自带累计，准确）
    usd = cost.get("total_cost_usd")
    if usd is not None:
        line1.append(f"{C['total']}Cost: ${usd:.2f}{C['reset']}")

    # Code：本会话 Claude 写/删的代码行数（标签青色，+/- 与 L1 git 变动同样的绿/红）
    lines_added = cost.get("total_lines_added", 0)
    lines_removed = cost.get("total_lines_removed", 0)
    if lines_added or lines_removed:
        line1.append(
            f"{C['total']}Code:{C['reset']} "
            f"{C['added']}+{lines_added}{C['reset']} {C['deleted']}-{lines_removed}{C['reset']}"
        )

    # 窄终端：宽度不够从尾部逐段去（保留项目名）
    while len(line1) > 1 and vlen(" | ".join(line1)) > W:
        line1.pop()

    # --- Line 2: Limit: 5h | 7d | Ctx ---
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
            f"{C['label']}{fmt_tokens(size)} Ctx:{C['reset']}{progress_bar(ctx['used_percentage'], bar_w)}",
            f"{C['label']}{fmt_tokens(size)} Ctx:{ctx['used_percentage']:.0f}%{C['reset']}",
        ]
    line2 = []
    if rl_parts or ctx_parts:
        for idx in (0, 1, 2):
            rl_seg = [p[idx] for p in rl_parts]
            ctx_seg = (ctx_parts[:1] if idx < 2 else ctx_parts[1:2]) if ctx_parts else []
            segs = rl_seg + ctx_seg
            if rl_seg:
                segs[0] = f"{C['label']}Limit:{C['reset']} {segs[0]}"
            if idx == 2 or vlen(" | ".join(segs)) <= W:
                line2 = segs
                break

    # --- Line 3: Tokens（上下文窗口 in/out/cache 构成，非会话累计）| TPS ---
    line3 = []
    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    cache_read = (ctx.get("current_usage") or {}).get("cache_read_input_tokens", 0)
    if total_in or total_out:
        tok = f"{C['tokens']}Tokens: in {fmt_tokens(total_in)}, out {fmt_tokens(total_out)}"
        if cache_read:
            tok += f", cache {fmt_tokens(cache_read)}"
        line3.append(tok + C['reset'])
    # 本轮 TPS（main 里算好传入）；无数据则不追加这一项，L3 整行空了由末尾 if line 隐藏整行
    if tps:
        line3.append(f"{C['tokens']}Out TPS: {tps:.0f} tokens/s{C['reset']}")
    while len(line3) > 1 and vlen(" | ".join(line3)) > W:
        line3.pop()

    # --- Line 4: Model | Duration | Remote ---
    line4 = []

    model_name = (data.get("model") or {}).get("display_name", "")
    if model_name:
        model_name = re.sub(r'\s*\(.*?\)', '', model_name)
        effort = (data.get("effort") or {}).get("level", "")
        if effort:
            model_name += f"/{effort}"
        model_name += f"/{'fast' if data.get('fast_mode') else 'nofast'}"
        line4.append(f"{C['model']}Model: {model_name}{C['reset']}")

    duration_ms = cost.get("total_duration_ms")
    if duration_ms and duration_ms > 0:
        line4.append(f"{C['duration']}Duration: {fmt_duration(duration_ms / 1000)}{C['reset']}")

    repo_host = ((data.get("workspace") or {}).get("repo") or {}).get("host", "")
    if repo_host:
        line4.append(f"{C['model']}Remote: {repo_host.rsplit('.', 1)[0]}{C['reset']}")

    while len(line4) > 1 and vlen(" | ".join(line4)) > W:
        line4.pop()

    output = [" | ".join(line) for line in (line1, line2, line3, line4) if line]
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

    # 尝试从第三方 provider 获取配额数据，覆盖 CC 自身上报的配额
    # load_rate_limits 优先返回已配置的第三方提供者数据，无则退回到官方数据
    try:
        from token_tracker.adapters.rate_limits import load_rate_limits as _load_provider
        _prov = _load_provider()
        if _prov:
            _prov_rl = {}
            if _prov.five_hour_pct is not None:
                _prov_rl["five_hour"] = {"used_percentage": _prov.five_hour_pct, "resets_at": _prov.five_hour_resets_at}
            if _prov.seven_day_pct is not None:
                _prov_rl["seven_day"] = {"used_percentage": _prov.seven_day_pct, "resets_at": _prov.seven_day_resets_at}
            if any((_prov_rl.get(k) or {}).get("used_percentage") is not None for k in ("five_hour", "seven_day")):
                data["rate_limits"] = _prov_rl
    except Exception:
        pass
    session_id = data.get("session_id") or ""
    prev_api_ms, prev_tps, state = _read_prev(session_id)  # 覆盖前读旧帧（按会话）
    tps = _compute_tps(data, prev_api_ms, prev_tps)
    if session_id:  # 本会话 TPS 状态并回 _tps_state（多会话共享文件互不清零；LRU 限 20 防膨胀）
        state.pop(session_id, None)
        state[session_id] = {"api": (data.get("cost") or {}).get("total_api_duration_ms"), "tps": tps}
        for k in list(state)[:-20]:
            del state[k]
        data["_tps_state"] = state
    save_data(data, now)
    render(data, now, tps)


if __name__ == "__main__":
    main()
'''


# Codex 伪 statusline（Stop hook + systemMessage）；每次回答后追加一行彩色 status。
# 实测：Stop 的 systemMessage 渲染 24-bit 真彩色 + 不进模型上下文（2026-06-19）。
# 配色暂 mocha 硬编码（CC statusline 烘焙值），未接主题系统——TUI 渲染区与 CC statusline 解耦。
CODEX_STATUSLINE_HOOK_SCRIPT = r'''#!/usr/bin/env python3
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
    """(branch, +A, -D)；失败/非 git/无 commit 返回 ("", 0, 0)。"""
    if not cwd:
        return "", 0, 0

    def run(args):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()

    try:
        branch = run(["branch", "--show-current"])
    except Exception:
        return "", 0, 0
    if not branch:
        return "", 0, 0
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
    return branch, a, d


def _render_project(cwd):
    if not cwd:
        return ""
    name = os.path.basename(cwd.rstrip("/"))
    branch, a, d = _git_status(cwd)
    if not branch:
        return f"{BOLD}{C['project']}[{name}]{RST}"
    inner = f"{C['branch']}{branch}{RST}"
    if a:
        inner += f" {C['added']}+{a}{RST}"
    if d:
        inner += f" {C['deleted']}-{d}{RST}"
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
        label = f"{model} {effort}" if effort else model
        line1.append(f"{C['total']}Model: {label}{RST}")  # 整体取 total 槽（mocha=red/红）

    # L2: Limit: 5h | 7d | <window> Ctx（仿 CC statusline，带进度条 + reset）
    line2 = []
    if rl and rl.five_hour_pct is not None:
        line2.append(_render_limit("5h", rl.five_hour_pct, rl.five_hour_resets_at, now_ts))
    if rl and rl.seven_day_pct is not None:
        line2.append(_render_limit("7d", rl.seven_day_pct, rl.seven_day_resets_at, now_ts))
    if ctx is not None:
        size = (info or {}).get("model_context_window") or 0
        prefix = f"{fmt_tokens(size)} " if size else ""
        line2.append(f"{C['label']}{prefix}Ctx {RST}{_bar(ctx)}")
    if line2:
        line2[0] = f"{C['label']}Limit:{RST} " + line2[0]

    lines = [" | ".join(x) for x in (line1, line2) if x]
    if lines:
        # 开头加 \n：Codex 把 systemMessage 包成 "warning:" 开头，让 status 内容另起一行、与之分开
        print(json.dumps({"systemMessage": "\n" + "\n".join(lines)}))


if __name__ == "__main__":
    main()
'''


# --- helpers ---

def _render_hook_script() -> str:
    """把 HOOK_VERSION + 当前主题 truecolor / 256 两套配色注入占位符，得到要落盘的状态栏脚本。"""
    name = config.resolve_theme()
    return (
        HOOK_SCRIPT
        .replace("__HOOK_VERSION__", HOOK_VERSION)
        .replace("__STATUSLINE_TRUECOLOR__", repr(themes.theme_to_statusline_ansi(name)))
        .replace("__STATUSLINE_COLOR256__", repr(themes.theme_to_statusline_ansi(name, "256")))
    )


def _render_codex_statusline_hook() -> str:
    """注入版本号 + 当前主题 statusline 配色（truecolor），得到要落盘的 Codex 伪 statusline 脚本。
    跟随主题：tt theme set 经 update_hook 重烘焙；不需 __TT_PYTHON__（脚本无 subprocess 调 tt）。"""
    name = config.resolve_theme()
    return (
        CODEX_STATUSLINE_HOOK_SCRIPT
        .replace("__STATUSLINE_HOOK_VERSION__", STATUSLINE_HOOK_VERSION)
        .replace("__STATUSLINE_TRUECOLOR__", repr(themes.theme_to_statusline_ansi(name)))
    )


def _write_codex_statusline_script() -> None:
    os.makedirs(_TT, exist_ok=True)
    with open(CODEX_STATUSLINE_HOOK_PATH, "w", encoding="utf-8") as f:
        f.write(_render_codex_statusline_hook())
    if os.name != "nt":
        os.chmod(CODEX_STATUSLINE_HOOK_PATH,
                 os.stat(CODEX_STATUSLINE_HOOK_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _installed_codex_statusline_version() -> str | None:
    try:
        with open(CODEX_STATUSLINE_HOOK_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


# 卸载时定位 tt 追加的整段 [[hooks.Stop]]——同时认新（codex-statusline）/ 旧（tt-statusline）两种特征码
_CODEX_STATUSLINE_REGEX = re.compile(
    r'\n*\[\[hooks\.Stop\]\]\s*'
    r'\[\[hooks\.Stop\.hooks\]\]\s*'
    r'type = "command"\s*'
    # command 兼容双引号 basic string（老用户已装）和单引号 literal string（修了 Windows 路径转义后的新装）
    r'command = ["\'][^"\']*(?:codex-statusline|tt-statusline)[^"\']*["\']\s*'
    r'timeout = \d+\s*'
)


def _has_tt_codex_statusline(content: str) -> bool:
    return "codex-statusline" in content or "tt-statusline" in content


def _install_codex_statusline(content: str, python: str) -> str:
    """落盘 Codex statusline 脚本 + 在 config.toml 末尾追加 Stop hook 段。
    - 新名 codex-statusline 段已存在 + python 路径一致 → 幂等返回；
    - 已存在但 python 路径不一致（用户升级 Python / 切换 conda/venv / 卸载某环境后跑 tt setup）→ 删旧装新，
      避免 command 指向已死 python（症状：脚本 import token_tracker 失败被 try/except 吞掉，状态栏只剩项目名）；
    - 只存在旧名 tt-statusline → 清掉后追加新路径段（老用户迁移）。"""
    _write_codex_statusline_script()
    cmd = f"{python} {CODEX_STATUSLINE_HOOK_PATH}"
    if "codex-statusline" in content or "tt-statusline" in content:
        # 从已有的 command 行提取 python 路径（兼容 basic / literal string 包裹）
        m = re.search(
            r"command = [\"']([^ ]+) [^\"']*(?:codex-statusline|tt-statusline)[^\"']*[\"']",
            content,
        )
        if m and m.group(1) == python and "codex-statusline" in content:
            return content  # python 一致 + 已是新名 → 幂等
        content = _CODEX_STATUSLINE_REGEX.sub("\n", content)  # 路径不一致或老名残留 → 删旧段
    return content.rstrip() + (
        "\n\n[[hooks.Stop]]\n\n"
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        # 用 TOML literal string（单引号）包裹 command，避免 Windows 反斜杠路径被当转义符
        # 解析失败（如 `C:\Users\...` 里的 `\U` 被识别为 unicode 转义起始）
        f"command = '{cmd}'\n"
        "timeout = 10\n"
    )


def _uninstall_codex_statusline(content: str) -> str:
    """删 Codex statusline 脚本 + 从 content 移除 tt 追加的 Stop hook 段（不动用户其它）。"""
    if os.path.exists(CODEX_STATUSLINE_HOOK_PATH):
        os.remove(CODEX_STATUSLINE_HOOK_PATH)
    return _CODEX_STATUSLINE_REGEX.sub("\n", content)


def _read_codex_config() -> tuple[str, dict] | None:
    try:
        with open(CODEX_CONFIG, encoding="utf-8") as f:
            content = f.read()
        return content, tomllib.loads(content)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def codex_statusline_active() -> bool:
    """双因素：用户意图（config）AND 实际装好（脚本文件 + config.toml 含特征码）。任一不满足 → False。"""
    if config.codex_faux_statusline_intent() is not True:
        return False
    if not os.path.exists(CODEX_STATUSLINE_HOOK_PATH):
        return False
    try:
        with open(CODEX_CONFIG, encoding="utf-8") as f:
            return _has_tt_codex_statusline(f.read())
    except OSError:
        return False


def is_setup() -> bool:
    """已配置 = CC 端 statusLine 指我们脚本（如装了 CC）AND Codex 端意图明确（如装了 Codex）。
    Codex 端意图为 True 时还要文件实装好；意图 False 则用户明确不要、不强求文件存在。"""
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)
    if not has_cc and not has_codex:
        return False
    if has_cc:
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
                settings = json.load(f)
            sl = settings.get("statusLine")
            if not isinstance(sl, dict) or not _is_tt_cc_command(sl.get("command") or ""):
                return False
        except (OSError, json.JSONDecodeError):
            return False
    if has_codex:
        intent = config.codex_faux_statusline_intent()
        if intent is None:  # 没跑过 wizard、没表达意图 → 视为未配
            return False
        # intent True 时双因素都要满足；intent False 时用户明确不要、不强求文件
        if intent and not codex_statusline_active():
            return False
    return True


def _installed_hook_version() -> str | None:
    try:
        with open(HOOK_SCRIPT_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


def _is_tt_cc_command(cmd: str) -> bool:
    """命令是否为 tt 的 CC statusline——认新 `claude-statusline` 与旧 `tt-statusline`（迁移识别用）。"""
    return "claude-statusline" in cmd or "tt-statusline" in cmd


def _write_cc_statusline_script() -> None:
    """渲染并落盘 CC statusline 脚本（mkdir + 执行权限）。"""
    os.makedirs(_TT, exist_ok=True)
    with open(HOOK_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(_render_hook_script())
    if os.name != "nt":
        os.chmod(HOOK_SCRIPT_PATH,
                 os.stat(HOOK_SCRIPT_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _migrate_legacy() -> None:
    """删旧位置（agent 根目录）的 tt 脚本 / 缓存 / 备份——迁到 ~/.config/token-tracker 后清残留。"""
    for p in _LEGACY_PATHS:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def needs_update() -> bool:
    # 检查 CC 脚本版本
    if os.path.exists(HOOK_SCRIPT_PATH) and _installed_hook_version() != HOOK_VERSION:
        return True
    # 检查 Codex 脚本版本
    sv = _installed_codex_statusline_version()
    if sv is not None and sv != STATUSLINE_HOOK_VERSION:
        return True
    # 检查 settings.json 是否指向旧路径
    if _cc_settings_path_stale():
        return True
    return False


def _cc_settings_path_stale() -> bool:
    """检测 settings.json 的 statusLine command 是否指向旧路径。"""
    if not os.path.exists(CLAUDE_SETTINGS):
        return False
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        sl = settings.get("statusLine")
        if not isinstance(sl, dict):
            return False
        cmd = sl.get("command", "")
        if not _is_tt_cc_command(cmd):
            return False
        expected_cmd = f"{sys.executable or 'python3'} {HOOK_SCRIPT_PATH}"
        return cmd != expected_cmd
    except (OSError, json.JSONDecodeError):
        return False


def _fix_cc_settings_path() -> None:
    """修复 settings.json 的 statusLine command 到当前脚本路径。"""
    if not os.path.exists(CLAUDE_SETTINGS):
        return
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        sl = settings.get("statusLine")
        if not isinstance(sl, dict):
            return
        cmd = sl.get("command", "")
        if not _is_tt_cc_command(cmd):
            return
        expected_cmd = f"{sys.executable or 'python3'} {HOOK_SCRIPT_PATH}"
        if cmd == expected_cmd:
            return
        settings["statusLine"] = {"type": "command", "command": expected_cmd}
        with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except (OSError, json.JSONDecodeError):
        pass


def update_hook() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):
        _write_cc_statusline_script()
    if _installed_codex_statusline_version() is not None:
        _write_codex_statusline_script()
    _fix_cc_settings_path()


# --- setup ---

def setup(auto: bool = False, components: SetupComponents | None = None, quiet: bool = False) -> None:
    """安装状态栏 + 可选组件。components=None 表示全装（向后兼容）。
    quiet=True 时不打任何提示（wizard 场景：由 wizard 末尾给一次综合总结）。"""
    if components is None:
        components = SetupComponents.all_on()
    p = (lambda *a, **k: None) if quiet else get_console().print

    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)

    if not has_cc and not has_codex:
        p(f"[red]{t('no_agent_install')}[/red]")
        return

    if auto:
        p(f"[dim]{t('first_setup')}[/dim]")

    os.makedirs(_TT, exist_ok=True)  # tt 自己的目录
    _migrate_legacy()                # 删旧位置（agent 根目录）残留，迁到 ~/.config/token-tracker

    if has_cc:
        _setup_claude(quiet)
    else:
        if not auto:
            p(f"[dim]{t('cc_not_found')}[/dim]")

    if has_codex:
        _setup_codex(components, quiet)
    else:
        if not auto:
            p(f"[dim]{t('codex_not_found')}[/dim]")

    # setup 真正落地了，写入当前引导版本——后续启动 cli 不再触发"老用户重新引导"。
    # early-return 分支（无 agent）不会到这，符合语义。
    config.save_setup_version()


def _migrate_cc_legacy_backup(settings: dict) -> None:
    """老用户的 statusLine 备份藏在 settings.json 的 `tokenTracker.previousStatusLine` 子字段——
    挪到 ~/.config/token-tracker/cc-backup.json，同时清掉 settings 子字段（不污染 agent 配置）。"""
    legacy = settings.pop("tokenTracker", None)
    if isinstance(legacy, dict) and isinstance(legacy.get("previousStatusLine"), dict):
        os.makedirs(_TT, exist_ok=True)
        with open(CC_BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump({"statusLine": legacy["previousStatusLine"]}, f, indent=2)


def _setup_claude(quiet: bool = False) -> None:
    p = (lambda *a, **k: None) if quiet else get_console().print
    _write_cc_statusline_script()

    settings: dict = {}
    if os.path.exists(CLAUDE_SETTINGS):
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)

    _migrate_cc_legacy_backup(settings)  # 老用户：把藏在 settings 里的备份挪到 cc-backup.json

    existing = settings.get("statusLine")
    if existing and not _is_tt_cc_command(existing.get("command") or ""):
        # 用户原 statusLine 备份到独立文件，不污染 agent 配置
        p(f"[yellow]{t('sl_backup_replace')}[/yellow]")
        os.makedirs(_TT, exist_ok=True)
        with open(CC_BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump({"statusLine": existing}, f, indent=2)

    python = sys.executable or "python3"
    settings["statusLine"] = {"type": "command", "command": f"{python} {HOOK_SCRIPT_PATH}"}

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    p(f"[green]✓[/green] {t('cc_configured')}")
    p(f"[dim]{t('restart_cc')}[/dim]")


def _setup_codex(components: SetupComponents, quiet: bool = False) -> None:
    """Codex 端只装/卸伪 statusline hook，**不再动 [tui].status_line**——伪 statusline 比官方更全。
    用户意图（components.codex_faux_statusline）也写入 config.json，给 wizard 总结 / is_setup 用。"""
    p = (lambda *a, **k: None) if quiet else get_console().print
    result = _read_codex_config()
    if result:
        content, _parsed = result
    elif os.path.isdir(CODEX_DIR):
        content = ""  # 装了 Codex 但还没 config.toml → 新建
    else:
        return

    config.save_codex_faux_statusline(components.codex_faux_statusline)  # 写入意图

    python = sys.executable or "python3"
    if components.codex_faux_statusline:
        content = _install_codex_statusline(content, python)
    else:
        content = _uninstall_codex_statusline(content)

    with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)

    p(f"[green]✓[/green] {t('codex_configured')}")
    if components.codex_faux_statusline:
        p(f"[dim]{t('codex_statusline_hint')}[/dim]")
    p(f"[dim]{t('restart_codex')}[/dim]")


# --- unsetup ---

def unsetup() -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)

    if has_cc:
        _unsetup_claude()
    if has_codex:
        _unsetup_codex()
    if not has_cc and not has_codex:
        get_console().print(f"[dim]{t('no_agent_detected')}[/dim]")


def _unsetup_claude() -> None:
    _migrate_legacy()  # 顺手清旧位置残留（老用户 unsetup 时也清）
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
        get_console().print(f"[green]✓[/green] {t('deleted_file', path=HOOK_SCRIPT_PATH)}")

    if not os.path.exists(CLAUDE_SETTINGS):
        return

    with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
        settings = json.load(f)

    sl = settings.get("statusLine")
    if isinstance(sl, dict) and _is_tt_cc_command(sl.get("command") or ""):
        previous = None
        if os.path.exists(CC_BACKUP_PATH):  # 新位置（独立文件）
            with open(CC_BACKUP_PATH, encoding="utf-8") as f:
                previous = json.load(f).get("statusLine")
            os.remove(CC_BACKUP_PATH)
        if isinstance(previous, dict):
            settings["statusLine"] = previous
            get_console().print(f"[green]✓[/green] {t('cc_restored')}")
        else:
            settings.pop("statusLine", None)
            get_console().print(f"[green]✓[/green] {t('cc_removed')}")
        settings.pop("tokenTracker", None)  # 顺手清掉老用户在 settings 里的子字段残留
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
            get_console().print(f"[green]✓[/green] {t('deleted_cache', path=STATUS_FILE)}")

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _unsetup_codex() -> None:
    """卸载 Codex 端：移除伪 statusline hook + 脚本。
    老用户残留：如有 codex-backup.json（旧版我们改过 status_line），恢复原值；新版不再动 status_line。"""
    result = _read_codex_config()
    if not result:
        return
    content, _parsed = result

    # 清伪 statusline（脚本 + hook 段）
    content = _uninstall_codex_statusline(content)

    # 兼容老用户：旧版我们曾接管 status_line + 写 codex-backup.json。这里恢复 + 删 backup。
    if os.path.exists(CODEX_BACKUP_LEGACY):
        try:
            with open(CODEX_BACKUP_LEGACY, encoding="utf-8") as f:
                old_items = json.load(f).get("status_line")
            if isinstance(old_items, list):
                body = ",\n".join(f'  "{item}"' for item in old_items)
                new_sl = f"status_line = [\n{body},\n]"
                content = re.sub(r'status_line\s*=\s*\[.*?\]', new_sl, content, flags=re.DOTALL)
            elif old_items is None:
                content = re.sub(r'status_line\s*=\s*\[.*?\]\n?', '', content, flags=re.DOTALL)
            os.remove(CODEX_BACKUP_LEGACY)
            get_console().print(f"[green]✓[/green] {t('codex_restored')}")
        except (OSError, json.JSONDecodeError):
            pass

    with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)
