#!/usr/bin/env python3
"""Claude Code statusLine — 状态栏显示 + 数据持久化到 tt-status.json"""
__version__ = "__HOOK_VERSION__"
import json, os, re, subprocess, sys, tempfile, unicodedata
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
    """读旧 tt-status.json：本会话 (api_duration_ms, last_tps) + 全量 _tps_state + _terminal_map
    + _tx_cache（transcript 解析缓存）。

    tt-status.json 是多会话共享单文件、会被其它会话覆盖；TPS 差分按 session_id 存进
    _tps_state dict、各会话互不干扰。返回 state / term_map 让本帧把自己的状态并回去、不丢别的会话。
    """
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return None, None, {}, {}, {}
    state = old.get("_tps_state")
    if not isinstance(state, dict):
        state = {}
    term_map = old.get("_terminal_map")
    if not isinstance(term_map, dict):
        term_map = {}
    tx_cache = old.get("_tx_cache")
    if not isinstance(tx_cache, dict):
        tx_cache = {}
    if session_id and session_id in state:
        s = state.get(session_id) or {}
        return s.get("api"), s.get("tps"), state, term_map, tx_cache
    if session_id and old.get("session_id") == session_id:  # 兼容升级前的旧单会话帧
        return (old.get("cost") or {}).get("total_api_duration_ms"), old.get("_last_tps"), state, term_map, tx_cache
    return None, None, state, term_map, tx_cache


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


# 上下文估算常数：用本机全部会话的真实上报 input 校准（锚点增量法，578 个采样点）。
# chars/token=1/3、每图≈1500tok 时中位误差 0.4%；对系数不敏感（2.2~4.2 误差都 <2%）。
_CHARS_PER_TOKEN = 3.0
_IMAGE_TOKENS = 1500


def _content_chars(content, imgs):
    """消息内容折算"上下文原料"字符量：text / tool_use 入参 / tool_result 文本。
    thinking 不计（CC 不回传历史 thinking，也不占后续请求上下文）；image 计数单独折算。"""
    if isinstance(content, str):
        return len(content)
    n = 0
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                n += len(b.get("text") or "")
            elif t == "tool_use":
                n += len(json.dumps(b.get("input") or {}, ensure_ascii=False))
            elif t == "tool_result":
                cc = b.get("content")
                n += len(cc) if isinstance(cc, str) else _content_chars(cc, imgs)
            elif t == "image":
                imgs[0] += 1
    return n


def _parse_transcript(path):
    """一次解析会话 transcript，产出状态栏全部派生指标；无数据返回 {}。

    - tin/tout/tcache: 会话累计 token（usage 按 message_id:requestId 去重——CC 每个内容块
      拆一行、usage 相同）→ L1 Total
    - est:     当前上下文 token 估算（锚点增量法）→ L2 Ctx% / L3 in
    - lin/lout/lcr/lcc: 最近一次真实上报的 usage → L3 out/cache/命中率兜底
    - tps:     最近一轮 output ÷ 该消息首末行时间戳差 → L3 TPS 兜底

    为什么需要 est/TPS 兜底：部分代理网关（GLM 等）会在会话中段起把 usage 的
    input/cache 字段清零（output 保留），CC 的 used_percentage 只看最后一条响应、
    total_api_duration_ms 也可能恒 0——这些指标随之全废。est 用锚点增量法：以上一次
    真实上报的 input 为基点，只对其后的增量字符按 1/3 折算（比全量折算准一个量级，
    系统提示/工具骨架不随字符线性走）。compact_boundary 处锚点与字符一并清零。
    """
    tin = tout = tcache = 0
    seen = set()    # usage 去重（id:requestId）
    rseen = set()   # 行 uuid 去重（resume/rewrite 会整行重写，字符会重复计）
    chars = 0
    imgs = [0]
    anchor = None   # (chars, imgs, input) 最近一次真实上报点
    last = None     # (in, out, cache_read, cache_creation) 最近一次 input>0 的上报
    last_out = 0    # 最近一次 output（input 被网关清零时仍在上报，单独跟踪避免展示过时值）
    groups = {}     # msg_id -> [首行 ts, 末行 ts, out]，TPS 兜底用
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return {}
    with f:
        for line in f:
            try:
                x = json.loads(line)
            except Exception:
                continue
            t = x.get("type")
            if t == "system":
                if x.get("subtype") == "compact_boundary":
                    chars = 0
                    imgs[0] = 0
                    anchor = None
                    last = None
                continue
            if t == "assistant":
                m = x.get("message") or {}
                u = m.get("usage") or {}
                k = f"{m.get('id')}:{x.get('requestId')}"
                if k not in seen:
                    seen.add(k)
                    tin += u.get("input_tokens") or 0
                    tout += u.get("output_tokens") or 0
                    tcache += (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            ru = x.get("uuid")
            if not ru or ru in rseen:
                continue
            rseen.add(ru)
            if x.get("isSidechain"):
                continue  # 子代理独立上下文：不计 est/锚点/TPS（usage 已计入上面的 Total）
            m = x.get("message") or {}
            chars += _content_chars(m.get("content"), imgs)
            if t != "assistant":
                continue
            u = m.get("usage") or {}
            out = u.get("output_tokens") or 0
            mid = m.get("id")
            if mid:
                ts = x.get("timestamp") or ""
                g = groups.get(mid)
                if g is None:
                    groups[mid] = [ts, ts, out]
                elif ts:
                    g[1] = ts
                    g[2] = g[2] or out
            in_tot = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                      + (u.get("cache_creation_input_tokens") or 0))
            if in_tot > 0:  # 真实上报：刷新锚点与 last
                last = (in_tot, out, u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0)
                anchor = (chars, imgs[0], in_tot)
            if out:
                last_out = out
    if anchor:
        est = anchor[2] + (chars - anchor[0]) / _CHARS_PER_TOKEN + (imgs[0] - anchor[1]) * _IMAGE_TOKENS
    else:
        est = chars / _CHARS_PER_TOKEN + imgs[0] * _IMAGE_TOKENS
    out_d = {}
    if tin or tout or tcache:
        out_d.update(tin=tin, tout=tout, tcache=tcache)
    if est > 0:
        out_d["est"] = max(0, int(est))
    if last:
        out_d.update(lin=last[0], lout=last_out or last[1], lcr=last[2], lcc=last[3])
    elif last_out:
        out_d["lout"] = last_out
    for ts0, ts1, gout in reversed(groups.values()):  # dict 保序：从最近的消息往回找
        if not (gout and ts0 and ts1):
            continue
        try:
            span = (datetime.fromisoformat(ts1.replace("Z", "+00:00"))
                    - datetime.fromisoformat(ts0.replace("Z", "+00:00"))).total_seconds()
        except Exception:
            continue
        if span >= 0.5:  # 时长过短算出来会爆大，跳过、沿用上一轮
            out_d["tps"] = gout / span
            break
    return out_d


def _parse_transcript_cached(path, cache):
    """带 mtime 缓存的 transcript 解析。statusline 在每次按键/轮询都会被调，而大 transcript
    解析要几十 ms——按 (mtime, size) 缓存上次结果进 tt-status.json，未变化时直接复用。"""
    try:
        st = os.stat(path)
        key = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return {}
    hit = cache.get(path)
    if isinstance(hit, dict) and hit.get("key") == key:
        return hit.get("data") or {}
    data = _parse_transcript(path)
    if data:
        cache[path] = {"key": key, "data": data}
        for k in list(cache)[:-4]:  # 只留最近几个会话，防跨会话膨胀
            del cache[k]
    return data


def render(data, now, tps=None, tx=None):
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

    # Total：会话累计（解析 transcript，CC 源数据，mtime 缓存未变时 ~0ms）；Total = in+out+cache
    if tx is None:
        tpath = data.get("transcript_path")
        tx = _parse_transcript_cached(tpath, {}) if tpath else {}
    if tx:
        line1.append(f"{C['total']}Total: {fmt_tokens(tx['tin'] + tx['tout'] + tx['tcache'])}{C['reset']}")

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
    size = ctx.get("context_window_size", 0)
    cc_pct = ctx.get("used_percentage")
    est_pct = tx.get("est") and size and tx["est"] / size * 100
    if cc_pct is not None and (cc_pct > 0 or not est_pct):
        # CC 官方值可用（>0，或没有估算兜底）——直接用
        ctx_parts = [
            f"{C['label']}{fmt_tokens(size)} Ctx:{C['reset']}{progress_bar(cc_pct, bar_w)}",
            f"{C['label']}{fmt_tokens(size)} Ctx:{cc_pct:.0f}%{C['reset']}",
        ]
    elif est_pct:
        # CC 值为 0（代理网关把最后一条响应的 usage 清零）——用 transcript 锚点增量估算兜底
        ctx_parts = [
            f"{C['label']}{fmt_tokens(size)} Ctx:{C['reset']}{progress_bar(est_pct, bar_w)}",
            f"{C['label']}{fmt_tokens(size)} Ctx:{est_pct:.0f}%{C['reset']}",
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

    # --- Line 3: Tokens（上下文窗口 in/out/cache 构成，非会话累计）| 命中率 | TPS ---
    line3 = []
    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    cache_read = (ctx.get("current_usage") or {}).get("cache_read_input_tokens", 0)
    # 代理网关清零 usage 时（total/current 全 0），用 transcript 最近一次真实上报兜底；
    # 上下文估算可用时 in 用估算值（比 last上报 更接近当前真实上下文）
    est = tx.get("est")
    if est and est > total_in:
        total_in = est
    if not total_in and tx.get("lin"):
        total_in = tx["lin"]
    if not total_out and tx.get("lout"):
        total_out = tx["lout"]
    if not cache_read and tx.get("lcr"):
        cache_read = tx["lcr"]
    if total_in or total_out:
        tok = f"{C['tokens']}Tokens: in {fmt_tokens(total_in)}, out {fmt_tokens(total_out)}"
        if cache_read:
            tok += f", cache {fmt_tokens(cache_read)}"
        line3.append(tok + C['reset'])
    # 缓存命中率 = 最近真实上报里 cache_read ÷ 全部输入（输入侧计费口径）；无 cache 上报则不显示
    lcr, lcc = tx.get("lcr", 0), tx.get("lcc", 0)
    lin = tx.get("lin", 0)
    if lin and (lcr or lcc):
        hit = lcr / (lin + lcr + lcc) * 100
        line3.append(f"{C['tokens']}Cache: {hit:.0f}%{C['reset']}")
    # 本轮 TPS（main 里从 CC 的 api_duration 差分算好传入）；网关不给 duration 时
    # 用 transcript 该消息首末行时间戳差兜底；都无则不显示
    if not tps and tx.get("tps"):
        tps = tx["tps"]
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
    prev_api_ms, prev_tps, state, term_map, tx_cache = _read_prev(session_id)  # 覆盖前读旧帧（按会话）
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
    tpath = data.get("transcript_path")
    tx = _parse_transcript_cached(tpath, tx_cache) if tpath else {}
    data["_tx_cache"] = tx_cache
    save_data(data, now)
    render(data, now, tps, tx)


if __name__ == "__main__":
    main()
