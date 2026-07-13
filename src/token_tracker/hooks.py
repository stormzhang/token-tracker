import json
import os
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from importlib import resources

from . import config
from .adapters.util import claude_home, codex_home
from .i18n import t
from .ui import themes
from .ui.console import get_console

_CLAUDE = claude_home()  # CLAUDE_CONFIG_DIR 覆盖 / ~/.claude
_CODEX = codex_home()    # CODEX_HOME 覆盖 / ~/.codex


@dataclass
class SetupComponents:
    """组件开关。CC statusLine 接管与 Codex 伪 statusline（Stop hook）均为可选组件，意图持久化到 config.json。"""
    cc_statusline: bool = True
    codex_faux_statusline: bool = True

    @classmethod
    def all_on(cls) -> "SetupComponents":
        return cls(cc_statusline=True, codex_faux_statusline=True)

# tt 自己的产物（statusline 脚本 + 缓存 + 备份）集中放 ~/.config/token-tracker（XDG，跟 theme/lang 同处）；
# settings.json / config.toml 是「改 agent 自己的配置」、必须留 agent 目录。statusLine/hook 的 command
# 是绝对路径，脚本放 agent 目录外照样跑（实测 + ccstatusline 等业界用 npx 全局脚本同理）。
_TT = config.CONFIG_DIR  # ~/.config/token-tracker

CLAUDE_SETTINGS = os.path.join(_CLAUDE, "settings.json")  # 改 Claude Code 配置，留 agent 目录
HOOK_SCRIPT_PATH = os.path.join(_TT, "claude-statusline.py")
CODEX_DIR = _CODEX
CODEX_CONFIG = os.path.join(CODEX_DIR, "config.toml")     # 改 Codex 配置，留 agent 目录
CODEX_STATUSLINE_HOOK_PATH = os.path.join(_TT, "codex-statusline.py")
STATUS_FILE = config.STATUS_FILE                          # CC statusline 缓存（单一权威定义在 config）
HOOK_VERSION = "2.0"
STATUSLINE_HOOK_VERSION = "1.2"

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


# 卸载时定位 tt 追加的整段 [[hooks.Stop]]——同时认新（codex-statusline）/ 旧（tt-statusline）两种特征码。
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

# tt Stop hook 的 command 值（去掉外层 TOML 引号）；与删除正则同一套三代形态
_CODEX_COMMAND_REGEX = re.compile(
    r'command = ("[^"\n]*(?:codex-statusline|tt-statusline)[^"\n]*"'
    r"|'[^'\n]*(?:codex-statusline|tt-statusline)[^'\n]*')"
)


def _has_tt_codex_statusline(content: str) -> bool:
    return "codex-statusline" in content or "tt-statusline" in content


def _codex_hook_command(content: str) -> str | None:
    """从 config.toml 内容提取 tt Stop hook 的 command 值（含内层引号、不含外层 TOML 引号）。"""
    m = _CODEX_COMMAND_REGEX.search(content)
    return m.group(1)[1:-1] if m else None


def _install_codex_statusline(content: str, python: str) -> str:
    """落盘 Codex statusline 脚本 + 在 config.toml 末尾追加 Stop hook 段。
    command 与 CC 侧同一拼法（_build_cc_command：双引号包路径 + Windows 正斜杠，#13/#14 同治）。
    - 已有段的 command 与本次要写的完全一致 → 幂等返回；
    - 不一致（python 升级/换环境、老名 tt-statusline、旧裸拼接格式）→ 删旧段装新段，
      避免 command 指向已死 python 或在含空格路径上断词（症状：状态栏静默半残）。"""
    _write_codex_statusline_script()
    cmd = _build_cc_command(python, CODEX_STATUSLINE_HOOK_PATH)
    if _has_tt_codex_statusline(content):
        if _codex_hook_command(content) == cmd:
            return content  # 新格式 + python/脚本路径一致 → 幂等
        content = _CODEX_STATUSLINE_REGEX.sub("\n", content)  # 其余一律删旧装新
    return content.rstrip() + (
        "\n\n[[hooks.Stop]]\n\n"
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        # 用 TOML literal string（单引号）包裹 command，避免 Windows 反斜杠路径被当转义符
        # 解析失败（如 `C:\Users\...` 里的 `\U` 被识别为 unicode 转义起始）；
        # 值内的双引号来自 _build_cc_command 的路径包裹，literal string 内原样合法
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
    Codex 端无从探测「用户自己的 statusline」：已记录意图非 None → 用意图，否则 → True。"""
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
    return SetupComponents(cc_statusline=cc, codex_faux_statusline=codex)


def is_setup() -> bool:
    """已配置 = 每个已装 agent 的组件意图都明确、且意图为 True 的组件实装好（双因素）。
    意图 False 则用户明确不要、不强求文件存在（自定义 statusLine 用户跑报表不再被抢占）。
    CC 端例外：意图缺失（None）但 statusLine 已是 tt 的 → 按存量用户推断为已配（不打扰）。"""
    has_cc = os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))
    has_codex = os.path.isdir(CODEX_DIR)
    if not has_cc and not has_codex:
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


def _codex_command_needs_sync() -> bool:
    """config.toml 里 tt Stop hook 的 command 是否还是旧格式（裸拼接 / 反斜杠）——
    与 CC 侧 #13/#14 同类问题，格式规则复用 _cc_command_outdated。没装 / 没段 → False。"""
    result = _read_codex_config()
    if not result:
        return False
    cmd = _codex_hook_command(result[0])
    return cmd is not None and _cc_command_outdated(cmd)


def _sync_codex_command() -> None:
    """把 config.toml 里 tt Stop hook 的 command 重写为新格式（删旧段装新段，其余内容不动）。"""
    result = _read_codex_config()
    if not result:
        return
    content = result[0]
    new_content = _install_codex_statusline(content, sys.executable or "python3")
    if new_content != content:
        with open(CODEX_CONFIG, "w", encoding="utf-8") as f:
            f.write(new_content)


def needs_update() -> bool:
    # 只在已安装（新位置脚本文件存在）时纳入版本判断，未装不主动装
    if os.path.exists(HOOK_SCRIPT_PATH) and _installed_hook_version() != HOOK_VERSION:
        return True
    sv = _installed_codex_statusline_version()
    if sv is not None and sv != STATUSLINE_HOOK_VERSION:
        return True
    if _codex_command_needs_sync():  # config.toml 里 Stop hook command 旧格式（同 #13/#14）
        return True
    return _cc_command_needs_sync()  # settings.json 里 command 格式过时也算待更新（issue #13/#14）


def update_hook() -> None:
    if os.path.exists(HOOK_SCRIPT_PATH):  # 已装才同步（未装不主动装）
        _write_cc_statusline_script()
    if _installed_codex_statusline_version() is not None:
        _write_codex_statusline_script()
    if _cc_command_needs_sync():
        _sync_cc_command()
    if _codex_command_needs_sync():
        _sync_codex_command()


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

    if not has_cc and not has_codex:
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
