#!/usr/bin/env python3
"""token-tracker Pi statusline（~/.pi/agent/extensions/tt-statusline.ts 调起）：渲染一行会话状态。
[项目](分支* +A -D ?U) | Total: <会话累计 token> | Cost: $<累计成本> | Model: <provider/model> | Ctx: <上下文占用%>
（字段、顺序、配色与 Kimi/CC statusline 同风格；Pi 是多 provider 工具、无统一订阅配额，故无 5h/7d Limit 段。
成本直接累计会话里 pi 按自己 models.json 定价算好的 usage.cost.total（无定价 provider 全 0），脚本不联网；
Ctx = 最后一条 assistant message 的 usage.totalTokens ÷ 上下文窗口——窗口优先取扩展 payload 的
contextWindow（pi 内部模型表，含 models-store），缺失/为 0 时回退读 ~/.pi/agent/models.json 的
providers.*.models[]（自定义 provider），再读不到就整段不显示，绝不硬编码窗口大小）
数据：sessionFile/cwd/model/contextWindow/width 取 argv[1] 的 JSON（pi.exec 不支持 stdin，见 core/exec.ts
stdio:["ignore",...]；无 argv 时回退读 stdin，便于手动调试）；token/成本增量解析会话 jsonl（state 文件
缓存 offset，避免每帧全量扫）；分支与未提交统计由 git 子进程补（pi 扩展拿不到分支名）；
终端映射写 tt-terminal-map.json（与 Codex/Kimi 同文件同 schema），供 tt sidebar 点击跳转。
width（终端列数）传入时做宽度降级：过窄依次丢 Ctx → 分支统计 → Cost 段。
被扩展在每 turn/session 事件后调起：任何解析失败都 fail-open 输出一行，绝不 traceback 到 stdout。
由 `tt setup` 生成，勿手改。"""
__version__ = "__PI_STATUSLINE_HOOK_VERSION__"
import json
import os
import subprocess
import sys
import tempfile

STATE_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-pi-statusline.json")
TERMINAL_MAP_FILE = os.path.join(os.path.expanduser("~/.config/token-tracker"), "tt-terminal-map.json")
MAX_SESSIONS = 20
MAX_TERMINAL_MAPPINGS = 20

# 配色由 tt setup / update_hook / tt theme set 烘焙时注入（跟随当前主题，与 CC/Codex/Kimi statusline 同源）。
# Pi TUI 支持 24-bit truecolor，只注入 truecolor 一套（同 Kimi statusline）。
C = __STATUSLINE_TRUECOLOR__
RST = C["reset"]
BOLD = "\033[1m"

if sys.platform == "win32":
    # Windows 控制台默认 GBK：项目名/分支名含非 GBK 字符时 print 会 UnicodeEncodeError。
    # 与 CC/Kimi statusline 同款防护。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _pi_home():
    """Pi 配置/数据根目录：PI_CODING_AGENT_DIR 优先，否则 ~/.pi/agent（内联实现，零依赖）。"""
    env = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return env if env else os.path.expanduser("~/.pi/agent")


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _context_window_from_models_json(model):
    """从 ~/.pi/agent/models.json 的 providers.*.models[] 查 model（provider/modelId 或裸 modelId）
    的 contextWindow；读不到返回 None（调用方整段不显示 Ctx，不硬编码窗口大小）。"""
    if not model:
        return None
    provider, _, model_id = model.partition("/")
    try:
        with open(os.path.join(_pi_home(), "models.json"), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, dict):
        return None
    for pname, pdata in providers.items():
        if provider and model_id and pname != provider:
            continue
        models = pdata.get("models") if isinstance(pdata, dict) else None
        if not isinstance(models, list):
            continue
        for m in models:
            if isinstance(m, dict) and m.get("id") == (model_id or model):
                window = m.get("contextWindow")
                return window if isinstance(window, (int, float)) and window > 0 else None
    return None


def _update_usage(session_file):
    """增量解析会话 jsonl，返回 (会话累计总 token, 累计成本 USD, 末条 assistant 的 totalTokens)。

    state：{sessionFile: {"offset": int, "models": {model: {"i","o","cr","cc","cost"}}, "ctx": int}}，
    flock 串行合并 + 原子替换 + LRU 20（文件操作骨架同 Kimi statusline 的 _update_usage）。
    只消费完整行（jsonl 只追加写，末尾可能是写了一半的行，留给下一帧）；offset > 文件大小说明
    文件被截断 → 从头重读、旧累计作废（否则重复计数）。ctx 是最后一条 assistant message 的
    usage.totalTokens（上下文占用，compaction 后会回落），每次消费到 assistant 行就刷新。
    """
    if not session_file or not os.path.exists(session_file):
        return 0, 0.0, 0
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
        entry = state.get(session_file)
        if not isinstance(entry, dict):
            entry = {}
        models = entry.get("models")
        models = models if isinstance(models, dict) else {}
        offset = entry.get("offset")
        offset = offset if isinstance(offset, int) else 0
        ctx_tokens = entry.get("ctx")
        ctx_tokens = int(ctx_tokens) if isinstance(ctx_tokens, (int, float)) else 0
        try:
            if offset > os.path.getsize(session_file):  # 文件被截断 → 重置
                offset, models, ctx_tokens = 0, {}, 0
            with open(session_file, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            last_nl = chunk.rfind(b"\n")
            complete = chunk[: last_nl + 1] if last_nl >= 0 else b""
            offset += len(complete)
            for line in complete.decode("utf-8", errors="replace").splitlines():
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict) or data.get("type") != "message":
                    continue
                message = data.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                model = message.get("model") or ""
                provider = message.get("provider") or ""
                if isinstance(provider, str) and provider and isinstance(model, str) and model:
                    model = f"{provider}/{model}"
                bucket = models.get(model)
                if not isinstance(bucket, dict):
                    bucket = {}
                    models[model] = bucket
                for short, field in (("i", "input"), ("o", "output"),
                                     ("cr", "cacheRead"), ("cc", "cacheWrite")):
                    val = usage.get(field)
                    if isinstance(val, (int, float)):
                        bucket[short] = bucket.get(short, 0) + int(val)
                cost = usage.get("cost")
                total_cost = cost.get("total") if isinstance(cost, dict) else None
                if isinstance(total_cost, (int, float)):
                    bucket["cost"] = bucket.get("cost", 0.0) + float(total_cost)
                total = usage.get("totalTokens")
                if isinstance(total, (int, float)) and total > 0:
                    ctx_tokens = int(total)
        except OSError:
            pass
        state.pop(session_file, None)
        state[session_file] = {"offset": offset, "models": models, "ctx": ctx_tokens}
        for key in list(state)[:-MAX_SESSIONS]:
            del state[key]
        # 无新增字节 → 跳过写盘：turn 级调用，没必要每帧重写 state
        if offset != entry.get("offset"):
            fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_FILE)
            tmp = None
        total = 0
        cost = 0.0
        for bucket in models.values():
            if not isinstance(bucket, dict):
                continue
            total += (int(bucket.get("i", 0)) + int(bucket.get("o", 0))
                      + int(bucket.get("cr", 0)) + int(bucket.get("cc", 0)))
            c = bucket.get("cost", 0.0)
            cost += c if isinstance(c, (int, float)) else 0.0
        return total, cost, ctx_tokens
    except OSError:
        return 0, 0.0, 0
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if lock:
            lock.close()


def _record_terminal_map(session_file):
    """记录当前 Pi 会话所在窗格，供普通 `tt sidebar` 点击跳转（key 用会话文件路径——
    与 adapter/sidebar 的 session_id 不同源，仅作终端定位键，自洽即可）。
    与 Codex/Kimi 共用一个文件一个 schema；多实例并发 read-modify-write 用 flock 串行。
    """
    term = {}
    if os.environ.get("ITERM_SESSION_ID"):
        term["iterm"] = os.environ["ITERM_SESSION_ID"]
    if os.environ.get("TMUX_PANE"):
        term["tmux"] = os.environ["TMUX_PANE"]
    if not session_file or not term:
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
        if term_map.get(session_file) == term:
            return  # 映射无变化 → 跳过写盘
        term_map.pop(session_file, None)
        term_map[session_file] = term
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


def _git_info(cwd):
    """当前分支 + 相对 HEAD 的未提交增删行数 + 未跟踪文件数（同 Kimi statusline 的 _git_stat，
    分支名 pi 扩展给不了、自己取）。超时压进几百 ms 预算，失败 / 非 git 仓库返回 ("", 0, 0, 0)。"""
    branch = ""
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=cwd,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", timeout=0.15).strip()
    except Exception:
        pass
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
    return branch, added, deleted, untracked


def _render_project(cwd, with_stat=True):
    """[项目](分支* +A -D ?U)（同 Kimi/CC statusline L1）；非 git 仓库退化为纯项目名。
    with_stat=False（宽度降级）时只保留分支名。"""
    if not cwd:
        return ""
    name = os.path.basename(cwd.rstrip("/")) or cwd
    branch, added, deleted, untracked = _git_info(cwd)
    if not branch:
        return f"{BOLD}{C['project']}[{name}]{RST}"
    inner = f"{C['branch']}{branch}{'*' if (added or deleted) else ''}{RST}"
    if with_stat:
        if added:
            inner += f" {C['added']}+{added}{RST}"
        if deleted:
            inner += f" {C['deleted']}-{deleted}{RST}"
        if untracked:
            inner += f" {C['untracked']}?{untracked}{RST}"
    return f"{BOLD}{C['project']}[{name}]{RST}({inner})"


def _pct_color(pct):
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


def _render(payload):
    session_file = payload.get("sessionFile")
    session_file = session_file if isinstance(session_file, str) else ""
    _record_terminal_map(session_file)
    total, cost, ctx_tokens = _update_usage(session_file)

    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) else ""
    model = payload.get("model")
    model = model if isinstance(model, str) else ""
    width = payload.get("width")
    width = int(width) if isinstance(width, (int, float)) and width > 0 else 0

    window = payload.get("contextWindow")
    window = int(window) if isinstance(window, (int, float)) and window > 0 else 0
    if not window:
        window = _context_window_from_models_json(model) or 0

    # 与 Kimi/CC statusline 同风格同序（单行版）：[项目](分支) | Total | Cost | Model | Ctx（无 Limit 段）
    segs_full = []
    proj = _render_project(cwd)
    if proj:
        segs_full.append(proj)
    if total:
        segs_full.append(f"{C['total']}Total: {fmt_tokens(total)}{RST}")
        segs_full.append(f"{C['total']}Cost: ${cost:.2f}{RST}")
    if model:
        segs_full.append(f"{C['model']}Model: {model}{RST}")
    if ctx_tokens and window:
        pct = ctx_tokens / window * 100
        segs_full.append(f"{C['label']}Ctx:{RST} {_pct_color(pct)}{pct:.0f}%{RST}")

    if not width:
        return " | ".join(segs_full)
    # 宽度降级：依次丢 Ctx → 分支统计（重渲染项目段）→ Cost，直到放下或只剩项目名
    line = " | ".join(segs_full)
    if _visible_len(line) <= width:
        return line
    segs = [s for s in segs_full if not s.startswith(f"{C['label']}Ctx:")]
    line = " | ".join(segs)
    if _visible_len(line) <= width:
        return line
    if proj:
        segs[0] = _render_project(cwd, with_stat=False)
    line = " | ".join(segs)
    if _visible_len(line) <= width:
        return line
    segs = [s for s in segs if not s.startswith(f"{C['total']}Cost:")]
    return " | ".join(segs)


def _visible_len(s):
    """去 ANSI 转义后的显示长度（宽度降级判断用；不计较全角宽字符，近似即可）。"""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def main():
    payload = {}
    try:
        raw = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        line = _render(payload)
    except Exception:
        line = ""
    print(line)  # 扩展取 stdout 第一行：单条 print，fail-open 也保证有一行
    sys.stdout.flush()


if __name__ == "__main__":
    main()
