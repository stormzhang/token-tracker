#!/usr/bin/env python3
"""Claude Code statusLine — 状态栏显示 + 数据持久化到 tt-status.json"""
__version__ = "__HOOK_VERSION__"
import json, os, re, subprocess, sys, tempfile, unicodedata
from datetime import datetime, timezone

STATUS_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-status.json")
ANSI_RE = re.compile(r'\033\[[0-9;]*m')
# Limit 行的分隔线分粗细：共用同一个重置窗口的相邻段之间用细线，其余用粗线。加入按模型的
# 周额度后 Limit 行有了「7d 与该额度同属一个周窗口」这层结构，靠粗细区分才不会读成并列。
# 两者可见宽度都与旧的 " | " 相同（3 列），窄终端的收窄判断无回归。
SEP_LIMIT = " \033[2m\u2503\033[0m "
SEP_LIMIT_GROUPED = " \033[2m\u2502\033[0m "
# 缓存超过这个秒数就认为过期（与 Claude Code 自己判定 cachedUsageUtilization 的阈值一致）
SCOPED_QUOTA_STALE_S = 3600
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
    # 显示宽度：东亚全角/宽字符占 2 列（wizard.py 已用同一规则）。项目名/分支/模型名含
    # CJK 时，按字符数少算会让窄终端的收窄判断失准、行溢出折行——按列宽计才准。
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
               for ch in ANSI_RE.sub("", s))


def get_width():
    # COLUMNS：Claude Code 会为 statusLine 子进程设置真实列宽（CC v2.1.153+）。该子进程的
    # stdin/stderr 是管道，get_terminal_size/dev-tty 都探测不到，故优先用它。与 ui/console.py
    # 同规矩：只认有效正整数（`!` 子进程占位的 "0"、非数字都忽略，回落原有探测链，无回归）。
    try:
        c = os.environ.get("COLUMNS")
        if c and c.isdigit():
            w = int(c)
            if w > 0:
                return max(1, w - 4)
    except Exception:
        pass
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
    """相对 HEAD 的未提交增删行数 + 未跟踪文件数（已跟踪改动按行、未跟踪按文件计数）。
    失败/无 commit 返回 (0, 0, 0)。"""
    added = deleted = 0
    try:
        out = subprocess.check_output(
            ["git", "diff", "HEAD", "--numstat"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        )
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            a, d = parts[0], parts[1]
            if a.isdigit():
                added += int(a)
            if d.isdigit():
                deleted += int(d)
    except Exception:
        pass
    untracked = 0
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        )
        untracked = sum(1 for ln in out.splitlines() if ln.strip())
    except Exception:
        pass
    return added, deleted, untracked


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
    """读旧 tt-status.json：本会话 (api_duration_ms, last_tps) + 全量 _tps_state + _terminal_map。

    tt-status.json 是多会话共享单文件、会被其它会话覆盖；TPS 差分按 session_id 存进
    _tps_state dict、各会话互不干扰。返回 state / term_map 让本帧把自己的状态并回去、不丢别的会话。
    """
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return None, None, {}, {}
    state = old.get("_tps_state")
    if not isinstance(state, dict):
        state = {}
    term_map = old.get("_terminal_map")
    if not isinstance(term_map, dict):
        term_map = {}
    if session_id and session_id in state:
        s = state.get(session_id) or {}
        return s.get("api"), s.get("tps"), state, term_map
    if session_id and old.get("session_id") == session_id:  # 兼容升级前的旧单会话帧
        return (old.get("cost") or {}).get("total_api_duration_ms"), old.get("_last_tps"), state, term_map
    return None, None, state, term_map


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


def _join_limit_segs(segs):
    """segs: [(text, grouped_with_previous)]，grouped 的用细线接到前一段。
    只有真的出现成组的相邻段时，其余分隔才升级为粗线——没有按模型额度可显示的用户
    （拿不到 weekly_scoped 桶的账号）看到的仍是原来的 " | "，零视觉变化。"""
    if not segs:
        return ""
    normal = SEP_LIMIT if any(grouped for _, grouped in segs[1:]) else " | "
    out = segs[0][0]
    for text, grouped in segs[1:]:
        out += (SEP_LIMIT_GROUPED if grouped else normal) + text
    return out


def _claude_config_dir(data):
    """定位当前账号的 Claude Code config dir。transcript_path 最可靠（它就在 config dir 下），
    CLAUDE_CONFIG_DIR 与 ~/.claude 兜底。注意**不要**解析 symlink：projects/ 可能被指到共享
    目录，解析后会走出 config dir 一路上溯到 $HOME，命中旧版本遗留的 ~/.claude.json。"""
    home = os.path.expanduser("~")
    transcript = data.get("transcript_path") or ""
    if transcript:
        d = os.path.dirname(os.path.abspath(transcript))
        while d and d not in (home, os.path.dirname(home)):
            if os.path.exists(os.path.join(d, ".claude.json")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")


def _scoped_quota(data, now_ts):
    """按模型计的周额度（当前是 Fable）。statusLine 的 payload 里没有这个桶——构造 payload 的
    那段只 spread five_hour / seven_day / spend_limit。但 Claude Code 会把 /api/oauth/usage 的
    响应缓存到 <config dir>/.claude.json，读缓存即可，不联网、不接触 OAuth token。
    返回 (label, pct, resets_at, stale)，取不到则 None。"""
    try:
        with open(os.path.join(_claude_config_dir(data), ".claude.json"), encoding="utf-8") as f:
            cached = json.load(f).get("cachedUsageUtilization") or {}
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None  # 文件不存在，或读到 CC 写了一半的内容——静默略过，不影响其余段
    hit = None
    for entry in ((cached.get("utilization") or {}).get("limits") or []):
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        if entry.get("percent") is None:
            continue
        name = ((entry.get("scope") or {}).get("model") or {}).get("display_name")
        if name:
            hit = (name, entry)
            break
    if hit is None:
        return None
    label, entry = hit

    resets_at = None
    raw = entry.get("resets_at")
    if isinstance(raw, str) and raw:
        try:
            resets_at = int(datetime.fromisoformat(raw).timestamp())
        except ValueError:
            resets_at = None

    fetched = cached.get("fetchedAtMs")
    stale = (not isinstance(fetched, (int, float))
             or (now_ts * 1000 - fetched) > SCOPED_QUOTA_STALE_S * 1000)
    return label, float(entry["percent"]), resets_at, stale


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
            added, deleted, untracked = git_diff_stat(project)
            if added:
                inner += f" {C['added']}+{added}{C['reset']}"
            if deleted:
                inner += f" {C['deleted']}-{deleted}{C['reset']}"
            if untracked:
                inner += f" {C['untracked']}?{untracked}{C['reset']}"
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

    # --- Line 2: Limit: 5h ┃ 7d │ <model> ┃ Ctx（无按模型额度时退回 5h | 7d | Ctx）---
    rl = data.get("rate_limits") or {}
    quota = _scoped_quota(data, now.timestamp())
    seven = rl.get("seven_day") or {}

    # 7d 与按模型的周额度是同一个周窗口，重置倒计时相同；重复打两遍纯属噪音，
    # 只在这一对的后者上打一次。窗口真的不同（或缓存里没有 resets_at）时各打各的。
    share_reset = False
    if quota is not None and quota[2] is not None and seven.get("resets_at"):
        share_reset = abs(int(seven["resets_at"]) - int(quota[2])) <= 120

    def _limit_tiers(label, pct, resets_at, with_reset, suffix=""):
        """一段的三档渲染：带倒计时 / 不带 / 只留百分数。"""
        reset_str = ""
        if with_reset and resets_at:
            remain = int(resets_at) - int(now.timestamp())
            if remain > 0:
                reset_str = f" \033[2m{C['label']}({fmt_duration(remain)}){C['reset']}"
        return (
            f"{C['label']}{label}:{C['reset']}{progress_bar(pct, bar_w)}{suffix}{reset_str}",
            f"{C['label']}{label}:{C['reset']}{progress_bar(pct, bar_w)}{suffix}",
            f"{C['label']}{label}:{pct:.0f}%{suffix}{C['reset']}",
        )

    # (三档渲染, 是否与前一段共用重置窗口, 是否是按模型的额度段)
    rl_parts = []
    for key, label in [("five_hour", "5h"), ("seven_day", "7d")]:
        entry = rl.get(key) or {}
        pct = entry.get("used_percentage")
        if pct is not None:
            rl_parts.append((
                _limit_tiers(label, pct, entry.get("resets_at"),
                             not (key == "seven_day" and share_reset)),
                False, False,
            ))
    if quota is not None:
        q_label, q_pct, q_reset, q_stale = quota
        rl_parts.append((
            _limit_tiers(q_label, q_pct, q_reset, True,
                         f"\033[2m~{C['reset']}" if q_stale else ""),
            share_reset, True,
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
        # 三档收窄；最后一档仍放不下时，丢掉按模型的额度段（5h / 7d / Ctx 优先保住）
        for idx, drop_quota in ((0, False), (1, False), (2, False), (2, True)):
            kept = [p for p in rl_parts if not (drop_quota and p[2])]
            segs = [(tiers[idx], grouped) for tiers, grouped, _ in kept]
            if ctx_parts:
                segs.append((ctx_parts[0] if idx < 2 else ctx_parts[1], False))
            if kept and segs:
                segs[0] = (f"{C['label']}Limit:{C['reset']} {segs[0][0]}", segs[0][1])
            joined = _join_limit_segs(segs)
            if drop_quota or vlen(joined) <= W:
                line2 = [joined] if joined else []
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
    session_id = data.get("session_id") or ""
    prev_api_ms, prev_tps, state, term_map = _read_prev(session_id)  # 覆盖前读旧帧（按会话）
    tps = _compute_tps(data, prev_api_ms, prev_tps)
    if session_id:  # 本会话 TPS 状态并回 _tps_state（多会话共享文件互不清零；LRU 限 20 防膨胀）
        state.pop(session_id, None)
        state[session_id] = {"api": (data.get("cost") or {}).get("total_api_duration_ms"), "tps": tps}
        for k in list(state)[:-20]:
            del state[k]
        # 终端定位（tt sidebar 点击跳转用）：statusline 是 CC 子进程，继承会话所在
        # 终端的环境——iTerm2 每窗格注入 ITERM_SESSION_ID、tmux 注入 TMUX_PANE。
        # 按 session_id 并回共享 map（同 _tps_state 的合并/LRU 语义）。
        term = {}
        if os.environ.get("ITERM_SESSION_ID"):
            term["iterm"] = os.environ["ITERM_SESSION_ID"]
        if os.environ.get("TMUX_PANE"):
            term["tmux"] = os.environ["TMUX_PANE"]
        term_map.pop(session_id, None)
        term_map[session_id] = term
        for k in list(term_map)[:-20]:
            del term_map[k]
    # 共享状态**无条件**随帧携带落盘（哪怕本帧无 session_id）——否则一条异常帧
    # 就会把别的会话攒的 TPS 状态与终端映射整个清掉（曾被无 session_id 的测试帧清表）
    data["_tps_state"] = state
    data["_terminal_map"] = term_map
    save_data(data, now)
    render(data, now, tps)


if __name__ == "__main__":
    main()
