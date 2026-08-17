#!/usr/bin/env python3
"""token-tracker Kimi Code statusline（tui.toml [status_line].command）：渲染一行会话状态。
[项目](分支* +A -D ?U) | Total: <会话累计 token> | Cost: $<累计成本> | 5h:<bar> X% (3h55m) | 7d:<bar> Y% (4d21h) | Model: <模型>/<effort>/<权限模式>
（字段、顺序、配色与 CC statusline 同风格；Kimi 只取 stdout 首行（二进制 runStatusLineCommand 硬编码截断），故压成一行。
5h/7d 限额走云端 GET <provider.base_url>/usages（OAuth access_token，同 CLI /usage 端点）：
渲染只读本地配额缓存、零网络；缓存超 120s 派生 detached 子进程后台刷新，token 过期/失败就整段不显示）
数据：model/cwd/gitBranch/permissionMode/sessionId 取 stdin JSON 快照；
token/成本增量解析本会话 wire.jsonl（state 文件缓存 offset，避免每帧全量扫，300ms 上限内零网络）；
effort 取 wire `llm.request.thinkingEffort`（实际生效档）；Out t/s（output ÷ 请求时长）计算与持久化保留、当前不展示；
终端映射写 tt-terminal-map.json（与 Codex 同文件同 schema），供 tt sidebar 点击跳转。
被 Kimi 以 1s 节流反复调用：任何解析失败都 fail-open 输出一行，绝不 traceback 到 stdout。
由 `tt setup` 生成，勿手改。"""
__version__ = "__KIMI_STATUSLINE_HOOK_VERSION__"
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

STATE_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-kimi-statusline.json")
TERMINAL_MAP_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-terminal-map.json")
QUOTA_CACHE_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-kimi-quota.json")
QUOTA_LOCK_FILE = QUOTA_CACHE_FILE + ".lock"     # 只取 mtime 当「上次派生刷新」标记，不做 flock
QUOTA_REFRESH_INTERVAL = 120                     # 后台刷新间隔（秒）：每 2 分钟最多一次 API 请求
QUOTA_DISPLAY_MAX_AGE = 900                      # 缓存超 15 分钟视为失效，Limit 段不显示
QUOTA_FETCH_TIMEOUT = 8                          # 与 CLI fetchManagedUsage 一致
DEFAULT_QUOTA_URL = "https://api.kimi.com/coding/v1/usages"
MAX_SESSIONS = 20
MAX_TERMINAL_MAPPINGS = 20

# 配色由 tt setup / update_hook / tt theme set 烘焙时注入（跟随当前主题，与 CC/Codex statusline 同源）。
# Kimi TUI 支持 24-bit truecolor，只注入 truecolor 一套（同 Codex 伪 statusline）。
C = __STATUSLINE_TRUECOLOR__
RST = C["reset"]
BOLD = "\033[1m"

if sys.platform == "win32":
    # Windows 控制台默认 GBK：项目名/分支名含非 GBK 字符时 print 会 UnicodeEncodeError，
    # 退出码非 0 → Kimi 回退内置布局。与 CC statusline 同款防护。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 定价由烘焙时从 analyzer.cost._fallback_pricing() 注入（kimi-k3 / kimi-k2.7-code / kimi-k2.6
# 三档 dict 原样 repr）。状态栏不联网、查不到价的模型按 $0 计（不 warn，避免污染状态栏）。
P = __KIMI_PRICING__


def _kimi_home():
    """Kimi Code 配置/数据根目录：KIMI_CODE_HOME 优先，否则 ~/.kimi-code（内联实现，零依赖）。"""
    env = os.environ.get("KIMI_CODE_HOME", "").strip()
    return env if env else os.path.expanduser("~/.kimi-code")


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _pricing_for(model):
    """wire 的模型 id 路由到注入的三档定价：kimi-code/k3 → kimi-k3；
    kimi-code/kimi-for-coding* → kimi-k2.7-code；其余 kimi* → kimi-k2.6；查不到 → None（按 $0 计）。"""
    m = (model or "").lower()
    if m in P:
        return P[m]
    if m.startswith("kimi-code/k3"):
        return P.get("kimi-k3")
    if m.startswith("kimi-code/kimi-for-coding"):
        return P.get("kimi-k2.7-code")
    if m.startswith("kimi"):
        return P.get("kimi-k2.6")
    return None


def _cost(model, i, o, cr, cc):
    """input*input + output*output + cache_read*cache_read + cache_creation*(cache_creation 或 input*1.25)。"""
    info = _pricing_for(model)
    if not info:
        return 0.0
    ic = info.get("input_cost_per_token", 0) or 0
    oc = info.get("output_cost_per_token", 0) or 0
    crc = info.get("cache_read_input_token_cost", 0) or 0
    ccc = info.get("cache_creation_input_token_cost") or ic * 1.25
    return i * ic + o * oc + cr * crc + cc * ccc


def _update_usage(session_id):
    """增量解析本会话 wire.jsonl，返回 (会话累计总 token, 累计成本 USD, effort, out_tps)。

    state：{sessionId: {"wire": path, "offset": int, "models": {model: {"i","o","cr","cc"}},
            "effort": str, "tps": float, "req_time": float}}，
    flock 串行合并 + 原子替换 + LRU 20（文件操作骨架同 codex statusline 的 _record_terminal_map）。
    state 里的 wire 路径失效时回退 glob <kimi_home>/sessions/*/<sessionId>/agents/main/wire.jsonl；
    offset > 文件大小说明 wire 被截断 → 从头重读、旧累计作废（否则重复计数）。
    effort / tps 只在消费到新字节时刷新（llm.request.thinkingEffort、output÷请求时长），其余帧回放 state。
    """
    if not session_id:
        return 0, 0.0, "", None
    tmp = None
    lock = None
    try:
        parent = os.path.dirname(STATE_FILE)
        os.makedirs(parent, exist_ok=True)
        lock = open(STATE_FILE + ".lock", "a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        entry = state.get(session_id)
        if not isinstance(entry, dict):
            entry = {}
        wire = entry.get("wire")
        if not (isinstance(wire, str) and os.path.exists(wire)):
            matches = glob.glob(os.path.join(
                _kimi_home(), "sessions", "*", session_id, "agents", "main", "wire.jsonl"))
            wire = matches[0] if matches else None
        models = entry.get("models")
        models = models if isinstance(models, dict) else {}
        offset = entry.get("offset")
        offset = offset if isinstance(offset, int) else 0
        # effort / tps 持久化在 state：渲染帧远多于新 usage.record，大部分帧直接回放上次值
        effort = entry.get("effort")
        effort = effort if isinstance(effort, str) else ""
        tps = entry.get("tps")
        tps = float(tps) if isinstance(tps, (int, float)) else None
        # llm.request 与其 usage.record 相隔整个生成时长（数秒~数十秒），1s 节流下必然落在不同
        # 消费帧——req_time 必须随 state 持久化，只在内存里保存会导致永远配对不上
        req_time = entry.get("req_time")
        req_time = float(req_time) if isinstance(req_time, (int, float)) else None
        if wire:
            try:
                if offset > os.path.getsize(wire):  # 文件被截断 → 重置
                    offset, models = 0, {}
                with open(wire, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                # 只消费完整行：Kimi 写 wire 与本脚本读取并发，chunk 末尾可能是写了一半的行。
                # 不完整部分留给下一帧重读——否则 offset 越过半截行，该条 usage.record 永久丢失
                #（解析失败被跳过、但字节已被 offset 吞掉）。
                last_nl = chunk.rfind(b"\n")
                complete = chunk[: last_nl + 1] if last_nl >= 0 else b""
                offset += len(complete)
                for line in complete.decode("utf-8", errors="replace").splitlines():
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    dtype = data.get("type")
                    if dtype == "llm.request":
                        rt = data.get("time")
                        req_time = float(rt) if isinstance(rt, (int, float)) else None
                        eff = data.get("thinkingEffort")  # 实际生效的思考档（跟随 /model 切换）
                        if isinstance(eff, str) and eff:
                            effort = eff
                        continue
                    if dtype != "usage.record":
                        continue
                    usage = data.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    model = data.get("model") or ""
                    bucket = models.get(model)
                    if not isinstance(bucket, dict):
                        bucket = {}
                        models[model] = bucket
                    for short, field in (("i", "inputOther"), ("o", "output"),
                                         ("cr", "inputCacheRead"), ("cc", "inputCacheCreation")):
                        val = usage.get(field)
                        if isinstance(val, (int, float)):
                            bucket[short] = bucket.get(short, 0) + int(val)
                    # Out TPS = 本请求 output ÷ 请求时长（llm.request→usage.record，含 prefill 的端到端有效值）。
                    # 与 CC 的 _compute_tps 同策略：算出会显示成 0 的不刷新、保持上次值
                    t = data.get("time")
                    out = usage.get("output")
                    if (req_time and isinstance(t, (int, float)) and t > req_time
                            and isinstance(out, (int, float)) and out > 0):
                        new_tps = out / ((t - req_time) / 1000)
                        if round(new_tps) > 0:
                            tps = new_tps
                    req_time = None
            except OSError:
                pass
        state.pop(session_id, None)
        state[session_id] = {"wire": wire, "offset": offset, "models": models,
                             "effort": effort, "tps": tps, "req_time": req_time}
        for key in list(state)[:-MAX_SESSIONS]:
            del state[key]
        # 无新增字节（offset/wire 都没动）→ 跳过写盘：Kimi 1s 节流反复调用，没必要每帧重写 state
        if offset != entry.get("offset") or wire != entry.get("wire"):
            fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_FILE)
            tmp = None
        total = 0
        cost = 0.0
        for model, bucket in models.items():
            if not isinstance(bucket, dict):
                continue
            i = int(bucket.get("i", 0))
            o = int(bucket.get("o", 0))
            cr = int(bucket.get("cr", 0))
            cc = int(bucket.get("cc", 0))
            total += i + o + cr + cc
            cost += _cost(model, i, o, cr, cc)
        return total, cost, effort, tps
    except OSError:
        return 0, 0.0, "", None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if lock:
            lock.close()


def _record_terminal_map(session_id):
    """记录当前 Kimi 会话所在窗格，供普通 `tt sidebar` 点击项目名跳转。

    与 Codex 共用一个文件一个 schema（sidebar 读取时对 agent 无差别合并）；多实例并发
    read-modify-write 用 flock 串行（Windows 无 iTerm/tmux，缺 fcntl 时仍保留原子替换兜底）。
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
        if term_map.get(session_id) == term:
            return  # 映射无变化 → 跳过写盘（1s 节流反复调用，没必要每帧重写）
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


def _pct_color(pct):
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


def _fmt_duration(s):
    """reset 倒计时紧凑格式（同 CC/Codex statusline）：4d21h / 3h55m / 45m。"""
    s = int(s)
    if s >= 86400:
        return f"{s // 86400}d{s % 86400 // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60}m"
    return f"{s // 60}m"


def _bar(pct, width=8):
    """进度条（仿 CC/Codex statusline）：█ 填充档位色 + ░ 空槽（>0 也染档位色），尾接 % 档位色。"""
    pct = max(0.0, min(100.0, float(pct)))
    filled = round(pct / 100 * width)
    empty = width - filled
    color = _pct_color(pct)
    empty_s = f"{color}{'░' * empty}{RST}" if pct > 0 and empty else "░" * empty
    return f"{color}{'█' * filled}{RST}{empty_s} {color}{pct:.0f}%{RST}"


def _quota_url():
    """/usages 端点（同 CLI /usage）：TT_KIMI_QUOTA_URL 覆盖 > config.toml managed provider
    base_url > 官方默认。"""
    env = os.environ.get("TT_KIMI_QUOTA_URL", "").strip()
    if env:
        return env
    try:
        import tomllib
        with open(os.path.join(_kimi_home(), "config.toml"), "rb") as f:
            cfg = tomllib.load(f)
        base = ((cfg.get("providers") or {}).get("managed:kimi-code") or {}).get("base_url", "")
        base = base.strip().rstrip("/")
        if base:
            return base + "/usages"
    except Exception:
        pass
    return DEFAULT_QUOTA_URL


def _read_quota_token():
    """读 OAuth access_token；过期/缺失返回 None（状态栏不做 refresh_token 流程，
    CLI 日常使用会自行刷新并写回凭证文件）。"""
    try:
        with open(os.path.join(_kimi_home(), "credentials", "kimi-code.json"), encoding="utf-8") as f:
            cred = json.load(f)
        token = cred.get("access_token")
        exp = cred.get("expires_at")
        if isinstance(token, str) and token and isinstance(exp, (int, float)) and exp > time.time():
            return token
    except Exception:
        pass
    return None


def _fetch_quota():
    """调 /usages 拿 5h/7d 用量写缓存（detached 子进程模式 --refresh-quota 调用）。
    任何失败都不写缓存——旧缓存自然超过 QUOTA_DISPLAY_MAX_AGE 后 Limit 段自动消失。"""
    token = _read_quota_token()
    if not token:
        return
    try:
        req = urllib.request.Request(_quota_url(), headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=QUOTA_FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return

    def _pct(detail):
        if not isinstance(detail, dict):
            return None
        try:
            limit = float(detail.get("limit") or 0)
            used = float(detail.get("used") or 0)
        except (TypeError, ValueError):
            return None
        return used / limit * 100 if limit > 0 else None

    def _reset_ts(detail):
        """detail.resetTime（ISO-8601 UTC，如 2026-08-23T13:32:55.338584Z）→ epoch 秒；解析失败 None。"""
        if not isinstance(detail, dict):
            return None
        rt = detail.get("resetTime")
        if not isinstance(rt, str) or not rt:
            return None
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(rt.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None

    five = five_reset = None
    for entry in data.get("limits") or []:
        win = (entry or {}).get("window") or {}
        if win.get("duration") == 300 and win.get("timeUnit") == "TIME_UNIT_MINUTE":
            detail = (entry or {}).get("detail")
            five = _pct(detail)
            five_reset = _reset_ts(detail)
            break
    usage = data.get("usage")
    cache = {"fetched_at": int(time.time()), "five_hour": five, "five_hour_reset": five_reset,
             "seven_day": _pct(usage), "seven_day_reset": _reset_ts(usage)}
    tmp = None
    try:
        parent = os.path.dirname(QUOTA_CACHE_FILE)
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, QUOTA_CACHE_FILE)
        tmp = None
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _maybe_refresh_quota():
    """缓存与上次派生都超 120s → detached 子进程后台拉 /usages（spawn ~10ms，不吃 300ms 预算）。
    lock 文件只做 mtime 标记：刷新在途（最长 8s 超时）或失败冷却期内不重复派生。"""
    now = time.time()

    def _age(path):
        try:
            return now - os.path.getmtime(path)
        except OSError:
            return float("inf")

    if _age(QUOTA_CACHE_FILE) < QUOTA_REFRESH_INTERVAL or _age(QUOTA_LOCK_FILE) < QUOTA_REFRESH_INTERVAL:
        return
    try:
        os.makedirs(os.path.dirname(QUOTA_LOCK_FILE), exist_ok=True)
        open(QUOTA_LOCK_FILE, "a").close()
    except OSError:
        pass
    try:
        # 跨平台 detach：POSIX 用 setsid，Windows 用 DETACHED_PROCESS + 新进程组
        #（start_new_session 在 Windows 上被忽略，且配合 cmd /c 父进程杀树时子进程要能存活）
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--refresh-quota"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
    except OSError:
        pass


def _render_quota():
    """读配额缓存渲染 ['5h:<bar> (3h55m)', '7d:<bar> (4d21h)']（仿 CC/Codex 进度条样式，无 Limit: 前缀）；
    缓存缺失/超 QUOTA_DISPLAY_MAX_AGE → 不显示。reset 字段为后加，旧缓存没有就不显示括号倒计时。"""
    try:
        if time.time() - os.path.getmtime(QUOTA_CACHE_FILE) > QUOTA_DISPLAY_MAX_AGE:
            return []
        with open(QUOTA_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return []
    segs = []
    now = time.time()
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        pct = cache.get(key) if isinstance(cache, dict) else None
        if not isinstance(pct, (int, float)):
            continue
        seg = f"{C['label']}{label}:{RST}{_bar(pct)}"
        reset = cache.get(key + "_reset")
        if isinstance(reset, (int, float)) and reset > now:
            seg += f" \033[2m{C['label']}({_fmt_duration(reset - now)}){RST}"
        segs.append(seg)
    return segs


def _git_stat(cwd):
    """相对 HEAD 的未提交增删行数 + 未跟踪文件数（同 CC statusline 的 git_diff_stat）。
    分支名用 payload 的 gitBranch，只补 numstat / ls-files 两个子进程；超时压进 300ms 预算，
    失败 / 非 git 仓库返回 (0, 0, 0)。"""
    added = deleted = 0
    try:
        out = subprocess.check_output(
            ["git", "diff", "HEAD", "--numstat"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=0.2)
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
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=0.15)
        untracked = sum(1 for ln in out.splitlines() if ln.strip())
    except Exception:
        pass
    return added, deleted, untracked


def _render_project(cwd, branch):
    """[项目](分支* +A -D ?U)（同 CC statusline L1）：分支名取 payload 的 gitBranch，
    增删行 / 未跟踪数由 _git_stat 补；非 git 仓库或统计全 0 时退化为纯分支名。"""
    if not cwd:
        return ""
    name = os.path.basename(cwd.rstrip("/")) or cwd
    if not branch:
        return f"{BOLD}{C['project']}[{name}]{RST}"
    added, deleted, untracked = _git_stat(cwd)
    inner = f"{C['branch']}{branch}{'*' if (added or deleted) else ''}{RST}"
    if added:
        inner += f" {C['added']}+{added}{RST}"
    if deleted:
        inner += f" {C['deleted']}-{deleted}{RST}"
    if untracked:
        inner += f" {C['untracked']}?{untracked}{RST}"
    return f"{BOLD}{C['project']}[{name}]{RST}({inner})"


def _render(payload):
    session_id = payload.get("sessionId")
    session_id = session_id if isinstance(session_id, str) else ""
    _record_terminal_map(session_id)
    total, cost, effort, tps = _update_usage(session_id)

    # 与 CC statusline 同风格同序（单行版）：[项目](分支) | Total | Cost | Out t/s | 5h/7d | Model/effort/权限模式
    # 5h/7d 走云端 /usages 的后台缓存（stdin 快照与 wire 都没有限额数据）。
    segments = []
    cwd = payload.get("cwd")
    branch = payload.get("gitBranch")
    proj = _render_project(cwd if isinstance(cwd, str) else "",
                           branch if isinstance(branch, str) else "")
    if proj:
        segments.append(proj)
    if total:
        segments.append(f"{C['total']}Total: {fmt_tokens(total)}{RST}")
        segments.append(f"{C['total']}Cost: ${cost:.2f}{RST}")
    if tps:
        pass  # Out t/s 先不展示（用户决定），tps 计算与 state 持久化逻辑保留，想恢复加一行 segments.append 即可
    _maybe_refresh_quota()
    segments.extend(_render_quota())
    model = payload.get("model")
    if isinstance(model, str) and model:
        if effort:
            model += f"/{effort}"  # wire llm.request 的 thinkingEffort（实际生效档）
        perm = payload.get("permissionMode")
        if isinstance(perm, str) and perm:
            model += f"/{perm}"  # 同 CC Model 段拼 effort/fast 的做法
        segments.append(f"{C['model']}Model: {model}{RST}")
    return " | ".join(segments)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh-quota":
        _fetch_quota()  # detached 后台刷新模式：只拉配额写缓存，不输出
        return
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        line = _render(payload)
    except Exception:
        line = ""
    print(line)  # Kimi 只取 stdout 第一行：单条 print，fail-open 也保证有一行
    sys.stdout.flush()


if __name__ == "__main__":
    main()
