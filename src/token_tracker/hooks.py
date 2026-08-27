import json
import os
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from importlib import resources

from . import config, sidebar_install
from .adapters.util import claude_home, codex_home, kimi_home
from .analyzer import cost as _cost_mod
from .i18n import t
from .ui import themes
from .ui.console import get_console

_CLAUDE = claude_home()  # CLAUDE_CONFIG_DIR 覆盖 / ~/.claude
_CODEX = codex_home()    # CODEX_HOME 覆盖 / ~/.codex
_KIMI = kimi_home()      # KIMI_CODE_HOME 覆盖 / ~/.kimi-code


@dataclass
class SetupComponents:
    """组件开关。CC statusLine 接管、Codex 伪 statusline（Stop hook）与 Kimi statusline
    （tui.toml [status_line].command）均为可选组件，意图持久化到 config.json。"""
    cc_statusline: bool = True
    codex_faux_statusline: bool = True
    kimi_statusline: bool = True

    @classmethod
    def all_on(cls) -> "SetupComponents":
        return cls(cc_statusline=True, codex_faux_statusline=True, kimi_statusline=True)

# tt 自己的产物（statusline 脚本 + 缓存 + 备份）集中放 ~/.config/token-tracker（XDG，跟 theme/lang 同处）；
# settings.json / config.toml 是「改 agent 自己的配置」、必须留 agent 目录。statusLine/hook 的 command
# 是绝对路径，脚本放 agent 目录外照样跑（实测 + ccstatusline 等业界用 npx 全局脚本同理）。
_TT = config.CONFIG_DIR  # ~/.config/token-tracker

CLAUDE_SETTINGS = os.path.join(_CLAUDE, "settings.json")  # 改 Claude Code 配置，留 agent 目录
HOOK_SCRIPT_PATH = os.path.join(_TT, "claude-statusline.py")
CODEX_DIR = _CODEX
CODEX_CONFIG = os.path.join(CODEX_DIR, "config.toml")     # 仅迁移旧内联 Stop；新 Hook 统一写 hooks.json
CODEX_STATUSLINE_HOOK_PATH = os.path.join(_TT, "codex-statusline.py")
KIMI_STATUSLINE_HOOK_PATH = os.path.join(_TT, "kimi-statusline.py")
# Kimi statusline 的 wire offset / 累计缓存（脚本写；与 CC 心跳、终端映射分文件，避免并发互相覆盖）
KIMI_STATUSLINE_STATE_PATH = os.path.join(_TT, "tt-kimi-statusline.json")
# Kimi statusline 的 5h/7d 限额缓存（模板的 detached --refresh-quota 子进程写）
KIMI_STATUSLINE_QUOTA_PATH = os.path.join(_TT, "tt-kimi-quota.json")
STATUS_FILE = config.STATUS_FILE                          # CC statusline 缓存（单一权威定义在 config）
TERMINAL_MAP_FILE = config.TERMINAL_MAP_FILE              # Codex Stop hook 采集的终端定位映射
HOOK_VERSION = "2.1"  # 2.0: 采集 _terminal_map（sidebar 点击跳转）；2.1: 共享状态无条件随帧携带、防异常帧清表
STATUSLINE_HOOK_VERSION = "1.6"  # 1.4: 第三方 provider 显示会话 Cost；1.5: 无配额不挂 Limit: 前缀；1.6: Total 不再重复计 reasoning（实测 total_tokens==in+out）
KIMI_STATUSLINE_HOOK_VERSION = "1.2"  # 1.2: Model 段加实际 effort（wire thinkingEffort），新增 Out t/s（output÷请求时长）

CC_BACKUP_PATH = os.path.join(_TT, "cc-backup.json")
CODEX_BACKUP_LEGACY = os.path.join(_TT, "codex-backup.json")  # 老用户残留，unsetup 时还能恢复

# 旧位置（agent 根目录）文件，迁移时删——老用户从 ~/.claude/~/.codex 迁到 ~/.config/token-tracker
_LEGACY_PATHS = [
    os.path.join(_CLAUDE, "tt-statusline.py"), os.path.join(_CLAUDE, "tt-status.json"),
    os.path.join(_CODEX, "tt-statusline.py"), os.path.join(_CODEX, "tt-backup.json"),
]

# 状态栏脚本模板在 templates/ 包数据（claude_statusline.py / codex_statusline.py）——
# 独立成文件让 ruff / mypy / 人都能直接读查（600 行脚本藏在 r-string 里 lint 完全失明）。
# 占位符（__HOOK_VERSION__ / __STATUSLINE_TRUECOLOR__ 等）在 _render_* 烘焙时注入；
# HOOK_VERSION / STATUSLINE_HOOK_VERSION 是唯一版本来源。


def _load_template(name: str) -> str:
    return (resources.files("token_tracker.templates") / name).read_text(encoding="utf-8")


# --- helpers ---

def _render_hook_script() -> str:
    """把 HOOK_VERSION + 当前主题 truecolor / 256 两套配色注入占位符，得到要落盘的状态栏脚本。"""
    name = config.resolve_theme()
    return (
        _load_template("claude_statusline.py")
        .replace("__HOOK_VERSION__", HOOK_VERSION)
        .replace("__STATUSLINE_TRUECOLOR__", repr(themes.theme_to_statusline_ansi(name)))
        .replace("__STATUSLINE_COLOR256__", repr(themes.theme_to_statusline_ansi(name, "256")))
    )


def _render_codex_statusline_hook() -> str:
    """注入版本号 + 当前主题 statusline 配色（truecolor），得到要落盘的 Codex 伪 statusline 脚本。
    跟随主题：tt theme set 经 update_hook 重烘焙；不需 __TT_PYTHON__（脚本无 subprocess 调 tt）。"""
    name = config.resolve_theme()
    return (
        _load_template("codex_statusline.py")
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


# --- Kimi Code statusline（真 statusline：tui.toml [status_line].command 调脚本，取 stdout 首行） ---

# 烘焙进脚本的 Kimi 定价 key（与 cost.py _fallback_pricing 的三档一一对应；
# 新增 Kimi 定价档时这里与模板路由要同步补）。
_KIMI_PRICING_KEYS = ("kimi-k3", "kimi-k2.7-code", "kimi-k2.6")


def _render_kimi_statusline_hook() -> str:
    """注入版本号 + 当前主题 statusline 配色（truecolor）+ Kimi 三档内置定价，得到落盘脚本。
    定价烘焙 _fallback_pricing 原样 dict（状态栏脚本零依赖、不联网；CLI 报表的在线价不适用这里）。"""
    name = config.resolve_theme()
    fallback = _cost_mod._fallback_pricing()
    pricing = {key: fallback[key] for key in _KIMI_PRICING_KEYS}
    return (
        _load_template("kimi_statusline.py")
        .replace("__KIMI_STATUSLINE_HOOK_VERSION__", KIMI_STATUSLINE_HOOK_VERSION)
        .replace("__STATUSLINE_TRUECOLOR__", repr(themes.theme_to_statusline_ansi(name)))
        .replace("__KIMI_PRICING__", repr(pricing))
    )


def _write_kimi_statusline_script() -> None:
    os.makedirs(_TT, exist_ok=True)
    with open(KIMI_STATUSLINE_HOOK_PATH, "w", encoding="utf-8") as f:
        f.write(_render_kimi_statusline_hook())
    if os.name != "nt":
        os.chmod(KIMI_STATUSLINE_HOOK_PATH,
                 os.stat(KIMI_STATUSLINE_HOOK_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _installed_kimi_statusline_version() -> str | None:
    try:
        with open(KIMI_STATUSLINE_HOOK_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


def _kimi_statusline_command(python: str | None = None) -> str:
    return _build_cc_command(python or sys.executable or "python3", KIMI_STATUSLINE_HOOK_PATH)


def kimi_statusline_active() -> bool:
    """双因素：用户意图 AND 实际装好（脚本存在 + tui.toml 的 command 指向 tt 脚本）。"""
    if config.kimi_statusline_intent() is not True:
        return False
    if not os.path.exists(KIMI_STATUSLINE_HOOK_PATH):
        return False
    return sidebar_install.kimi_statusline_hook_present()


# 迁移 / 卸载时定位 tt 旧版追加的整段 [[hooks.Stop]]——
# 同时认新（codex-statusline）/ 旧（tt-statusline）两种特征码。
# command 值兼容三代形态：双引号 basic string（最老）、单引号 literal 裸拼接（0.4.x）、
# 单引号 literal 内含双引号包裹（现行，防路径空格断词，与 CC 侧 #13/#14 同一治法）
_CODEX_STATUSLINE_REGEX = re.compile(
    r'\n*\[\[hooks\.Stop\]\]\s*'
    r'\[\[hooks\.Stop\.hooks\]\]\s*'
    r'type = "command"\s*'
    r'command = ("[^"\n]*(?:codex-statusline|tt-statusline)[^"\n]*"'
    r"|'[^'\n]*(?:codex-statusline|tt-statusline)[^'\n]*')\s*"
    r'timeout = \d+\s*'
)


def _has_tt_codex_statusline(content: str) -> bool:
    return _CODEX_STATUSLINE_REGEX.search(content) is not None


def _migrate_codex_statusline_config(content: str) -> str:
    """只移除 Token Tracker 的旧内联 Stop 段；[hooks.state] 与用户其它 TOML 原样保留。"""
    return _CODEX_STATUSLINE_REGEX.sub("\n", content)


def _uninstall_codex_statusline(content: str) -> str:
    """删 Codex statusline 运行产物 + 旧内联 Stop 段（hooks.json 由统一 installer 管理）。"""
    if os.path.exists(CODEX_STATUSLINE_HOOK_PATH):
        os.remove(CODEX_STATUSLINE_HOOK_PATH)
    if os.path.exists(TERMINAL_MAP_FILE):
        os.remove(TERMINAL_MAP_FILE)
    return _migrate_codex_statusline_config(content)


def _read_codex_config() -> tuple[str, dict] | None:
    try:
        with open(CODEX_CONFIG, encoding="utf-8") as f:
            content = f.read()
        return content, tomllib.loads(content)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _codex_statusline_command(python: str | None = None) -> str:
    executable = python or sys.executable or "python3"
    if os.name != "nt":
        home = os.path.realpath(os.path.expanduser("~"))
        paths = (os.path.abspath(executable), os.path.abspath(CODEX_STATUSLINE_HOOK_PATH))
        if all(os.path.commonpath((home, path)) == home for path in paths):
            return " ".join(sidebar_install._portable_shell_path(path) for path in paths)
    return _build_cc_command(executable, CODEX_STATUSLINE_HOOK_PATH)


def _inline_codex_statusline_present() -> bool:
    result = _read_codex_config()
    return bool(result and _has_tt_codex_statusline(result[0]))


def codex_statusline_active() -> bool:
    """双因素：用户意图 AND 实际装好。

    迁移期兼容旧 config.toml 内联 Stop，让 needs_update() 能无打扰搬到 hooks.json；
    新安装只认 hooks.json。
    """
    if config.codex_faux_statusline_intent() is not True:
        return False
    if not os.path.exists(CODEX_STATUSLINE_HOOK_PATH):
        return False
    return sidebar_install.statusline_hook_present() or _inline_codex_statusline_present()


def _settings_has_tt_statusline() -> bool:
    """settings.json 的 statusLine 是否指向 tt 脚本（读失败 / 损坏 → False）。"""
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        sl = settings.get("statusLine")
        return isinstance(sl, dict) and _is_tt_cc_command(sl.get("command") or "")
    except (OSError, json.JSONDecodeError):
        return False


def cc_statusline_active() -> bool:
    """双因素：用户意图（config）AND 实际装好（脚本文件 + settings.json 的 statusLine 指我们脚本）。"""
    if config.cc_statusline_intent() is not True:
        return False
    if not os.path.exists(HOOK_SCRIPT_PATH):
        return False
    return _settings_has_tt_statusline()


def recommended_components() -> SetupComponents:
    """setup(components=None) 与 wizard 问题默认值的唯一权威来源。
    CC 端探测优先（do-no-harm）：settings.json 里有非 tt 的自定义 statusLine（或 JSON 损坏）→ False，
    绝不静默替换用户自定义；否则已记录意图非 None → 用意图；否则 → True（全新 / 已是 tt 的 → 接管）。
    Codex 端无从探测「用户自己的 statusline」：已记录意图非 None → 用意图，否则 → True。
    Kimi 端探测 tui.toml（do-no-harm，同 CC 哲学）：用户自定义 status_line.command → False，
    绝不静默替换；否则已记录意图非 None → 用意图；否则 → True。"""
    cc = True
    if os.path.exists(CLAUDE_SETTINGS):
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            settings = None  # 损坏 → 不可安全触碰
        if not isinstance(settings, dict):
            cc = False
        else:
            sl = settings.get("statusLine")
            cmd = sl.get("command") if isinstance(sl, dict) else None
            if cmd and not (isinstance(cmd, str) and _is_tt_cc_command(cmd)):
                cc = False  # 非 tt 的自定义 statusLine（含非法类型）→ 不接管
    if cc:
        cc_intent = config.cc_statusline_intent()
        cc = cc_intent if cc_intent is not None else True
    codex_intent = config.codex_faux_statusline_intent()
    codex = codex_intent if codex_intent is not None else True
    if sidebar_install.kimi_statusline_user_custom():
        kimi = False  # 用户自己的 status_line.command → 不接管
    else:
        kimi_intent = config.kimi_statusline_intent()
        kimi = kimi_intent if kimi_intent is not None else True
    return SetupComponents(cc_statusline=cc, codex_faux_statusline=codex, kimi_statusline=kimi)


def is_setup() -> bool:
    """已配置 = 每个已装 agent 的组件意图都明确、且意图为 True 的组件实装好（双因素）。
    意图 False 则用户明确不要、不强求文件存在（自定义 statusLine 用户跑报表不再被抢占）。
    CC 端例外：意图缺失（None）但 statusLine 已是 tt 的 → 按存量用户推断为已配（不打扰）。"""
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)
    has_kimi = os.path.isdir(_KIMI)
    if not has_cc and not has_codex and not has_kimi:
        return False
    if has_cc:
        intent = config.cc_statusline_intent()
        if intent is None:
            # 迁移推断（不 bump SETUP_VERSION 的配套）：存量用户没表达过意图，statusLine 已是 tt 的
            # 视为已配、不打扰；其余（全新 / 自定义 / 损坏）视为未配，走一次 setup（推荐默认绝不抢占）。
            if not _settings_has_tt_statusline():
                return False
        elif intent and not cc_statusline_active():
            return False
    if has_codex:
        intent = config.codex_faux_statusline_intent()
        if intent is None:  # 没跑过 wizard、没表达意图 → 视为未配
            return False
        # intent True 时双因素都要满足；intent False 时用户明确不要、不强求文件
        if intent and not codex_statusline_active():
            return False
    if has_kimi:
        intent = config.kimi_statusline_intent()
        if intent is None:  # 没跑过 wizard、没表达意图 → 视为未配（老用户升级后重走一次 setup）
            return False
        if intent and not kimi_statusline_active():
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


def _build_cc_command(python: str, script: str) -> str:
    """拼 statusLine command 字符串。
    Windows: 反斜杠转正斜杠（CC 在 Windows 走 Git Bash/sh 执行 command，反斜杠被吞致 exit 127）；
    所有平台: 两段路径都加双引号包裹（防路径含空格断词）。
    issue #13 / #14 根治：旧格式 `f"{python} {script}"` 在 Windows 静默失败、状态栏空白。"""
    if os.name == "nt":
        python = python.replace("\\", "/")
        script = script.replace("\\", "/")
    return f'"{python}" "{script}"'


def _cc_command_outdated(cmd: str) -> bool:
    """settings.json 里 tt 的 statusLine.command 是否还是旧格式（裸拼接 / 含反斜杠）。
    新格式：两段路径都用 `"` 包裹 + Windows 上路径必须正斜杠。
    仅对 tt 的 command 生效（_is_tt_cc_command 已先过滤），用户原 command 不动。"""
    if not cmd:
        return False
    if not cmd.startswith('"'):
        return True  # 没引号 = 旧裸拼接
    if os.name == "nt" and "\\" in cmd:
        return True  # Windows 上还含反斜杠 = 没转过来
    return False


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


def _cc_command_needs_sync() -> bool:
    """检测 settings.json 里 tt 的 statusLine.command 是否需要重写为新格式（issue #13/#14）。
    用户原 command（非 tt）一律不动。"""
    if not os.path.exists(CLAUDE_SETTINGS):
        return False
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    cmd = (settings.get("statusLine") or {}).get("command") or ""
    if not _is_tt_cc_command(cmd):
        return False
    return _cc_command_outdated(cmd)


def _sync_cc_command() -> None:
    """重写 settings.json 里 tt 的 statusLine.command 字段（保留其它字段不动）。"""
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if not _is_tt_cc_command((settings.get("statusLine") or {}).get("command") or ""):
        return
    python = sys.executable or "python3"
    settings["statusLine"] = {"type": "command", "command": _build_cc_command(python, HOOK_SCRIPT_PATH)}
    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _sync_codex_managed_hooks(quiet: bool = False) -> bool:
    """把 Stop + UserPromptSubmit 统一同步到 hooks.json，再迁移旧内联 Stop。

    先写 hooks.json、成功后才移除 config.toml 旧段，避免迁移中断导致伪 statusline 失效。
    """
    p = (lambda *a, **k: None) if quiet else get_console().print
    statusline_command = (
        _codex_statusline_command()
        if config.codex_faux_statusline_intent() is True
        else None
    )
    try:
        changed = sidebar_install.install_managed_hooks(statusline_command)
    except ValueError:
        get_console().print(
            f"[red]{t('codex_hooks_corrupt', path=sidebar_install.CODEX_HOOKS)}[/red]"
        )
        return False

    result = _read_codex_config()
    if result:
        content, _parsed = result
        migrated = _migrate_codex_statusline_config(content)
        if migrated != content:
            with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
                f.write(migrated)
            changed = True
    if changed:
        p(f"[green]✓[/green] {t('codex_hooks_synced')}")
        p(f"[dim]{t('sidebar_hook_trust')}[/dim]")
    return True


def needs_update() -> bool:
    # 只在已安装（新位置脚本文件存在）时纳入版本判断，未装不主动装
    if os.path.exists(HOOK_SCRIPT_PATH) and _installed_hook_version() != HOOK_VERSION:
        return True
    sv = _installed_codex_statusline_version()
    if sv is not None and sv != STATUSLINE_HOOK_VERSION:
        return True
    # setup_version 3 起，用户级 $tt-sidebar Skill 与 UserPromptSubmit hook 也属于 setup 产物。
    # 老用户（setup_version < 3）由 cli 的升级引导统一安装，不在这里抢跑。
    if os.path.isdir(CODEX_DIR) and config.setup_version() >= 3:
        statusline_command = (
            _codex_statusline_command()
            if config.codex_faux_statusline_intent() is True
            else None
        )
        if (
            sidebar_install.skill_needs_sync()
            or sidebar_install.managed_hooks_need_sync(statusline_command)
            or _inline_codex_statusline_present()
        ):
            return True
    # setup_version 4 起，Kimi 的 tt-sidebar Skill 与 UserPromptSubmit hook 也属于 setup 产物
    if os.path.isdir(_KIMI) and config.setup_version() >= 4:
        if sidebar_install.kimi_skill_needs_sync() or sidebar_install.kimi_hooks_need_sync():
            return True
    # setup_version 5 起，Kimi statusline（tui.toml [status_line].command）也属于 setup 产物。
    # 双因素哲学：意图为 True 才要求实装（None/False 不强求——None 由 is_setup 引导走 setup，
    # False 是用户明确不要）；版本落后或 tui command 漂移都由 update_hook 收敛。
    if (
        os.path.isdir(_KIMI)
        and config.setup_version() >= 5
        and config.kimi_statusline_intent() is True
    ):
        if _installed_kimi_statusline_version() != KIMI_STATUSLINE_HOOK_VERSION:
            return True
        if sidebar_install.kimi_statusline_needs_sync(_kimi_statusline_command()):
            return True
    return _cc_command_needs_sync()  # settings.json 里 command 格式过时也算待更新（issue #13/#14）


def update_hook() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):  # 已装才同步（未装不主动装）
        _write_cc_statusline_script()
    if _installed_codex_statusline_version() is not None:
        _write_codex_statusline_script()
    if _cc_command_needs_sync():
        _sync_cc_command()
    if os.path.isdir(CODEX_DIR) and config.setup_version() >= 3:
        _sync_codex_managed_hooks(quiet=True)
        _setup_codex_sidebar(quiet=True)
    if os.path.isdir(_KIMI) and config.setup_version() >= 4:
        _setup_kimi_sidebar(quiet=True)
    # 意图为 True 才收敛 Kimi statusline（重烘焙脚本 + 同步 tui command）；
    # 损坏 tui.toml 自动更新绝不覆盖，只在显式 setup 时提示（同 hooks.json 哲学）。
    if (
        os.path.isdir(_KIMI)
        and config.setup_version() >= 5
        and config.kimi_statusline_intent() is True
    ):
        _write_kimi_statusline_script()
        try:
            sidebar_install.install_kimi_statusline(_kimi_statusline_command())
        except ValueError:
            pass


# --- setup ---

def setup(auto: bool = False, components: SetupComponents | None = None, quiet: bool = False) -> None:
    """安装状态栏 + 可选组件。components=None 表示推荐默认（recommended_components：
    已有意图优先、CC 端探测 settings.json、绝不静默替换用户自定义 statusLine）。
    quiet=True 时不打任何提示（wizard 场景：由 wizard 末尾给一次综合总结）。"""
    if components is None:
        components = recommended_components()
    p = (lambda *a, **k: None) if quiet else get_console().print

    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)
    has_kimi = os.path.isdir(_KIMI)

    if not has_cc and not has_codex and not has_kimi:
        p(f"[red]{t('no_agent_install')}[/red]")
        return

    if auto:
        p(f"[dim]{t('first_setup')}[/dim]")

    os.makedirs(_TT, exist_ok=True)  # tt 自己的目录
    _migrate_legacy()                # 删旧位置（agent 根目录）残留，迁到 ~/.config/token-tracker

    if has_cc:
        _setup_claude(components, quiet)
    else:
        if not auto:
            p(f"[dim]{t('cc_not_found')}[/dim]")

    if has_codex:
        _setup_codex(components, quiet)
        _setup_codex_sidebar(quiet)
    else:
        if not auto:
            p(f"[dim]{t('codex_not_found')}[/dim]")

    if has_kimi:
        _setup_kimi_statusline(components, quiet)
        _setup_kimi_sidebar(quiet)
    else:
        if not auto:
            p(f"[dim]{t('kimi_not_found')}[/dim]")

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


def _restore_cc_statusline(settings: dict, p) -> None:
    """statusLine 是 tt 的才动：从 cc-backup.json 还原（或直接移除）+ 清 tokenTracker 残留 + 删缓存。
    opt-out（_optout_claude）与卸载（_unsetup_claude）共用；打印走传入的 p（quiet 感知）。"""
    sl = settings.get("statusLine")
    if not (isinstance(sl, dict) and _is_tt_cc_command(sl.get("command") or "")):
        return
    previous = None
    if os.path.exists(CC_BACKUP_PATH):  # 新位置（独立文件）
        try:
            with open(CC_BACKUP_PATH, encoding="utf-8") as f:
                previous = json.load(f).get("statusLine")
        except (OSError, json.JSONDecodeError):
            # 备份读不出来 → 保留文件供手动抢救，statusLine 走移除分支
            p(f"[yellow]{t('cc_backup_corrupt', path=CC_BACKUP_PATH)}[/yellow]")
        else:
            os.remove(CC_BACKUP_PATH)
    if isinstance(previous, dict):
        settings["statusLine"] = previous
        p(f"[green]✓[/green] {t('cc_restored')}")
    else:
        settings.pop("statusLine", None)
        p(f"[green]✓[/green] {t('cc_removed')}")
    settings.pop("tokenTracker", None)  # 顺手清掉老用户在 settings 里的子字段残留
    if os.path.exists(STATUS_FILE):
        os.remove(STATUS_FILE)
        p(f"[green]✓[/green] {t('deleted_cache', path=STATUS_FILE)}")


def _optout_claude(p) -> None:
    """CC opt-out：删 tt 脚本 + 只还原「本来是 tt 的」statusLine，用户自定义的完全不碰。
    settings.json 损坏时不碰 settings（只删脚本），避免安装路径 json.load 抛异常的崩溃循环。"""
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
    if os.path.exists(CLAUDE_SETTINGS):
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            settings = None
        if isinstance(settings, dict):
            before = json.dumps(settings, sort_keys=True)
            _restore_cc_statusline(settings, p)
            if json.dumps(settings, sort_keys=True) != before:  # 有实际改动才写回
                with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=2, ensure_ascii=False)
    p(f"[dim]{t('cc_statusline_skipped')}[/dim]")


def _setup_claude(components: SetupComponents, quiet: bool = False) -> None:
    """CC 端装/卸 statusLine 接管。用户意图（components.cc_statusline）先写入 config.json（镜像 _setup_codex）。"""
    p = (lambda *a, **k: None) if quiet else get_console().print
    config.save_cc_statusline(components.cc_statusline)  # 写入意图（任何文件操作之前，镜像 _setup_codex）

    if not components.cc_statusline:
        _optout_claude(p)
        return

    settings: dict = {}
    if os.path.exists(CLAUDE_SETTINGS):
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            # settings.json 损坏时不能静默覆盖（里面可能是用户手改打错的配置）——
            # 报错跳过 CC 端；错误不受 quiet 抑制（wizard 场景也必须让用户看到）
            get_console().print(f"[red]{t('cc_settings_corrupt', path=CLAUDE_SETTINGS)}[/red]")
            return

    _write_cc_statusline_script()

    _migrate_cc_legacy_backup(settings)  # 老用户：把藏在 settings 里的备份挪到 cc-backup.json

    existing = settings.get("statusLine")
    if existing and not _is_tt_cc_command(existing.get("command") or ""):
        # 用户原 statusLine 备份到独立文件，不污染 agent 配置
        p(f"[yellow]{t('sl_backup_replace')}[/yellow]")
        os.makedirs(_TT, exist_ok=True)
        with open(CC_BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump({"statusLine": existing}, f, indent=2)

    python = sys.executable or "python3"
    settings["statusLine"] = {"type": "command", "command": _build_cc_command(python, HOOK_SCRIPT_PATH)}

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    p(f"[green]✓[/green] {t('cc_configured')}")
    p(f"[dim]{t('restart_cc')}[/dim]")


def _setup_codex(components: SetupComponents, quiet: bool = False) -> None:
    """Codex 端同步用户级 hooks.json，**不再动 [tui].status_line**。

    Stop（可选伪 statusline）与 UserPromptSubmit（sidebar）由同一个 installer 原子合并；
    旧 config.toml 内联 Stop 在 JSON 成功落盘后迁移，[hooks.state] 原样保留。
    """
    p = (lambda *a, **k: None) if quiet else get_console().print
    if not os.path.isdir(CODEX_DIR):
        return

    config.save_codex_faux_statusline(components.codex_faux_statusline)  # 写入意图

    if components.codex_faux_statusline:
        _write_codex_statusline_script()
    else:
        _uninstall_codex_statusline("")

    _sync_codex_managed_hooks(quiet=quiet)
    p(f"[green]✓[/green] {t('codex_configured')}")
    if components.codex_faux_statusline:
        p(f"[dim]{t('codex_statusline_hint')}[/dim]")
    p(f"[dim]{t('restart_codex')}[/dim]")


def _setup_codex_sidebar(quiet: bool = False) -> None:
    """安装用户级 $tt-sidebar Skill；两个 Codex Hook 已由 _setup_codex 统一同步。"""
    p = (lambda *a, **k: None) if quiet else get_console().print
    try:
        skill_changed = sidebar_install.install_skill()
    except FileExistsError:
        get_console().print(
            f"[yellow]{t('sidebar_skill_conflict', path=sidebar_install.SIDEBAR_SKILL_DIR)}[/yellow]"
        )
        skill_changed = False
    if skill_changed:
        p(
            f"[green]✓[/green] "
            f"{t('sidebar_skill_installed', path=sidebar_install.SIDEBAR_SKILL_DIR)}"
        )


def _setup_kimi_statusline(components: SetupComponents, quiet: bool = False) -> None:
    """Kimi 端装/卸 statusline（tui.toml 的 [status_line].command）。意图先落盘（镜像 _setup_codex）。
    opt-out（components.kimi_statusline=False）删脚本 + 只摘 tt 的 command，用户自定义完全不碰；
    用户自定义 command 存在时即便选了 True 也绝不覆盖（do-no-harm，同 CC 哲学）。"""
    p = (lambda *a, **k: None) if quiet else get_console().print
    config.save_kimi_statusline(components.kimi_statusline)  # 写入意图（任何文件操作之前）

    if not components.kimi_statusline:
        _remove_kimi_statusline_artifacts()
        p(f"[dim]{t('kimi_statusline_skipped')}[/dim]")
        return

    if sidebar_install.kimi_statusline_user_custom():
        p(f"[yellow]{t('kimi_statusline_skipped_custom')}[/yellow]")
        return

    _write_kimi_statusline_script()
    previous = sidebar_install.kimi_statusline_tui_command()
    try:
        changed = sidebar_install.install_kimi_statusline(_kimi_statusline_command())
    except ValueError:
        # tui.toml 损坏时不能静默覆盖（可能是用户手改打错）——报错跳过；错误不受 quiet 抑制
        get_console().print(f"[red]{t('kimi_tui_corrupt', path=sidebar_install.KIMI_TUI)}[/red]")
        return
    if changed:
        key = "kimi_statusline_synced" if previous else "kimi_statusline_installed"
        p(f"[green]✓[/green] {t(key)}")
        p(f"[dim]{t('kimi_statusline_hint')}[/dim]")
    else:
        # 无变更也要给确认行（与 CC/Codex 的「已配置」一致），否则用户无从判断组件状态
        p(f"[green]✓[/green] {t('kimi_statusline_installed')}")


def _remove_kimi_statusline_artifacts() -> None:
    """删 Kimi statusline 运行产物（脚本 + state/quota 缓存 + lock）+ 只摘 tui.toml 里 tt 的 command。
    opt-out 与 unsetup 共用；损坏 tui.toml 无法定位 tt command → 不碰（只删脚本/缓存）。"""
    artifacts = [KIMI_STATUSLINE_HOOK_PATH, KIMI_STATUSLINE_STATE_PATH, KIMI_STATUSLINE_QUOTA_PATH]
    artifacts += [f"{p}.lock" for p in artifacts[1:]]
    for path in artifacts:
        if os.path.exists(path):
            os.remove(path)
    try:
        sidebar_install.uninstall_kimi_statusline()
    except ValueError:
        pass


def _setup_kimi_sidebar(quiet: bool = False) -> None:
    """Kimi 端：安装 $KIMI_CODE_HOME/skills 下的 tt-sidebar Skill + config.toml 的
    UserPromptSubmit hook（sidebar 事件通道；statusline 组件由 _setup_kimi_statusline 负责）。"""
    p = (lambda *a, **k: None) if quiet else get_console().print
    try:
        skill_changed = sidebar_install.install_kimi_skill()
    except FileExistsError:
        get_console().print(
            f"[yellow]{t('sidebar_skill_conflict', path=sidebar_install.KIMI_SKILL_DIR)}[/yellow]"
        )
        skill_changed = False
    try:
        hooks_changed = sidebar_install.install_kimi_hooks()
    except ValueError:
        get_console().print(
            f"[red]{t('kimi_hooks_corrupt', path=sidebar_install.KIMI_CONFIG)}[/red]"
        )
        hooks_changed = False
    if skill_changed:
        p(f"[green]✓[/green] {t('kimi_skill_installed', path=sidebar_install.KIMI_SKILL_DIR)}")
    if hooks_changed:
        p(f"[green]✓[/green] {t('kimi_hooks_synced')}")
    if skill_changed or hooks_changed:
        p(f"[dim]{t('kimi_hook_hint')}[/dim]")


# --- unsetup ---

def unsetup() -> None:
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)
    has_kimi = os.path.isdir(_KIMI)

    if has_cc:
        _unsetup_claude()
    if has_codex:
        _unsetup_codex()
        _unsetup_codex_sidebar()
    if has_kimi:
        _unsetup_kimi_statusline()
        _unsetup_kimi_sidebar()
    if not has_cc and not has_codex and not has_kimi:
        get_console().print(f"[dim]{t('no_agent_detected')}[/dim]")


def _unsetup_claude() -> None:
    _migrate_legacy()  # 顺手清旧位置残留（老用户 unsetup 时也清）
    if os.path.exists(HOOK_SCRIPT_PATH):
        os.remove(HOOK_SCRIPT_PATH)
        get_console().print(f"[green]✓[/green] {t('deleted_file', path=HOOK_SCRIPT_PATH)}")

    if not os.path.exists(CLAUDE_SETTINGS):
        return

    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        # 损坏就不动 settings.json（无法定位 tt 的 statusLine 段），提示手动处理
        get_console().print(f"[red]{t('cc_settings_corrupt_unsetup', path=CLAUDE_SETTINGS)}[/red]")
        return

    _restore_cc_statusline(settings, get_console().print)

    with open(CLAUDE_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _unsetup_codex() -> None:
    """卸载 Codex 端：移除伪 statusline hook + 脚本。
    老用户残留：如有 codex-backup.json（旧版我们改过 status_line），恢复原值；新版不再动 status_line。"""
    result = _read_codex_config()
    content = result[0] if result else ""

    # config.toml 不存在 / 损坏也必须先清运行产物；hooks.json 由 _unsetup_codex_sidebar 统一清。
    content = _uninstall_codex_statusline(content)
    if not result:
        return

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


def _unsetup_codex_sidebar() -> None:
    if sidebar_install.uninstall_skill():
        get_console().print(
            f"[green]✓[/green] {t('deleted_file', path=sidebar_install.SIDEBAR_SKILL_DIR)}"
        )
    try:
        hook_removed = sidebar_install.uninstall_managed_hooks()
    except ValueError:
        get_console().print(
            f"[red]{t('codex_hooks_corrupt_unsetup', path=sidebar_install.CODEX_HOOKS)}[/red]"
        )
        return
    if hook_removed:
        get_console().print(f"[green]✓[/green] {t('codex_hooks_removed')}")


def _unsetup_kimi_statusline() -> None:
    """卸载 Kimi statusline：删脚本 + state/quota 缓存（含 lock）+ 只摘 tui.toml 里 tt 的 command。
    与 _unsetup_claude/_unsetup_codex 一致：不动意图字段（用户重跑 tt setup 时可按原意图恢复）。"""
    paths = [
        KIMI_STATUSLINE_HOOK_PATH,
        KIMI_STATUSLINE_STATE_PATH,
        f"{KIMI_STATUSLINE_STATE_PATH}.lock",
        KIMI_STATUSLINE_QUOTA_PATH,
        f"{KIMI_STATUSLINE_QUOTA_PATH}.lock",
    ]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            key = "deleted_file" if path == KIMI_STATUSLINE_HOOK_PATH else "deleted_cache"
            get_console().print(f"[green]✓[/green] {t(key, path=path)}")
    try:
        removed = sidebar_install.uninstall_kimi_statusline()
    except ValueError:
        get_console().print(f"[red]{t('kimi_tui_corrupt_unsetup', path=sidebar_install.KIMI_TUI)}[/red]")
        return
    if removed:
        get_console().print(f"[green]✓[/green] {t('kimi_statusline_removed')}")


def _unsetup_kimi_sidebar() -> None:
    if sidebar_install.uninstall_kimi_skill():
        get_console().print(
            f"[green]✓[/green] {t('deleted_file', path=sidebar_install.KIMI_SKILL_DIR)}"
        )
    try:
        hook_removed = sidebar_install.uninstall_kimi_hooks()
    except ValueError:
        get_console().print(
            f"[red]{t('kimi_hooks_corrupt_unsetup', path=sidebar_install.KIMI_CONFIG)}[/red]"
        )
        return
    if hook_removed:
        get_console().print(f"[green]✓[/green] {t('kimi_hooks_removed')}")
