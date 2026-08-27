"""adapter 间共享的小工具：JSONL 逐行解析、cwd → 项目名、agent 配置根目录。"""

import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path


def claude_home() -> str:
    """Claude Code 配置/数据根目录：`CLAUDE_CONFIG_DIR`（逗号分隔取第一个）优先，否则 `~/.claude`。
    官方支持该环境变量覆盖位置；Windows 下 `~` 经 expanduser 解析到 `%USERPROFILE%`。"""
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        return env.split(",")[0].strip()
    return os.path.expanduser("~/.claude")


def codex_home() -> str:
    """Codex 配置/数据根目录：`CODEX_HOME` 优先，否则 `~/.codex`（官方支持该环境变量覆盖）。"""
    env = os.environ.get("CODEX_HOME", "").strip()
    if env:
        return env
    return os.path.expanduser("~/.codex")


def kimi_home() -> str:
    """Kimi Code 配置/数据根目录：`KIMI_CODE_HOME` 优先，否则 `~/.kimi-code`（官方支持该环境变量覆盖）。"""
    env = os.environ.get("KIMI_CODE_HOME", "").strip()
    if env:
        return env
    return os.path.expanduser("~/.kimi-code")


def pi_home() -> str:
    """Pi 配置/数据根目录：`PI_CODING_AGENT_DIR` 优先，否则 `~/.pi/agent`（官方支持该环境变量覆盖，
    见 pi 文档 environment-variables.md）。"""
    env = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    if env:
        return env
    return os.path.expanduser("~/.pi/agent")


def iter_jsonl_dicts(path: Path | str) -> Iterator[dict]:
    """逐行读取 JSONL，只 yield dict 行。

    统一处理 strip/空行/JSONDecodeError/非 dict 行/文件打不开，
    让调用方只关心业务字段，不必各自复制这套骨架。
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    yield data
    except OSError:
        return


def file_may_have_events_since(path: Path, cutoff: datetime | None) -> bool:
    """文件是否可能包含 cutoff 之后的事件；无窗口时放行，文件消失时跳过。

    JSONL 只追加写，事件时间不会晚于文件 mtime，因此 mtime 早于 cutoff 的文件可在逐行解析前
    安全排除。mtime 新只代表“可能有”，仍由解析器按事件时间做最终过滤。
    """
    if cutoff is None:
        return True
    try:
        return path.stat().st_mtime >= cutoff.timestamp()
    except OSError:
        return False


def project_from_cwd(cwd: str) -> str:
    """项目名：优先取所属 git 仓库根的目录名（逐级向上找 .git，纯文件系统、不依赖 git 二进制）；
    非仓库 / 仓库根也删了 → 回退去 home 前缀后的最后一段。

    解决「在项目子目录里跑 agent 被识别成子目录名」（如 infohunter/official → official）：
    从 cwd 一路 dirname 向上，第一个含 .git 的目录就是项目根。.git 是仓库元数据目录/文件，
    判断它存在只读文件系统，与 git 是否安装无关；子目录已删也能向上命中仓库根。
    """
    home = os.path.expanduser("~")
    d = cwd
    while d and d not in (os.sep, home):
        if os.path.exists(os.path.join(d, ".git")):
            return os.path.basename(d)
        parent = os.path.dirname(d)
        if parent == d:  # 触顶，防死循环
            break
        d = parent
    # fallback：去 home 前缀后的最后一段
    rel = cwd[len(home):].strip(os.sep) if cwd.startswith(home) else cwd.strip(os.sep)
    parts = rel.split(os.sep)
    return parts[-1] if parts and parts[-1] else rel or "unknown"


def parse_epoch_ms(raw: object) -> datetime | None:
    """Kimi wire 事件时间是 epoch 毫秒（int）。"""
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(raw / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def kimi_project_from_session_dir(session_dir: Path) -> str:
    """Kimi 会话目录的 state.json → cwd/workDir → 项目名；读不到回退 wd_<name>_<hash> 目录名。

    布局：`<kimi_home>/sessions/<wd_*>/<session_*>/`，state.json 在会话目录下。
    """
    try:
        with open(session_dir / "state.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    work_dir = (data.get("cwd") or data.get("workDir")) if isinstance(data, dict) else None
    if isinstance(work_dir, str) and work_dir:
        return project_from_cwd(work_dir)
    match = re.fullmatch(r"wd_(.+)_[0-9a-f]{6,}", session_dir.parent.name)
    return match.group(1) if match else "unknown"
