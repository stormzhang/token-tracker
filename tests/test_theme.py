import pytest

from token_tracker import cli, config
from token_tracker.ui import theme, themes


def test_all_themes_have_complete_base_and_heat():
    # 每个主题必须齐 9 基色 + is_light bool + 5 档热力。
    for name in themes.THEME_NAMES:
        spec = themes.get_theme(name)
        assert set(spec["base"]) == set(themes.BASE_SLOTS), name
        assert isinstance(spec["is_light"], bool), name
        heat = themes.heat_colors(name)
        assert len(heat) == 5, name


def test_heat_interpolation_endpoints():
    # 插值主题（无显式 heat）：首档=cell，末档=green。
    spec = themes.get_theme("frappe")
    heat = themes.heat_colors("frappe")
    assert heat[0] == spec["cell"]
    assert heat[-1] == spec["base"]["green"]


def test_mocha_heat_unchanged():
    # mocha 热力显式硬编码，须与历史像素一致（daily 热力图不回归）。
    assert themes.heat_colors("mocha") == ["#313244", "#475951", "#628168", "#7da87f", "#a6e3a1"]


def test_derive_slots_maps_semantics():
    slots = themes.derive_slots(themes.get_theme("mocha")["base"])
    assert slots["token"] == "#74c7ec"  # sapphire
    assert slots["cost"] == "#f9e2af"  # yellow
    assert slots["accent"] == "bold #a6e3a1"  # bold green
    assert slots["dim"] == "#6c7086"  # overlay0


def test_statusline_ansi_truecolor():
    ansi = themes.theme_to_statusline_ansi("mocha")
    assert set(ansi) == set(themes._STATUSLINE_SLOTS) | {"reset"}
    assert ansi["reset"] == "\033[0m"
    # truecolor 主题：着色 key 都是 38;2 序列。
    assert all("38;2" in v for k, v in ansi.items() if k != "reset")
    assert ansi["tokens"] == "\033[38;2;250;179;135m"  # peach #fab387（状态栏沿用旧观感）


def test_statusline_ansi_256_approximation():
    ansi = themes.theme_to_statusline_ansi("mocha", "256")
    assert set(ansi) == set(themes._STATUSLINE_SLOTS) | {"reset"}
    # 256 模式：着色 key 都是 38;5;N，无 truecolor 序列。
    assert all("38;5;" in v for k, v in ansi.items() if k != "reset")
    assert "38;2" not in "".join(ansi.values())


def test_hex_to_256_known_values():
    # mocha green #a6e3a1 → 256 索引 151（与旧手调 statusline 值一致，验证近似算法）。
    assert themes._hex_to_256("#a6e3a1") == 151
    assert themes._hex_to_256("#000000") == 16
    assert themes._hex_to_256("#ffffff") == 231


def test_get_theme_unknown_falls_back_to_mocha():
    assert themes.get_theme("nope") is themes.THEMES["mocha"]


def test_resolve_env_alias_and_name(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.delenv("COLORFGBG", raising=False)
    monkeypatch.setenv("TT_THEME", "light")
    assert config.resolve_theme() == "latte"
    monkeypatch.setenv("TT_THEME", "dark")
    assert config.resolve_theme() == "mocha"
    monkeypatch.setenv("TT_THEME", "nord")
    assert config.resolve_theme() == "nord"


def test_resolve_auto_uses_colorfgbg(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setenv("TT_THEME", "auto")
    monkeypatch.setenv("COLORFGBG", "0;15")
    assert config.resolve_theme() == "latte"
    monkeypatch.setenv("COLORFGBG", "15;0")
    assert config.resolve_theme() == "mocha"


def test_resolve_priority_env_over_config(monkeypatch):
    # env 显式主题应盖过配置文件。
    monkeypatch.setattr(config, "load_config", lambda: {"theme": "dracula"})
    monkeypatch.delenv("COLORFGBG", raising=False)
    monkeypatch.setenv("TT_THEME", "nord")
    assert config.resolve_theme() == "nord"
    # env 未知值时落到配置文件。
    monkeypatch.setenv("TT_THEME", "bogus")
    assert config.resolve_theme() == "dracula"


def test_resolve_config_when_env_absent(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {"theme": "frappe"})
    monkeypatch.delenv("TT_THEME", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    assert config.resolve_theme() == "frappe"


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "cfg" / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "cfg" / "lang.json"))
    config.save_theme("nord")
    assert config.load_config()["theme"] == "nord"


def test_s_proxy_follows_active_theme(monkeypatch):
    monkeypatch.setattr(theme, "_ACTIVE_NAME", None)
    theme.set_active_theme("mocha")
    assert theme._S.token == "#74c7ec"  # mocha sapphire
    assert theme._S.accent == "bold #a6e3a1"  # bold green
    theme.set_active_theme("dracula")
    assert theme._S.token == "#8be9fd"  # dracula cyan→sapphire


def test_s_proxy_unknown_attr_raises(monkeypatch):
    monkeypatch.setattr(theme, "_ACTIVE_NAME", "mocha")
    try:
        _ = theme._S.nope
        raise AssertionError("expected AttributeError")
    except AttributeError:
        pass


def test_preview_theme_restores(monkeypatch):
    monkeypatch.setattr(theme, "_ACTIVE_NAME", "mocha")
    before = theme._S.token
    with theme.preview_theme("nord"):
        assert theme._S.token == "#88c0d0"  # nord frost cyan→sapphire
        assert theme.get_active_theme_name() == "nord"
    assert theme._S.token == before
    assert theme.get_active_theme_name() == "mocha"


def test_heat_greens_follows_theme(monkeypatch):
    monkeypatch.setattr(theme, "_ACTIVE_NAME", "mocha")
    assert theme.heat_greens() == ["#313244", "#475951", "#628168", "#7da87f", "#a6e3a1"]
    theme.set_active_theme("latte")
    assert theme.heat_greens() == ["#ccd0da", "#bbd9b8", "#98c990", "#75b868", "#40a02b"]


def test_theme_set_writes_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    monkeypatch.setattr(cli, "is_setup", lambda: False)  # 不触发 update_hook
    monkeypatch.delenv("TT_THEME", raising=False)
    monkeypatch.setattr(theme, "_ACTIVE_NAME", None)
    cli.cmd_theme(["set", "nord"])
    assert config.load_config()["theme"] == "nord"
    assert theme.get_active_theme_name() == "nord"


def test_theme_set_unknown_exits(monkeypatch):
    monkeypatch.setattr(cli, "is_setup", lambda: False)
    with pytest.raises(SystemExit):
        cli.cmd_theme(["set", "bogus"])


def test_cmd_theme_show_and_list_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    monkeypatch.delenv("TT_THEME", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    cli.cmd_theme([])  # show
    cli.cmd_theme(["list"])  # list 列出所有主题名
    out = capsys.readouterr().out
    assert "mocha" in out and "dracula" in out


def test_cmd_theme_shorthand_set(tmp_path, monkeypatch):
    # `tt theme frappe` 简写 = set frappe
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    monkeypatch.setattr(cli, "is_setup", lambda: False)
    monkeypatch.delenv("TT_THEME", raising=False)
    monkeypatch.setattr(theme, "_ACTIVE_NAME", None)
    cli.cmd_theme(["frappe"])
    assert config.load_config()["theme"] == "frappe"


def test_should_run_wizard(monkeypatch):
    # 双 tty + 非会话内 → 进向导；非 tty 或会话内 → 降级（plan 风险重点）。
    # 会话判定只用硬环境信号（kimi 的目录启发式不能压向导——独立终端同项目目录会误判）。
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_in_agent_session_env", lambda: False)
    assert cli._should_run_wizard() is True
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    assert cli._should_run_wizard() is False
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_in_agent_session_env", lambda: True)
    assert cli._should_run_wizard() is False


def test_in_agent_session_env_uses_only_hard_signals(monkeypatch):
    # 回归：kimi 目录启发式命中（同项目目录有活跃 kimi 会话）不得影响 wizard 判定
    for var in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)
    assert cli._in_agent_session_env() is False
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli._in_agent_session_env() is True


def test_save_resolve_lang_roundtrip(tmp_path, monkeypatch):
    # 写入 zh / en 都能读回；非法值 / 未配置都返回 None（由 i18n 走环境变量兜底）。
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    assert config.resolve_lang() is None  # 未配置
    config.save_lang("zh")
    assert config.resolve_lang() == "zh"
    config.save_lang("en")
    assert config.resolve_lang() == "en"
    config.save_lang("ja")  # 非法值
    assert config.resolve_lang() is None


def test_i18n_set_lang_switches_translations():
    # set_lang 立即切换全局 _CURRENT，后续 t() 返回新语言文案。
    from token_tracker import i18n
    original = i18n.LANG
    try:
        i18n.set_lang("zh")
        assert i18n.t("wizard_pick_theme") == "选择配色主题"
        i18n.set_lang("en")
        assert i18n.t("wizard_pick_theme") == "Pick a theme"
        i18n.set_lang("invalid")  # 非法值 → 兜底 en
        assert i18n.LANG == "en"
    finally:
        i18n.set_lang(original)  # 还原避免污染后续测试


def test_detect_system_lang_win32_falls_back_when_ctypes_unavailable(monkeypatch):
    # Windows 分支：拿不到 windll（如非 Windows 环境）时优雅回退环境变量，不崩。
    from token_tracker import i18n
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    assert i18n._detect_system_lang() == "zh"  # ctypes.windll 不可用 → except → 回退 LANG


def test_detect_lang_prefers_config_over_env(tmp_path, monkeypatch):
    # 用户配置文件优先于 LANG 环境变量（防止终端 LANG=en_US 但用户在 wizard 选了 zh）。
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("TT_LANG", raising=False)
    from token_tracker import i18n
    config.save_lang("zh")
    assert i18n._detect_lang() == "zh"


def test_run_wizard_saves_theme(tmp_path, monkeypatch):
    # wizard 用 questionary 交互：mock select 直接返回值（避免起 prompt_toolkit 主循环）。
    from token_tracker import i18n, wizard

    class _FakeQ:
        def __init__(self, value):
            self._v = value

        def ask(self):
            return self._v

    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))

    # 顺序：选语言（zh）→ 选主题（nord）→ 增强项 Yes/No（按检测到的 agent 数量）
    # has_cc / has_codex / has_kimi / has_pi 固定，问题数稳定；多余 mock 序列不会被消耗。
    monkeypatch.setattr(wizard, "_has_cc", lambda: True)
    monkeypatch.setattr(wizard, "_has_codex", lambda: True)
    monkeypatch.setattr(wizard, "_has_kimi", lambda: False)
    monkeypatch.setattr(wizard, "_has_pi", lambda: False)
    selects = iter(["zh", "nord", "Yes", "Yes"])
    monkeypatch.setattr("questionary.select", lambda *a, **k: _FakeQ(next(selects)))
    monkeypatch.setattr(wizard, "setup", lambda **kw: None)  # 不真落地配置
    monkeypatch.delenv("TT_THEME", raising=False)
    monkeypatch.setattr(theme, "_ACTIVE_NAME", None)
    wizard.run_wizard()
    assert config.load_config()["theme"] == "nord"
    assert config.load_config()["lang"] == "zh"  # 语言也被保存
    assert i18n.LANG == "zh"  # i18n 即时切换
