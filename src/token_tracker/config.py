"""统一配置存储：~/.config/token-tracker/config.json（固定路径、不读 XDG_CONFIG_HOME——
跨平台行为一致 + statusline 模板内硬编码同路径；不绑 Claude Code）。

包含所有 tt 自身偏好——主题、语言、组件意图。schema_version 用于未来格式迁移。

主题解析优先级：`TT_THEME` 环境变量（兼容旧 light/dark 别名）> 配置文件 > `COLORFGBG` 自动
> mocha 默认。`auto` 仅在 Catppuccin mocha↔latte 间按终端深浅切。

字段读取一律 fallback + 严格类型校验——config 可能被用户手改 / JSON 损坏 / 缺字段，
任何不一致都视为"没表达意图"，避免 stale config 导致 is_setup 假装"已配"。
"""

import json
import os

from .ui import themes

CONFIG_DIR = os.path.expanduser("~/.config/token-tracker")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
# CC statusline 缓存的单一权威路径（statusline 脚本写、tt status / adapters.rate_limits 读）。
# 注意两个 statusline 模板因脚本独立运行硬编码了对应缓存路径——改这里必须同步改模板。
STATUS_FILE = os.path.join(CONFIG_DIR, "tt-status.json")
# Codex Stop hook 采集的会话→终端窗格映射。与 CC 心跳/status 缓存分文件，避免两个
# Agent 并发 read-modify-write 同一 JSON 时互相覆盖；sidebar 读取时再合并。
TERMINAL_MAP_FILE = os.path.join(CONFIG_DIR, "tt-terminal-map.json")
SCHEMA_VERSION = 1

# 引导版本：每次新增"值得让老用户重新走一遍 setup"的配置项时手动 +1（只能整数、一次 +1）。
# 老用户 config 里没这字段 / 旧版本号 < 当前 → 触发重新引导（真终端弹 wizard、非 tty 静默 _auto_setup）。
# 跟 SCHEMA_VERSION 解耦：那是数据格式版本，这是用户引导版本，bump 节奏完全不同。
# 2（0.4.2）：强制所有现存用户（0.3.8/0.4.0=无字段=0、0.4.1=1，全 < 2）升级后重走一遍 setup。
# 3：安装随包分发的用户级 Codex $tt-sidebar Skill + UserPromptSubmit hook。
# 4：安装 Kimi Code 的 tt-sidebar Skill（$KIMI_CODE_HOME/skills）+ config.toml 的 UserPromptSubmit hook。
# 5：安装 Kimi Code statusline（tui.toml 的 [status_line].command + kimi-statusline.py 脚本）。
# 6：安装 Pi statusline（~/.pi/agent/extensions/tt-statusline.ts 扩展 + pi-statusline.py 脚本）。
# 注：CC statusLine 变可选组件（issue #16/#17）时决定**不 bump**——小众需求不打断存量用户，
# 存量 tt 用户 intent 缺失时由 is_setup 按「statusLine 已是 tt 的」推断为已配，想改的手动 tt setup。
SETUP_VERSION = 6

# 旧位置（独立 theme.json / lang.json），老用户首次读 config.json 不存在时自动合并迁移
_LEGACY_THEME_PATH = os.path.join(CONFIG_DIR, "theme.json")
_LEGACY_LANG_PATH = os.path.join(CONFIG_DIR, "lang.json")

# 旧 TT_THEME 值兼容映射
_ALIASES = {"light": "latte", "dark": "mocha"}


def _read_json(path: str) -> dict:
    """读 JSON 文件，缺失/损坏/非 dict 都返回空 dict（不抛）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _migrate_legacy_if_needed() -> dict | None:
    """老用户首次读 config.json 不存在 → 从 theme.json/lang.json 合并迁移；返回迁移后的 data，否则 None。"""
    if os.path.exists(CONFIG_PATH):
        return None
    theme_data = _read_json(_LEGACY_THEME_PATH)
    lang_data = _read_json(_LEGACY_LANG_PATH)
    if not theme_data and not lang_data:
        return None  # 旧文件也没有，纯新用户
    data: dict = {"schema_version": SCHEMA_VERSION}
    if "theme" in theme_data:
        data["theme"] = theme_data["theme"]
    if "lang" in lang_data:
        data["lang"] = lang_data["lang"]
    _write(data)
    # 删旧文件
    for p in (_LEGACY_THEME_PATH, _LEGACY_LANG_PATH):
        try:
            os.remove(p)
        except OSError:
            pass
    return data


def _write(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_config() -> dict:
    """读 config.json，缺失时自动从老文件迁移；schema_version 不对走迁移；坏数据返回空 dict。"""
    migrated = _migrate_legacy_if_needed()
    if migrated is not None:
        return migrated
    data = _read_json(CONFIG_PATH)
    # 这里以后可加 schema 迁移：if data.get("schema_version") != SCHEMA_VERSION: data = migrate(data)
    return data


def _save_field(key: str, value) -> None:
    """更新 config.json 一个字段（保留其它字段，写入 schema_version）。"""
    data = load_config()
    data[key] = value
    data["schema_version"] = SCHEMA_VERSION
    _write(data)


# --- theme ---


def save_theme(name: str) -> None:
    _save_field("theme", name)


def _normalize(name: str) -> str:
    name = name.strip().lower()
    return _ALIASES.get(name, name)


def _auto_theme() -> str:
    """按 COLORFGBG 判终端深浅：浅色 → latte，否则 → mocha。"""
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        try:
            if int(parts[-1]) > 8:
                return "latte"
        except (ValueError, IndexError):
            pass
    return "mocha"


def resolve_theme() -> str:
    """按优先级链解析出当前生效的主题名（保证是 themes.THEMES 里的合法键）。"""
    env = _normalize(os.environ.get("TT_THEME", ""))
    if env == "auto":
        return _auto_theme()
    if env in themes.THEMES:
        return env
    cfg_val = load_config().get("theme")
    cfg = _normalize(cfg_val) if isinstance(cfg_val, str) else ""
    if cfg == "auto":
        return _auto_theme()
    if cfg in themes.THEMES:
        return cfg
    return _auto_theme()


# --- lang ---


def save_lang(lang: str) -> None:
    _save_field("lang", lang)


def resolve_lang() -> str | None:
    """读用户保存的语言偏好，严格校验类型 + 取值。无效返回 None（让 i18n 走环境变量兜底）。"""
    val = load_config().get("lang")
    return val if val in ("zh", "en") else None


# --- components 意图（双因素之一：is_setup / wizard 总结读这个 + 看文件） ---


def save_codex_faux_statusline(enabled: bool) -> None:
    """wizard 选完后写入意图。"""
    _save_field("codex_faux_statusline", bool(enabled))


def codex_faux_statusline_intent() -> bool | None:
    """读用户对 Codex 伪 statusline 的意图。严格 bool；非 bool / 缺字段 → None（视为没表达）。"""
    val = load_config().get("codex_faux_statusline")
    return val if isinstance(val, bool) else None


def save_cc_statusline(enabled: bool) -> None:
    """wizard 选完后写入意图（CC statusLine 是否由 tt 接管）。"""
    _save_field("cc_statusline", bool(enabled))


def cc_statusline_intent() -> bool | None:
    """读用户对 CC statusLine 接管的意图。严格 bool；非 bool / 缺字段 → None（视为没表达）。"""
    val = load_config().get("cc_statusline")
    return val if isinstance(val, bool) else None


def save_kimi_statusline(enabled: bool) -> None:
    """wizard 选完后写入意图（Kimi tui.toml 的 status_line.command 是否由 tt 接管）。"""
    _save_field("kimi_statusline", bool(enabled))


def kimi_statusline_intent() -> bool | None:
    """读用户对 Kimi statusline 接管的意图。严格 bool；非 bool / 缺字段 → None（视为没表达）。"""
    val = load_config().get("kimi_statusline")
    return val if isinstance(val, bool) else None


def save_pi_statusline(enabled: bool) -> None:
    """wizard 选完后写入意图（Pi 扩展状态栏是否安装）。"""
    _save_field("pi_statusline", bool(enabled))


def pi_statusline_intent() -> bool | None:
    """读用户对 Pi statusline 的意图。严格 bool；非 bool / 缺字段 → None（视为没表达）。"""
    val = load_config().get("pi_statusline")
    return val if isinstance(val, bool) else None


# --- setup 引导版本（老用户升级后重新引导一次的判定依据） ---


def save_setup_version(v: int = SETUP_VERSION) -> None:
    """setup 真正完成后写入当前 SETUP_VERSION，标记"已按当前版本引导过"。"""
    _save_field("setup_version", int(v))


def setup_version() -> int:
    """读已落地的 setup_version；老用户 / 字段缺失 / 非 int → 0（触发重新引导）。"""
    val = load_config().get("setup_version")
    return val if isinstance(val, int) else 0
