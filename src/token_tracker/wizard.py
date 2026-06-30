"""首次运行交互配置向导：选主题 + 选增强项，再落地状态栏 + 选择的组件。

仅在「首次运行 + tty + 非会话内」时进入（判定在 cli.py），其它场景（CI / 脚本 /
`!tt` / AI 会话内）一律降级到静默 `setup(auto=True)`。
用 questionary 提供上下键选择体验；agent 由 setup 自动检测、语言跟随系统。
"""

import copy
import os
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager

import questionary
from prompt_toolkit.styles import Style
from questionary.prompts import common as _q_common

from . import config, i18n
from .hooks import CLAUDE_SETTINGS, CODEX_DIR, SetupComponents, setup
from .i18n import t
from .ui import themes
from .ui.console import get_console

_POINTER = "❯"  # 光标字符（取代默认 »；现代 CLI 风、视觉更明显）
_QMARK = "●"    # 问题前缀符（取代默认 ?；圆点）


@contextmanager
def _highlight_listtitle_names():
    """临时给 questionary 打两处补丁，专治带色板的 list 标题选项。只在主题选择期间生效、用完还原。

    1. 列表行名字高亮：questionary 对 list 标题（FormattedText）直接 `tokens.extend`、跳过光标
       高亮逻辑（源码确认），导致主题名不跟光标变色。这里在 `_get_choice_tokens` 输出后处理：
       光标行（含 `[SetCursorPosition]`）里空 style 的文本 token（= 主题名，色板 token 带 hex）染 mauve。
    2. 选完回显去色板：select 回显（is_answered）会把选中项 list 标题的所有 token 文本拼出来
       （名字 + 色板 ■），这里 patch `get_pointed_at`，回显阶段只返回名字（去 padding、丢色板）。
    """
    orig_tokens = _q_common.InquirerControl._get_choice_tokens
    orig_gpa = _q_common.InquirerControl.get_pointed_at
    mauve = themes.get_theme("mocha")["base"]["mauve"]  # 固定 mocha 风格（引导界面不跟随所选主题）

    def patched_tokens(self):
        result = []
        in_cursor_row = False
        for tok in orig_tokens(self):
            style, text = tok[0], tok[1]
            if style == "[SetCursorPosition]":
                in_cursor_row = True
                result.append(tok)
            elif in_cursor_row and text == "\n":
                in_cursor_row = False
                result.append(tok)
            elif in_cursor_row and style == "" and text.strip():
                result.append((f"fg:{mauve} bold", text))  # 光标行的名字 token 染高亮、保留原文（含 padding）
            else:
                result.append(tok)
        return result

    def patched_gpa(self):
        choice = orig_gpa(self)
        if getattr(self, "is_answered", False) and isinstance(choice.title, list):
            c = copy.copy(choice)
            c.title = choice.title[0][1].rstrip()  # 回显只留名字 token（去 padding）、丢色板
            return c
        return choice

    _q_common.InquirerControl._get_choice_tokens = patched_tokens  # type: ignore[method-assign]
    _q_common.InquirerControl.get_pointed_at = patched_gpa  # type: ignore[method-assign]
    try:
        yield
    finally:
        _q_common.InquirerControl._get_choice_tokens = orig_tokens  # type: ignore[method-assign]
        _q_common.InquirerControl.get_pointed_at = orig_gpa  # type: ignore[method-assign]


def _wizard_style() -> Style:
    """questionary 配色固定 mocha 风格——引导界面不跟随所选主题（与报表 / 状态栏配色解耦）。"""
    base = themes.get_theme("mocha")["base"]
    return Style.from_dict({
        "qmark": f"fg:{base['green']} bold",                       # 问题前 ? 用 green
        "question": "bold",                                         # 问题文本加粗
        "pointer": f"fg:{base['mauve']} bold",                     # 光标 ❯ 用 mauve
        "highlighted": f"noreverse fg:{base['mauve']} bold",       # cursor 当前行 mauve+bold
        "instruction": f"fg:{base['overlay0']}",                   # (Use arrow keys) 暗灰
        "answer": f"fg:{base['green']} bold",                      # 回答展示 green + 加粗
    })


def _current_first(choices: list, current) -> list:
    """把 value==current 的项重排到最前（作 cursor 起点），其余保持原序。

    关键：不能给 questionary.select 传 default —— 它的 `_is_selected` 会把 default 项永久标成
    `class:selected`，且渲染优先级 selected > highlighted，导致 default 项无论光标在哪都高亮、
    不跟光标走（已对源码确认）。所以改用「重排让当前值打头」设初始光标，全程无 selected 标记。
    """
    def val(c):
        return c.value if isinstance(c, questionary.Choice) else c
    front = [c for c in choices if val(c) == current]
    rest = [c for c in choices if val(c) != current]
    return front + rest


def _select(message, choices):
    """questionary.select 包一层、统一注入 wizard style 和 ❯ 光标（不传 default，见 _current_first）。
    instruction 传单空格关掉默认的 `(Use arrow keys)` 提示（传空串会 fallback 回默认、故用空格）。"""
    return questionary.select(
        message, choices=choices,
        style=_wizard_style(), pointer=_POINTER, qmark=_QMARK, instruction=" ",
    )


def _has_cc() -> bool:
    return os.path.isdir(os.path.dirname(CLAUDE_SETTINGS))


def _has_codex() -> bool:
    return os.path.isdir(CODEX_DIR)


def _ask_language(prefix: str = "") -> None:
    """每次都问语言（作为配置项之一、带步骤号；光标默认停在已存的语言上，回车即保留现状）。
    prompt 双语硬编码（此时 i18n 还没确定语言）；选完保存 + i18n.set_lang() 即时切换。
    Ctrl+C 中断沿用现状、不改任何状态。
    """
    langs = [
        questionary.Choice(title="中文", value="zh"),
        questionary.Choice(title="English", value="en"),
    ]
    choice = _select(
        f"{prefix}Language / 语言",
        _current_first(langs, config.resolve_lang() or "en"),
    ).ask()
    if choice not in ("zh", "en"):
        return  # Ctrl+C 沿用现状
    config.save_lang(choice)
    i18n.set_lang(choice)


def _disp_width(s: str) -> int:
    """显示宽度：东亚全角 / 宽字符算 2 列、其余 1 列（全角「（推荐）」用 len 会错位）。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _theme_label(name: str) -> str:
    """主题名 +（mocha）紧跟「（推荐）」。"""
    return name if name != "mocha" else f"{name} {t('theme_recommended')}"


def _theme_choice(name: str, name_width: int) -> questionary.Choice:
    """主题选项：同一行拼「名字区（空 style，含 mocha 的『（推荐）』）+ 8 色块（各主题基色 hex）」。

    名字区故意用空 style —— 配合 `_highlight_listtitle_names()` 补丁，光标到哪行那行名字
    染 mauve 高亮（和语言 / Yes-No 步一致）；色板 token 带 hex 色、补丁不动、保持各自本色。
    name_width 是所有 label 的最大显示宽（调用方算），按它 pad 让色板列对齐。
    """
    slots = ("green", "yellow", "peach", "red", "blue", "sapphire", "mauve", "pink")
    base = themes.get_theme(name)["base"]
    label = _theme_label(name)
    pad = " " * max(1, name_width - _disp_width(label) + 2)  # +2 留名字与色板间距
    parts: list[tuple[str, str]] = [("", label + pad)]
    for slot in slots:
        parts.append((base[slot], "■ "))
    return questionary.Choice(title=parts, value=name)


def _ask_yes_no(message: str, default: bool = True) -> bool:
    """_select 包 Yes/No 两项（上下键选 + 回车确认，体验统一）。Ctrl+C → 用默认值。
    把默认项重排到首位作光标起点（不传 default 避免永久 selected 标记）。"""
    ans = _select(
        message,
        _current_first(["Yes", "No"], "Yes" if default else "No"),
    ).ask()
    if ans is None:
        return default
    return ans == "Yes"


def ask_components(step_prefix_fn: Callable[[int], str] | None = None) -> SetupComponents:
    """按检测到的 agent 问增强项；返回 SetupComponents。说明压进问题一行、选完直接下一项。

    step_prefix_fn(i) 返回第 i 个问题的步骤前缀（i 从 1 开始）；不传则无前缀。
    语言由调用方（wizard）在更早的步骤问过，这里不重复。
    """
    has_cc = _has_cc()
    has_codex = _has_codex()
    qi = 1
    claude_sl = False
    codex_faux = False
    prefix = step_prefix_fn or (lambda i: "")

    if has_cc:
        claude_sl = _ask_yes_no(f"{prefix(qi)}{t('wizard_q_claude_statusline')}", default=claude_sl)
        qi += 1

    if has_codex:
        codex_faux = _ask_yes_no(f"{prefix(qi)}{t('wizard_q_codex_statusline')}", default=codex_faux)
        qi += 1

    return SetupComponents(claude_statusline=claude_sl, codex_faux_statusline=codex_faux)


def _print_summary(console, choice: str, components: SetupComponents) -> None:
    """选完所有项后的综合简洁总结：键值对齐的配置回顾 + 一行重启/下一步提示。
    层次：暗色标签 + 值正常色、状态用 ✓/✗ 图标、✓ 配置完成头用绿。
    状态栏显示用双因素（意图 AND 文件存在）。"""
    from .hooks import claude_statusline_active, codex_statusline_active  # 延迟 import 避免循环
    base = themes.get_theme("mocha")["base"]
    green, pink, dim = base["green"], base["pink"], base["overlay0"]
    lang_name = "中文" if i18n.LANG == "zh" else "English"  # 语言名本身不翻译

    rows = [(t("wizard_summary_lang"), lang_name), (t("wizard_summary_theme"), choice)]
    if _has_cc():
        state = f"[{green}]✓[/{green}]" if claude_statusline_active() else f"[{dim}]✗[/{dim}]"
        rows.append((t("wizard_summary_claude_statusline"), state))
    if _has_codex():
        # 双因素（意图 AND 文件实装）；_setup_codex 已写入意图，此处直接查 active
        state = f"[{green}]✓[/{green}]" if codex_statusline_active() else f"[{dim}]✗[/{dim}]"
        rows.append((t("wizard_summary_codex_statusline"), state))
    key_w = max(_disp_width(k) for k, _ in rows)

    console.print()
    console.print(f"[{green}]✓[/{green}] {t('wizard_done')}")
    for k, v in rows:
        pad = " " * (key_w - _disp_width(k) + 3)  # 标签右侧对齐值，留 3 空格间距
        console.print(f"  [{dim}]{k}[/{dim}]{pad}[{pink}]{v}[/{pink}]")
    console.print(f"  [{dim}]{t('wizard_restart')}[/{dim}]")
    console.print(f"  [{dim}]{t('wizard_reconfig')}[/{dim}]")
    console.print(f"  [{dim}]{t('wizard_view_reports')}[/{dim}]")
    console.print()
    console.print(f"  [{green}]{t('wizard_signoff')} - by stormzhang[/{green}]")  # 署名 sign-off，非 dim


def run_wizard() -> None:
    # agent 守卫由调用方 cli._run_setup_flow 统一做（唯一入口），这里假设至少有一个 agent。
    from .adapters.registry import detect_agents
    from .cli import _get_version

    console = get_console()
    has_cc = _has_cc()
    has_codex = _has_codex()

    # 总步数：语言 + 主题 + 增强项问题数（按检测到的 agent 决定）
    enhancement_q = (1 if has_cc else 0) + (1 if has_codex else 0)
    total = 2 + enhancement_q

    # 欢迎行（品牌 + 版本，缩进 2）固定英文不随语言；署名移到末尾 sign-off 行；下一行显示检测到的 agent
    console.print()
    green = themes.get_theme("mocha")["base"]["green"]
    console.print(f"  [bold {green}]Welcome to use token-tracker v{_get_version()}[/]")
    agents = detect_agents()
    console.print(f"  [dim]{t('detected', agents=', '.join(a.name + ' ✓' for a in agents))}[/dim]")
    console.print()

    # Step 1: 语言（也是配置项之一、带步骤号、影响后续所有 i18n 文案）
    _ask_language(prefix=f"[1/{total}] ")

    # Step 2: 选主题（步骤号进 message、无额外描述、选完直接下一项）。
    # 主题固定顺序（mocha 打头、「（推荐）」紧跟名字）；内联色板 + 名字跟光标高亮（靠补丁）
    name_width = max(_disp_width(_theme_label(n)) for n in themes.THEME_NAMES)
    with _highlight_listtitle_names():
        choice = _select(
            f"[2/{total}] {t('wizard_pick_theme')}",
            [_theme_choice(n, name_width) for n in themes.THEME_NAMES],
        ).ask()
    if not choice:
        choice = config.resolve_theme()
    config.save_theme(choice)  # 持久化即可；wizard 引导界面固定 mocha、不随选的主题变色

    # Step 3-N: 增强项（仅当检测到 agent；动态步数）
    components = ask_components(step_prefix_fn=lambda i: f"[{i + 2}/{total}] ")

    # 落地配置（静默，由综合总结统一反馈）
    setup(components=components, quiet=True)
    _print_summary(console, choice, components)
