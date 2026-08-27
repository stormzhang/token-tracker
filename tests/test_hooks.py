import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib

import pytest

from token_tracker import config, hooks, i18n, sidebar_install


@pytest.fixture(autouse=True)
def _isolate_real_home(tmp_path, monkeypatch):
    """hooks/config 全部路径常量默认指向 tmp——任何用例都不许碰真实 ~/.claude、~/.codex、~/.config。

    教训（2026-07-02）：update_hook 的 codex command sync 因单个用例漏 patch CODEX_CONFIG，
    把 monkeypatch 的假 python 写进了真实 ~/.codex/config.toml；setup() 组件用例也曾把
    codex_faux_statusline=false 写进真实 config.json。默认全隔离后，
    单个用例只需再 patch 自己关心的路径（后设的 monkeypatch 覆盖这里的默认值）。
    """
    tt = tmp_path / "_tt"
    home = tmp_path / "_home"
    monkeypatch.setattr(hooks, "_TT", str(tt))
    monkeypatch.setattr(hooks, "_CLAUDE", str(home / ".claude"))
    monkeypatch.setattr(hooks, "_CODEX", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(home / ".claude" / "settings.json"))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tt / "claude-statusline.py"))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(home / ".codex" / "config.toml"))
    monkeypatch.setattr(hooks, "_KIMI", str(home / ".kimi-code"))
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(home / ".kimi-code" / "config.toml"))
    monkeypatch.setattr(
        sidebar_install, "KIMI_SKILL_DIR", str(home / ".kimi-code" / "skills" / "tt-sidebar")
    )
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tt / "codex-statusline.py"))
    monkeypatch.setattr(hooks, "KIMI_STATUSLINE_HOOK_PATH", str(tt / "kimi-statusline.py"))
    monkeypatch.setattr(hooks, "KIMI_STATUSLINE_STATE_PATH", str(tt / "tt-kimi-statusline.json"))
    monkeypatch.setattr(hooks, "KIMI_STATUSLINE_QUOTA_PATH", str(tt / "tt-kimi-quota.json"))
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(home / ".kimi-code" / "tui.toml"))
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(home / ".codex" / "hooks.json"))
    monkeypatch.setattr(
        sidebar_install, "SIDEBAR_SKILL_DIR", str(home / ".agents" / "skills" / "tt-sidebar")
    )
    monkeypatch.setattr(hooks, "STATUS_FILE", str(tt / "tt-status.json"))
    monkeypatch.setattr(hooks, "TERMINAL_MAP_FILE", str(tt / "tt-terminal-map.json"))
    monkeypatch.setattr(hooks, "CC_BACKUP_PATH", str(tt / "cc-backup.json"))
    monkeypatch.setattr(hooks, "CODEX_BACKUP_LEGACY", str(tt / "codex-backup.json"))
    monkeypatch.setattr(hooks, "_LEGACY_PATHS", [])
    cfg = tmp_path / "_cfg"
    monkeypatch.setattr(config, "CONFIG_DIR", str(cfg))
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg / "config.json"))
    monkeypatch.setattr(config, "STATUS_FILE", str(cfg / "tt-status.json"))
    monkeypatch.setattr(config, "TERMINAL_MAP_FILE", str(cfg / "tt-terminal-map.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(cfg / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(cfg / "lang.json"))


def test_all_path_constants_are_isolated():
    """新增模块级路径常量必须同步加进 autouse 隔离 fixture（本测试是兜底）：
    遍历 hooks / config / sidebar_install 的大写路径常量，凡仍指向真实 home 的即漏 patch。
    教训：KIMI_STATUSLINE_QUOTA_PATH 新增时漏 patch，CI 干净 HOME 上写真实路径失败。"""
    real_home = os.path.expanduser("~")
    offenders: list[str] = []
    for module in (hooks, config, sidebar_install):
        for name in dir(module):
            if not name.isupper():
                continue
            value = getattr(module, name)
            values = value if isinstance(value, (list, tuple)) else (value,)
            for item in values:
                if isinstance(item, str) and item.startswith(real_home):
                    offenders.append(f"{module.__name__}.{name}={item}")
    assert not offenders, "路径常量未纳入 autouse 隔离 fixture：" + "；".join(offenders)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run_statusline(script_path, project_dir):
    data = {
        "workspace": {"project_dir": str(project_dir)},
        "context_window": {"used_percentage": 30, "context_window_size": 200000,
                           "total_input_tokens": 100, "total_output_tokens": 50},
    }
    # HOME 重定向到脚本所在临时目录：脚本会 save_data 到 ~/.config/token-tracker/tt-status.json，
    # 不隔离会把真实文件覆盖成无 session_id 的测试帧（曾把 _tps_state/_terminal_map 反复清空）
    env = dict(os.environ, HOME=os.path.dirname(str(script_path)))
    r = subprocess.run([sys.executable, str(script_path)], input=json.dumps(data),
                       text=True, capture_output=True, env=env)
    return r.stdout.splitlines()[0] if r.stdout.strip() else ""


def test_rendered_hook_script_has_single_version_source():
    # HOOK_VERSION 是唯一版本来源：渲染后脚本里的 __version__ 必须等于它，
    # 且占位符不能残留（否则 needs_update 永远判不相等，每次都重写文件）。
    rendered = hooks._render_hook_script()
    assert f'__version__ = "{hooks.HOOK_VERSION}"' in rendered
    assert "__HOOK_VERSION__" not in rendered


def test_statusline_records_terminal_map(tmp_path):
    # HOOK_VERSION 2.0：statusline 按 session_id 采集 ITERM_SESSION_ID/TMUX_PANE 进
    # _terminal_map（sidebar 点击跳转的映射源），多会话合并不互相清零。
    # HOME 重定向到 tmp，脚本的 STATUS_FILE(~/.config/...) 随之落在临时目录、不碰真实文件。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path),
               ITERM_SESSION_ID="w0t1p0:AAA-111", TMUX_PANE="%7", COLUMNS="120")

    def _run(session_id):
        data = {"session_id": session_id,
                "workspace": {"project_dir": str(tmp_path)},
                "context_window": {"used_percentage": 30, "context_window_size": 200000,
                                   "total_input_tokens": 100, "total_output_tokens": 50}}
        subprocess.run([sys.executable, str(script)], input=json.dumps(data),
                       text=True, capture_output=True, env=env)

    _run("sess-a")
    env["ITERM_SESSION_ID"] = "w0t2p0:BBB-222"
    _run("sess-b")
    status_path = tmp_path / ".config" / "token-tracker" / "tt-status.json"
    tmap = json.loads(status_path.read_text())["_terminal_map"]
    assert tmap["sess-a"] == {"iterm": "w0t1p0:AAA-111", "tmux": "%7"}  # 第二帧未清掉第一帧
    assert tmap["sess-b"] == {"iterm": "w0t2p0:BBB-222", "tmux": "%7"}
    # 回归（2.1）：无 session_id 的异常帧不得清掉共享状态（曾被这类帧反复清表）
    _run("")
    tmap = json.loads(status_path.read_text())["_terminal_map"]
    assert set(tmap) == {"sess-a", "sess-b"}


def test_installed_version_parser_roundtrips(tmp_path, monkeypatch):
    # _installed_hook_version 读回的版本应与写入的 HOOK_VERSION 一致，
    # 保证 needs_update 不会因解析偏差而误判。
    script_path = tmp_path / "tt-statusline.py"
    script_path.write_text(hooks._render_hook_script(), encoding="utf-8")
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(script_path))
    assert hooks._installed_hook_version() == hooks.HOOK_VERSION


def test_statusline_get_width_reads_columns(tmp_path, monkeypatch):
    # PR #20：Claude Code 把 statusLine 子进程的 stdin/stderr 都设成管道 → get_terminal_size(2) 与
    # /dev/tty 都探测不到，只能回落 116。修复：先读 COLUMNS（同 ui/console._forced_width 规则），
    # 有效正整数即用（减 4 边距）；"0"（CC `!` 子进程占位）/ 非数字 / 未设 → 落回原探测链、无回归。
    # 用一个独立命名空间跑模板，避免污染 hooks 模块的执行栈。
    import importlib.util
    script = tmp_path / "cc-sl.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_tt_pr20_cc_sl", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("COLUMNS", "60")
    assert mod.get_width() == 56  # 60 - 4 边距
    monkeypatch.setenv("COLUMNS", "200")
    assert mod.get_width() == 196
    monkeypatch.setenv("COLUMNS", "0")  # CC `!` 子进程占位 → 落回原链
    w0 = mod.get_width()
    assert w0 != 0 and w0 != -4  # 落回 get_terminal_size/dev-tty/116，不会是 0-4=-4
    monkeypatch.setenv("COLUMNS", "abc")  # 非数字 → 落回原链
    assert mod.get_width() == w0
    monkeypatch.delenv("COLUMNS", raising=False)  # 未设 → 落回原链
    assert mod.get_width() == w0


def test_statusline_vlen_counts_cjk_as_two_columns(tmp_path):
    # PR #20：vlen 用 east_asian_width 判 W/F 计 2 列（与 wizard.py:141 同规则）。项目名 / 分支 /
    # 模型名含 CJK 时按字符数少算会让窄面板收窄失准、行折行溢出。
    import importlib.util
    script = tmp_path / "cc-sl.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_tt_pr20_cc_sl_vlen", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.vlen("abc") == 3                    # ASCII 半角 1 列
    assert mod.vlen("测试项目") == 8                # 4 全角 × 2 列
    assert mod.vlen("测试项目-令牌追踪") == 17      # 8 CJK × 2 = 16 + 1 半角连字符 = 17
    assert mod.vlen("\x1b[31mred\x1b[0m") == 3     # ANSI 序列被剥除
    assert mod.vlen("A你B我") == 6                  # ASCII 1 + CJK 2 + ASCII 1 + CJK 2


def test_statusline_script_bakes_theme_colors(monkeypatch):
    # statusline 脚本在烘焙时注入当前主题 truecolor + default 3-bit 兜底；占位符不残留、语法正确。
    monkeypatch.setenv("TT_THEME", "dracula")
    monkeypatch.delenv("COLORFGBG", raising=False)
    rendered = hooks._render_hook_script()
    assert "__STATUSLINE_TRUECOLOR__" not in rendered
    assert "__STATUSLINE_COLOR256__" not in rendered
    assert "38;2;80;250;123" in rendered  # dracula green（truecolor）注入
    assert "38;5;" in rendered  # 256 色兜底注入
    compile(rendered, "<statusline>", "exec")  # 注入后语法正确


def test_codex_statusline_render_injects_version():
    # Codex 伪 statusline 脚本：版本号 + 主题配色注入、占位符不残留、语法正确（无 __TT_PYTHON__ 需求）。
    rendered = hooks._render_codex_statusline_hook()
    assert f'__version__ = "{hooks.STATUSLINE_HOOK_VERSION}"' in rendered
    assert "__STATUSLINE_HOOK_VERSION__" not in rendered
    assert "__STATUSLINE_TRUECOLOR__" not in rendered  # 配色占位符已替换
    assert "'reset'" in rendered and "38;2" in rendered  # 注入了 truecolor 配色 dict（跟随主题）
    compile(rendered, "<codex-statusline>", "exec")


def test_codex_statusline_records_terminal_map_without_touching_cc_status(tmp_path):
    # Codex Stop hook 从精确 transcript 读 session_meta.id，采集当前终端环境并按 session 合并；
    # 单独落 tt-terminal-map.json，不能覆盖 CC 心跳/rate limit 使用的 tt-status.json。
    script = tmp_path / "codex-statusline.py"
    script.write_text(hooks._render_codex_statusline_hook(), encoding="utf-8")
    cfg = tmp_path / ".config" / "token-tracker"
    cfg.mkdir(parents=True)
    status_path = cfg / "tt-status.json"
    status_path.write_text(json.dumps({"session_id": "claude-live", "rate_limits": {"five_hour": 12}}),
                           encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path), ITERM_SESSION_ID="w0t1p0:AAA-111", TMUX_PANE="%7")

    def _run(session_id):
        rollout = tmp_path / f"{session_id or 'missing'}.jsonl"
        payload = {"cwd": str(tmp_path)}
        if session_id:
            rollout.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": str(tmp_path)},
            }) + "\n", encoding="utf-8")
            payload["transcript_path"] = str(rollout)
        subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, check=True)

    _run("codex-a")
    env["ITERM_SESSION_ID"] = "w0t2p0:BBB-222"
    _run("codex-b")
    fallback = tmp_path / ".codex" / "sessions" / "fallback.jsonl"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": "wrong-fallback", "cwd": str(tmp_path)}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {}}}},
    ]) + "\n", encoding="utf-8")
    _run("")  # 无精确 ID 时最近文件只供显示，不得把当前窗格错绑给 fallback 会话，也不得清表

    term_map = json.loads((cfg / "tt-terminal-map.json").read_text())["_terminal_map"]
    assert term_map["codex-a"] == {"iterm": "w0t1p0:AAA-111", "tmux": "%7"}
    assert term_map["codex-b"] == {"iterm": "w0t2p0:BBB-222", "tmux": "%7"}
    assert "wrong-fallback" not in term_map
    assert json.loads(status_path.read_text()) == {
        "session_id": "claude-live", "rate_limits": {"five_hour": 12},
    }


def test_codex_statusline_config_migration_preserves_state_and_user_stop(tmp_path, monkeypatch):
    # 只迁移 Token Tracker 的旧内联 Stop；[hooks.state] 信任记录与用户 Stop 一字不动。
    import tomllib

    script = tmp_path / "codex-statusline.py"
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    script.write_text("managed", encoding="utf-8")
    terminal_map = tmp_path / "tt-terminal-map.json"
    terminal_map.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(hooks, "TERMINAL_MAP_FILE", str(terminal_map))
    content = (
        '[tui]\nstatus_line = ["project"]\n\n'
        "[hooks.state]\n"
        'enabled = true\n\n'
        '[hooks.state."config.toml:stop:0:0"]\n'
        'trusted_hash = "sha256:keep-me"\n\n'
        '[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "/usr/bin/my-other-stop"\ntimeout = 3\n\n'
        '[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\n'
        f"command = 'python3 {script}'\n"
        "timeout = 10\n"
    )

    migrated = hooks._migrate_codex_statusline_config(content)
    assert str(script) not in migrated
    assert "/usr/bin/my-other-stop" in migrated
    assert '[hooks.state."config.toml:stop:0:0"]' in migrated
    assert 'trusted_hash = "sha256:keep-me"' in migrated
    parsed = tomllib.loads(migrated)
    assert parsed["hooks"]["state"]["config.toml:stop:0:0"]["trusted_hash"] == "sha256:keep-me"
    assert parsed["hooks"]["Stop"][0]["hooks"][0]["command"] == "/usr/bin/my-other-stop"

    removed = hooks._uninstall_codex_statusline(content)
    assert removed == migrated
    assert not script.exists()
    assert not terminal_map.exists()


def test_codex_hooks_update_migrates_inline_and_refreshes_both_commands(tmp_path, monkeypatch):
    # 老用户升级：旧 config.toml Stop + 指向项目 .venv 的 UserPromptSubmit 一次迁到 hooks.json，
    # 两个 handler 都改用当前安装版解释器；[hooks.state] 保留。
    codex_config = tmp_path / "config.toml"
    script = tmp_path / "codex-statusline.py"
    codex_config.write_text(
        "[hooks.state]\n"
        'enabled = true\n\n'
        '[hooks.state."config.toml:stop:0:0"]\n'
        'trusted_hash = "sha256:keep-me"\n\n'
        '[[hooks.Stop]]\n\n'
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {script}'\n"
        "timeout = 10\n",
        encoding="utf-8",
    )
    codex_hooks = tmp_path / "hooks.json"
    codex_hooks.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [{
                "hooks": [{
                    "type": "command",
                    "command": (
                        '"/project/.venv/bin/python" -B -m '
                        "token_tracker.sidebar_command prompt-hook --agent codex"
                    ),
                    "timeout": 2,
                }],
            }],
        },
    }), encoding="utf-8")
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(codex_dir))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tmp_path / "claude-statusline.py"))  # CC 未装
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(codex_hooks))
    monkeypatch.setattr(sidebar_install, "SIDEBAR_SKILL_DIR", str(tmp_path / "tt-sidebar"))
    monkeypatch.setattr(hooks.sys, "executable", "/new/python3")
    monkeypatch.setattr(hooks.os, "name", "posix")
    config.save_codex_faux_statusline(True)
    config.save_setup_version(config.SETUP_VERSION)
    script.write_text(hooks._render_codex_statusline_hook(), encoding="utf-8")

    assert hooks.needs_update()
    hooks.update_hook()
    content = codex_config.read_text(encoding="utf-8")
    assert "[[hooks.Stop]]" not in content
    assert 'trusted_hash = "sha256:keep-me"' in content
    installed = json.loads(codex_hooks.read_text(encoding="utf-8"))["hooks"]
    assert installed["Stop"][0]["hooks"][0]["command"] == f'"/new/python3" "{script}"'
    assert installed["UserPromptSubmit"][0]["hooks"][0]["command"] == (
        '"/new/python3" -B -m token_tracker.sidebar_command prompt-hook --agent codex'
    )
    assert not hooks.needs_update()


def test_codex_hooks_corrupt_json_keeps_inline_stop(tmp_path, monkeypatch, capsys):
    # 先写 hooks.json、成功后才删 config.toml：JSON 损坏时必须保留旧 Stop，避免迁移失败让功能消失。
    script = tmp_path / "codex-statusline.py"
    codex_config = tmp_path / "config.toml"
    original = (
        "[hooks.state]\n"
        'enabled = true\n\n'
        '[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\n'
        f"command = 'python3 {script}'\n"
        "timeout = 10\n"
    )
    codex_config.write_text(original, encoding="utf-8")
    codex_hooks = tmp_path / "hooks.json"
    codex_hooks.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(codex_hooks))
    config.save_codex_faux_statusline(True)

    assert not hooks._sync_codex_managed_hooks(quiet=True)
    assert codex_config.read_text(encoding="utf-8") == original
    assert codex_hooks.read_text(encoding="utf-8") == "{broken"
    assert "hooks.json" in capsys.readouterr().out


def test_codex_statusline_windows_path_is_json_safe(tmp_path, monkeypatch):
    # hooks.json 取代内联 TOML 后，Windows 路径仍转正斜杠并用双引号包裹，含空格也不会断词。
    codex_hooks = tmp_path / "hooks.json"
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(codex_hooks))
    monkeypatch.setattr(
        hooks,
        "CODEX_STATUSLINE_HOOK_PATH",
        r"C:\Users\test\.config\token-tracker\codex-statusline.py",
    )
    monkeypatch.setattr(hooks.os, "name", "nt")
    command = hooks._codex_statusline_command(r"C:\Program Files\Python313\python.exe")
    assert command == (
        '"C:/Program Files/Python313/python.exe" '
        '"C:/Users/test/.config/token-tracker/codex-statusline.py"'
    )
    assert sidebar_install.install_managed_hooks(command)
    parsed = json.loads(codex_hooks.read_text(encoding="utf-8"))
    assert parsed["hooks"]["Stop"][0]["hooks"][0]["command"] == command


def test_codex_statusline_home_command_is_portable_and_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    executable = home / ".local" / "bin" / "python"
    script = home / ".config" / "token-tracker" / "codex-statusline.py"
    codex_hooks = tmp_path / "hooks.json"
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    monkeypatch.setattr(hooks.sys, "executable", str(executable))
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(codex_hooks))
    monkeypatch.setattr(sidebar_install.os.path, "expanduser", lambda value: str(home) if value == "~" else value)

    command = hooks._codex_statusline_command()
    assert command == '"$HOME/.local/bin/python" "$HOME/.config/token-tracker/codex-statusline.py"'
    assert sidebar_install.install_managed_hooks(command)
    assert not sidebar_install.managed_hooks_need_sync(command)
    assert not sidebar_install.install_managed_hooks(command)


def test_codex_statusline_external_path_stays_absolute(tmp_path, monkeypatch):
    home = tmp_path / "home"
    executable = home / ".local" / "bin" / "python"
    script = tmp_path / "external" / "codex-statusline.py"
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    monkeypatch.setattr(hooks.sys, "executable", str(executable))
    monkeypatch.setattr(sidebar_install.os.path, "expanduser", lambda value: str(home) if value == "~" else value)

    assert hooks._codex_statusline_command() == f'"{executable}" "{script}"'


def test_codex_statusline_version_roundtrip(tmp_path, monkeypatch):
    # _installed_codex_statusline_version 读回的版本应与写入的 STATUSLINE_HOOK_VERSION 一致，
    # 保证 needs_update 不会因解析偏差而误判。
    script_path = tmp_path / "tt-statusline.py"
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script_path))
    assert hooks._installed_codex_statusline_version() is None  # 未装
    hooks._write_codex_statusline_script()
    assert hooks._installed_codex_statusline_version() == hooks.STATUSLINE_HOOK_VERSION


def test_setup_components_defaults_all_on():
    # SetupComponents 默认值全开（setup(components=None) 走 recommended_components 智能默认，另测）。
    c = hooks.SetupComponents()
    assert c.cc_statusline is True
    assert c.codex_faux_statusline is True
    assert hooks.SetupComponents.all_on() == c


def test_setup_components_off_skips_install(tmp_path, monkeypatch):
    # codex_faux_statusline=False → Codex 伪 statusline 不装。
    # 隔离 HOME，避免污染主人真实 ~/.claude / ~/.codex
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    codex_config = home / ".codex" / "config.toml"
    codex_config.write_text("[tui]\nstatus_line = []\n", encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(home / ".claude" / "tt-statusline.py"))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(home / ".codex" / "tt-statusline.py"))
    _isolate_config(monkeypatch, tmp_path / "cfg")  # setup 现在写 intent + setup_version，必须隔离

    hooks.setup(components=hooks.SetupComponents(codex_faux_statusline=False))

    # CC statusline 仍装（command 现在带引号包裹，issue #13 修复）
    assert json.loads(settings_path.read_text())["statusLine"]["command"].endswith('tt-statusline.py"')
    # Codex 端：不再动 [tui].status_line（保持用户原配置）；Stop hook（tt-statusline）也不在 config 里
    codex_content = codex_config.read_text()
    assert "status_line = []" in codex_content  # 用户原 status_line 没被动
    assert "tt-statusline" not in codex_content   # Codex 伪 statusline hook 段未追加
    # 意图落盘：CC True / Codex False
    from token_tracker import config
    assert config.cc_statusline_intent() is True
    assert config.codex_faux_statusline_intent() is False


def test_setup_claude_corrupt_settings_no_crash_no_clobber(tmp_path, monkeypatch, capsys):
    # 回归：settings.json 损坏时 is_setup()=False → 任意命令进 setup 流程 → 旧代码裸 json.load 直接崩栈。
    # 新行为：报错跳过 CC 端、原文件一字不动（可能是用户手改打错，不能静默覆盖）。
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"statusLine": broken', encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tmp_path / "claude-statusline.py"))

    hooks._setup_claude(hooks.SetupComponents(), quiet=True)  # 不抛异常
    assert settings_path.read_text(encoding="utf-8") == '{"statusLine": broken'  # 原样保留
    assert not (tmp_path / "claude-statusline.py").exists()  # 早退，未落任何文件
    assert "settings.json" in capsys.readouterr().out  # quiet 也要出声（错误不可静默）


def test_unsetup_claude_corrupt_settings_no_crash(tmp_path, monkeypatch, capsys):
    # unsetup 遇损坏 settings.json：不崩、不动文件、提示手动检查。
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tmp_path / "claude-statusline.py"))
    monkeypatch.setattr(hooks, "_migrate_legacy", lambda: None)

    hooks._unsetup_claude()  # 不抛异常
    assert settings_path.read_text(encoding="utf-8") == "not json at all"
    assert "settings.json" in capsys.readouterr().out


def test_unsetup_claude_corrupt_backup_removes_statusline(tmp_path, monkeypatch):
    # 备份文件损坏：不崩，statusLine 走移除分支，损坏备份保留在磁盘供手动抢救。
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "statusLine": {"type": "command", "command": '"/py" "/x/claude-statusline.py"'},
        "keep": 1,
    }), encoding="utf-8")
    backup_path = tmp_path / "cc-backup.json"
    backup_path.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "CC_BACKUP_PATH", str(backup_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tmp_path / "claude-statusline.py"))
    monkeypatch.setattr(hooks, "STATUS_FILE", str(tmp_path / "tt-status.json"))
    monkeypatch.setattr(hooks, "_migrate_legacy", lambda: None)

    hooks._unsetup_claude()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "statusLine" not in settings  # 恢复不了 → 移除
    assert settings["keep"] == 1
    assert backup_path.exists()  # 损坏备份保留


def test_cli_setup_wizard_or_auto(monkeypatch):
    # `tt setup` 经 _run_setup_flow：装了 agent 时，双 tty 非会话内 → run_wizard；否则 → _auto_setup。
    from token_tracker import cli, wizard
    calls: dict = {}
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "detect_agents",
                        lambda: [SimpleNamespace(name="Claude Code", id="claude-code")])  # 有 agent
    monkeypatch.setattr(wizard, "run_wizard", lambda: calls.__setitem__("wizard", True))
    monkeypatch.setattr(cli, "_auto_setup", lambda: calls.__setitem__("auto", True))
    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr("sys.argv", ["tt", "setup"])

    monkeypatch.setattr(cli, "_should_run_wizard", lambda: True)
    cli.main()
    assert calls == {"wizard": True}

    calls.clear()
    monkeypatch.setattr(cli, "_should_run_wizard", lambda: False)
    cli.main()
    assert calls == {"auto": True}


def test_cli_setup_flow_no_agent(monkeypatch):
    # _run_setup_flow 是 agent 守卫单一入口：零 agent → 提示 no_agent_install，不进 wizard / auto。
    from token_tracker import cli, wizard
    calls: dict = {}
    monkeypatch.setattr(cli, "detect_agents", lambda: [])  # 没装 agent
    monkeypatch.setattr(wizard, "run_wizard", lambda: calls.__setitem__("wizard", True))
    monkeypatch.setattr(cli, "_auto_setup", lambda: calls.__setitem__("auto", True))
    monkeypatch.setattr(cli, "is_setup", lambda: False)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr("sys.argv", ["tt", "setup"])
    cli.main()
    assert calls == {}  # 既没进 wizard 也没 auto


def test_codex_statusline_uninstall_keeps_other_stop_hooks(tmp_path, monkeypatch):
    # 卸载只移除 tt 旧版追加的内联 [[hooks.Stop]]，用户已有的 Stop hook 不动。
    script = tmp_path / "tt-statusline.py"
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    user_stop = (
        '\n[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "/usr/bin/my-other-stop"\ntimeout = 3\n'
    )
    tt_stop = (
        '\n[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\n'
        f"command = 'python3 {script}'\ntimeout = 10\n"
    )
    removed = hooks._uninstall_codex_statusline(
        '[tui]\nstatus_line = ["project"]\n' + user_stop + tt_stop
    )
    assert "tt-statusline" not in removed
    assert "/usr/bin/my-other-stop" in removed  # 用户的 Stop 完整保留


@pytest.mark.skipif(not shutil.which("git"), reason="需要 git")
def test_statusline_shows_git_diff_stat(tmp_path):
    # statusline 第一行在分支括号内显示相对 HEAD 的未提交增删（+N 绿 / -N 红），0 改动则隐藏。
    script_path = tmp_path / "tt-statusline.py"
    script_path.write_text(hooks._render_hook_script(), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1\n2\n3\n")
    (repo / "b.txt").write_text("1\n2\n3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    # 干净工作区 → 括号里只有分支、无 +/-
    line_clean = _run_statusline(script_path, repo)
    assert "[repo]" in line_clean
    assert "+" not in line_clean and "-" not in line_clean

    # a.txt 追加 2 行（+2）、b.txt 删 1 行（-1），未暂存 → 相对 HEAD 共 +2 -1
    (repo / "a.txt").write_text("1\n2\n3\n4\n5\n")
    (repo / "b.txt").write_text("1\n2\n")
    line_dirty = _run_statusline(script_path, repo)
    assert "+2" in line_dirty and "-1" in line_dirty

    # 再造 2 个未跟踪文件 → 应额外显示 ?2（按文件数计、不读行数）
    (repo / "new1.txt").write_text("x\n")
    (repo / "new2.txt").write_text("x\ny\n")
    line_with_untracked = _run_statusline(script_path, repo)
    assert "?2" in line_with_untracked


def _run_statusline_home(script_path, payload, home):
    """隔离 HOME 下跑落盘 statusline 脚本，返回完整 stdout（不污染真实 ~/.claude）。"""
    env = {**os.environ, "HOME": str(home), "COLORTERM": "truecolor"}
    r = subprocess.run([sys.executable, str(script_path)], input=json.dumps(payload),
                       text=True, capture_output=True, env=env)
    return r.stdout


def test_statusline_line4_tps_code_repo(tmp_path):
    # Line 4：本轮 TPS（api_duration 差分）+ Code 行数 + Repo host。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    base = {
        "session_id": "S1",
        "workspace": {"project_dir": str(tmp_path), "repo": {"host": "github.com"}},
        "context_window": {"current_usage": {"output_tokens": 10}},
        "cost": {"total_api_duration_ms": 1000, "total_lines_added": 208, "total_lines_removed": 8},
    }
    _run_statusline_home(script, base, home)  # 第一帧：写 tt-status.json 建立 prev
    frame2 = {
        "session_id": "S1",
        "workspace": {"project_dir": str(tmp_path), "repo": {"host": "github.com"}},
        "context_window": {"current_usage": {"output_tokens": 200}},
        "cost": {"total_api_duration_ms": 2000, "total_lines_added": 208, "total_lines_removed": 8},
    }
    out = _run_statusline_home(script, frame2, home)  # 同会话 Δ1000ms / output 200 → TPS 200
    assert "TPS: 200 tokens/s" in out  # 带单位
    assert "Code" in out and "+208" in out and "-8" in out
    assert "Remote: github" in out and "github.com" not in out  # .com 被去除
    # 第三帧空闲（Δ=0、output 小）→ 沿用上次 200，不回落到 -
    frame3 = {**frame2, "context_window": {"current_usage": {"output_tokens": 2}}}
    out3 = _run_statusline_home(script, frame3, home)
    assert "TPS: 200 tokens/s" in out3


def test_statusline_total_tokens(tmp_path):
    # Total：从 transcript 解析会话累计 in+out+cache（去重、跳非 assistant），第 1 行显示总和。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    tx = tmp_path / "tx.jsonl"
    rows = [
        {"type": "assistant", "requestId": "r1", "message": {"id": "m1", "usage": {
            "input_tokens": 100, "output_tokens": 2000,
            "cache_creation_input_tokens": 500, "cache_read_input_tokens": 3000}}},
        {"type": "assistant", "requestId": "r1", "message": {"id": "m1", "usage": {  # 重复 → 去重
            "input_tokens": 100, "output_tokens": 2000,
            "cache_creation_input_tokens": 500, "cache_read_input_tokens": 3000}}},
        {"type": "assistant", "requestId": "r2", "message": {"id": "m2", "usage": {
            "input_tokens": 50, "output_tokens": 1000,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000}}},
        {"type": "user", "message": {}},  # 非 assistant 跳过
    ]
    tx.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    payload = {"session_id": "S1", "transcript_path": str(tx),
               "workspace": {"project_dir": str(tmp_path)},
               "context_window": {"current_usage": {"output_tokens": 1}},
               "cost": {"total_api_duration_ms": 1000}}
    out = _run_statusline_home(script, payload, home)
    # in=150, out=3000, cache=(500+3000)+(0+4000)=7500；Total=in+out+cache=10650→11k
    assert "Total: 11k" in out
    assert "Cache" not in out  # Cache 单列已删除


def test_statusline_line3_tps_hidden_when_no_prior_value(tmp_path):
    # 从未有过有效值时（output 一直太小）→ TPS 项隐藏（不再显示 "-"）；L3 无其它数据时整行不出现。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    base = {"session_id": "S1", "workspace": {"project_dir": str(tmp_path)},
            "context_window": {"current_usage": {"output_tokens": 2}},
            "cost": {"total_api_duration_ms": 5000}}
    _run_statusline_home(script, base, home)
    out = _run_statusline_home(script, base, home)  # Δ=0、output=2、无历史值 → 不显示 TPS 项
    assert "TPS" not in out


def test_statusline_tps_keeps_last_value_when_zero(tmp_path):
    # 算出会显示成 0 的（output 小 / Δ 很大）→ 不刷新，保持上次有效值。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    def frame(api, out):
        return {"session_id": "S1", "workspace": {"project_dir": str(tmp_path)},
                "context_window": {"current_usage": {"output_tokens": out}},
                "cost": {"total_api_duration_ms": api}}

    _run_statusline_home(script, frame(10000, 5), home)            # 建 prev_api
    out2 = _run_statusline_home(script, frame(11000, 200), home)   # Δ1000ms / out200 → tps 200
    assert "TPS: 200 tokens/s" in out2
    # Δ 很大(100s) + output 小(20) → tps≈0.2 → round 0 → 不刷新、沿用 200
    out3 = _run_statusline_home(script, frame(111000, 20), home)
    assert "TPS: 200 tokens/s" in out3
    assert "TPS: 0 tokens/s" not in out3


def test_statusline_tps_isolated_per_session(tmp_path):
    # 多会话共享 tt-status.json：TPS 差分按 session_id 隔离，别的会话覆盖文件也不把本会话清成 "-"。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    def frame(sid, api, out):
        return {"session_id": sid, "workspace": {"project_dir": str(tmp_path)},
                "context_window": {"current_usage": {"output_tokens": out}},
                "cost": {"total_api_duration_ms": api}}

    _run_statusline_home(script, frame("A", 1000, 5), home)            # A 建 prev_api
    _run_statusline_home(script, frame("B", 500000, 5), home)          # B 覆盖文件、建自己 prev
    out_a = _run_statusline_home(script, frame("A", 2000, 200), home)  # A：Δ1000 / out200 → 200
    assert "TPS: 200 tokens/s" in out_a                                # 没被 B 的覆盖清成 "-"
    _run_statusline_home(script, frame("B", 502000, 5), home)          # 夹一帧 B
    out_b = _run_statusline_home(script, frame("B", 504000, 300), home)  # B：Δ2000 / out300 → 150
    assert "TPS: 150 tokens/s" in out_b


def test_statusline_progress_bar_empty_grid_tinted(tmp_path):
    # 进度条未填充网格按当前档位色着色；pct=0 时保持灰（裸 ░ 紧跟 reset、不着色）。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    def bar_out(pct):
        payload = {"session_id": "S1", "workspace": {"project_dir": str(tmp_path)},
                   "rate_limits": {"five_hour": {"used_percentage": pct}}}
        return _run_statusline_home(script, payload, home)

    esc = re.compile(r"\x1b\[[0-9;]*m")

    out0 = bar_out(0)
    assert "░" in out0 and esc.findall(out0)[-1] + "░" in out0      # pct=0：灰格紧跟 reset、未着色

    out60 = bar_out(60)
    assert "░" in out60 and esc.findall(out60)[-1] + "░" not in out60  # pct>0：未填充格被染色、不在 reset 后


def test_setup_codex_uses_hooks_json_without_creating_config(tmp_path, monkeypatch):
    # 装了 Codex 但还没 config.toml → 两个 Hook 都写 hooks.json，不为 Hook 新建空 config.toml。
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)  # 只有目录、无 config.toml
    codex_config = home / ".codex" / "config.toml"
    codex_hooks = home / ".codex" / "hooks.json"
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    tt_dir = home / ".config" / "token-tracker"
    monkeypatch.setattr(hooks, "_TT", str(tt_dir))
    monkeypatch.setattr(
        hooks,
        "CODEX_STATUSLINE_HOOK_PATH",
        str(tt_dir / "codex-statusline.py"),
    )
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(codex_hooks))
    monkeypatch.setattr(hooks.config, "CONFIG_PATH", str(tmp_path / "tt-config.json"))  # 隔离 config.json

    assert not codex_config.exists()
    hooks._setup_codex(hooks.SetupComponents(), quiet=True)
    assert not codex_config.exists()
    installed = json.loads(codex_hooks.read_text(encoding="utf-8"))["hooks"]
    assert set(installed) == {"Stop", "UserPromptSubmit"}
    assert "codex-statusline.py" in installed["Stop"][0]["hooks"][0]["command"]


def test_detect_system_lang_non_darwin_falls_back_to_env(monkeypatch):
    # 非 macOS（或 darwin 检测失败）回退环境变量：LANG=zh → zh，否则 en。
    from token_tracker import i18n
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    assert i18n._detect_system_lang() == "zh"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n._detect_system_lang() == "en"


# --- SETUP_VERSION 引导版本（老用户升级后重新引导） ---


def _isolate_config(monkeypatch, tmp_path):
    """把 config.py 的所有路径常量切到 tmp_path，避免污染主人真实 ~/.config/token-tracker。"""
    from token_tracker import config
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(tmp_path / "lang.json"))


def test_setup_writes_setup_version(tmp_path, monkeypatch):
    # setup() 真正落地后必须写入 setup_version=当前 SETUP_VERSION——
    # 这是引导机制收口：所有路径（新用户 / wizard / _auto_setup / 手动 tt setup）都经此。
    from token_tracker import config
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    codex_config = home / ".codex" / "config.toml"
    codex_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(home / ".claude" / "tt-statusline.py"))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(home / ".codex" / "tt-statusline.py"))
    _isolate_config(monkeypatch, tmp_path / "cfg")

    assert config.setup_version() == 0  # 老用户初始 0
    hooks.setup(quiet=True)
    assert config.setup_version() == config.SETUP_VERSION  # setup 完成后被打上当前版本


def test_cli_outdated_setup_triggers_setup_flow(monkeypatch, tmp_path):
    # 老用户 is_setup=True 且 setup_version < SETUP_VERSION → 自动走 _run_setup_flow
    # （内部分流真终端 wizard / 会话内 _auto_setup，这里只验触发、不管分流）。
    from token_tracker import cli, config
    _isolate_config(monkeypatch, tmp_path)
    calls: dict = {}

    def fake_flow():
        calls["flow"] = True
        raise SystemExit(0)  # 短路 cli.main 后续数据命令逻辑

    monkeypatch.setattr(cli, "_run_setup_flow", fake_flow)
    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    # setup_version 字段缺失 → 读出 0 < SETUP_VERSION
    monkeypatch.setattr(config, "SETUP_VERSION", 2)
    monkeypatch.setattr("sys.argv", ["tt", "status"])

    with pytest.raises(SystemExit):
        cli.main()
    assert calls == {"flow": True}


def test_cli_setup_up_to_date_skips_flow(monkeypatch, tmp_path):
    # setup_version 已是当前 → 不触发 _run_setup_flow，正常往下跑。
    from token_tracker import cli, config
    _isolate_config(monkeypatch, tmp_path)
    calls: dict = {}

    monkeypatch.setattr(cli, "_run_setup_flow", lambda: calls.__setitem__("flow", True))
    monkeypatch.setattr(cli, "is_setup", lambda: True)
    monkeypatch.setattr(cli, "needs_update", lambda: False)
    monkeypatch.setattr(cli, "_build_status_data", lambda _agents: {})
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "detect_agents",
                        lambda: [SimpleNamespace(name="Claude Code", id="claude-code")])
    config.save_setup_version(config.SETUP_VERSION)  # 已是最新
    monkeypatch.setattr("sys.argv", ["tt", "status"])

    cli.main()
    assert calls == {}


def test_build_cc_command_windows_quotes_and_slashes(monkeypatch):
    # issue #13/#14：Windows 上 statusLine command 必须正斜杠 + 引号包裹，
    # 否则 CC 走 Git Bash 执行时反斜杠被吞，状态栏静默空白。
    monkeypatch.setattr(hooks.os, "name", "nt")
    cmd = hooks._build_cc_command(
        r"C:\Users\X\pipx\venvs\token-tracker\Scripts\python.exe",
        r"C:\Users\X\.config\token-tracker\claude-statusline.py",
    )
    assert cmd == '"C:/Users/X/pipx/venvs/token-tracker/Scripts/python.exe" "C:/Users/X/.config/token-tracker/claude-statusline.py"'
    assert "\\" not in cmd  # 反斜杠全转完
    assert cmd.count('"') == 4  # 两段路径各包一对引号


def test_build_cc_command_unix_always_quoted(monkeypatch):
    # Unix 平台不转换路径分隔符，但始终加引号（防路径含空格断词）。
    monkeypatch.setattr(hooks.os, "name", "posix")
    cmd = hooks._build_cc_command(
        "/Users/John Doe/.local/share/uv/tools/token-tracker/bin/python3",
        "/Users/John Doe/.config/token-tracker/claude-statusline.py",
    )
    assert cmd.startswith('"') and cmd.count('"') == 4
    assert "John Doe" in cmd  # 含空格路径被引号包住、能正确执行


def test_cc_command_outdated_detects_legacy_format(monkeypatch):
    # 旧格式（裸拼接、无引号）应被检测为过时；新格式不动。
    monkeypatch.setattr(hooks.os, "name", "posix")
    assert hooks._cc_command_outdated("/usr/bin/python3 /home/u/.config/token-tracker/claude-statusline.py")
    assert not hooks._cc_command_outdated('"/usr/bin/python3" "/home/u/.config/token-tracker/claude-statusline.py"')
    # Windows 上即便有引号，含反斜杠也算过时
    monkeypatch.setattr(hooks.os, "name", "nt")
    assert hooks._cc_command_outdated(r'"C:\Users\X\python.exe" "C:\Users\X\claude-statusline.py"')
    assert not hooks._cc_command_outdated('"C:/Users/X/python.exe" "C:/Users/X/claude-statusline.py"')
    # 空命令 / 非 tt 命令交给上层 _is_tt_cc_command 过滤；这里仅断言空串返回 False
    assert not hooks._cc_command_outdated("")


def test_update_hook_rewrites_outdated_cc_command(tmp_path, monkeypatch):
    # 老用户场景：HOOK_SCRIPT_PATH 存在 + settings.json 里 command 是旧格式 →
    # 跑任意 tt 命令触发 update_hook 自动重写为新格式（用户其它字段不动）。
    settings_file = tmp_path / "settings.json"
    script_file = tmp_path / "claude-statusline.py"
    script_file.write_text(hooks._render_hook_script(), encoding="utf-8")
    settings_file.write_text(json.dumps({
        "statusLine": {"type": "command",
                       "command": "/old/python3 /old/path/claude-statusline.py"},
        "userField": "keep me",
    }), encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_file))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(script_file))
    monkeypatch.setattr(hooks.sys, "executable", "/new/python3")
    monkeypatch.setattr(hooks.os, "name", "posix")

    assert hooks._cc_command_needs_sync()  # 检测到过时
    hooks.update_hook()
    new_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert new_settings["statusLine"]["command"].startswith('"/new/python3"')
    assert new_settings["userField"] == "keep me"  # 用户其它字段保留
    assert not hooks._cc_command_needs_sync()  # 重写后不再触发


def test_cc_command_sync_skips_non_tt_command(tmp_path, monkeypatch):
    # 用户自己的 statusLine（非 tt）即便没引号也不动——只管 tt 自己装的。
    settings_file = tmp_path / "settings.json"
    user_cmd = "/usr/bin/my-own-statusline --foo"
    settings_file.write_text(json.dumps({
        "statusLine": {"type": "command", "command": user_cmd},
    }), encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_file))
    assert not hooks._cc_command_needs_sync()
    hooks._sync_cc_command()  # no-op
    assert json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"]["command"] == user_cmd


# --- CC statusLine 可选组件（issue #16/#17：自定义 statusLine 与 tt 报表共存） ---


def _cc_only_home(tmp_path, monkeypatch, settings_text=None):
    """CC-only 隔离环境：settings / 脚本 / 备份 / 缓存全指向 tmp，Codex 目录不存在，config 隔离。"""
    cc_dir = tmp_path / "home" / ".claude"
    cc_dir.mkdir(parents=True)
    settings_path = cc_dir / "settings.json"
    if settings_text is not None:
        settings_path.write_text(settings_text, encoding="utf-8")
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(settings_path))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(cc_dir / "claude-statusline.py"))
    monkeypatch.setattr(hooks, "CC_BACKUP_PATH", str(cc_dir / "cc-backup.json"))
    monkeypatch.setattr(hooks, "STATUS_FILE", str(cc_dir / "tt-status.json"))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(tmp_path / "no-codex"))
    monkeypatch.setattr(hooks, "_LEGACY_PATHS", [])
    _isolate_config(monkeypatch, tmp_path / "cfg")
    return settings_path


_TT_SL = {"statusLine": {"type": "command", "command": '"/usr/bin/python3" "/x/claude-statusline.py"'}}
_CUSTOM_SL = {"statusLine": {"type": "command", "command": "/usr/bin/my-own-statusline --foo"}}


def test_config_cc_statusline_intent_roundtrip(tmp_path, monkeypatch):
    # intent 严格 bool：True/False 读回一致；缺字段 / 被手改成非 bool → None（视为没表达）。
    from token_tracker import config
    _isolate_config(monkeypatch, tmp_path)
    assert config.cc_statusline_intent() is None
    config.save_cc_statusline(True)
    assert config.cc_statusline_intent() is True
    config.save_cc_statusline(False)
    assert config.cc_statusline_intent() is False
    config._save_field("cc_statusline", "yes")
    assert config.cc_statusline_intent() is None


def test_cc_statusline_active_double_factor(tmp_path, monkeypatch):
    # 双因素：intent True AND 脚本存在 AND settings 的 command 是 tt 的；任一不满足 → False。
    from token_tracker import config
    settings_path = _cc_only_home(tmp_path, monkeypatch, json.dumps(_TT_SL))
    script = tmp_path / "home" / ".claude" / "claude-statusline.py"
    script.write_text("x", encoding="utf-8")

    assert hooks.cc_statusline_active() is False  # intent None
    config.save_cc_statusline(False)
    assert hooks.cc_statusline_active() is False  # intent False
    config.save_cc_statusline(True)
    assert hooks.cc_statusline_active() is True   # intent True + 实装好

    settings_path.write_text(json.dumps(_CUSTOM_SL), encoding="utf-8")
    assert hooks.cc_statusline_active() is False  # command 被改走
    settings_path.write_text("not json{{{", encoding="utf-8")
    assert hooks.cc_statusline_active() is False  # settings 损坏
    settings_path.write_text(json.dumps(_TT_SL), encoding="utf-8")
    script.unlink()
    assert hooks.cc_statusline_active() is False  # 脚本缺失


def test_is_setup_cc_intent_three_states(tmp_path, monkeypatch):
    # is_setup CC 分支三态：intent None（非存量 tt）→ 未配；False → 放行（不强求文件）；True → 要求实装。
    from token_tracker import config
    settings_path = _cc_only_home(tmp_path, monkeypatch, json.dumps(_CUSTOM_SL))

    assert hooks.is_setup() is False  # intent None + 自定义 statusLine → 触发引导（推荐默认会 opt-out）
    config.save_cc_statusline(False)
    assert hooks.is_setup() is True   # 自定义 statusLine 用户 opt-out 后放行
    config.save_cc_statusline(True)
    assert hooks.is_setup() is False  # intent True 但没实装（command 非 tt）
    (tmp_path / "home" / ".claude" / "claude-statusline.py").write_text("x", encoding="utf-8")
    settings_path.write_text(json.dumps(_TT_SL), encoding="utf-8")
    assert hooks.is_setup() is True   # intent True + 实装好


def test_is_setup_legacy_tt_user_without_intent(tmp_path, monkeypatch):
    # 不 bump SETUP_VERSION 的配套推断：存量用户（statusLine 已是 tt 的、config 无 cc_statusline 字段）
    # 升级后视为已配——不弹向导、不触发 setup、不被打扰；想改的手动 tt setup。
    from token_tracker import config
    _cc_only_home(tmp_path, monkeypatch, json.dumps(_TT_SL))

    assert config.cc_statusline_intent() is None  # 存量用户没有 intent 字段
    assert hooks.is_setup() is True               # 但 statusLine 已是 tt 的 → 推断已配


def test_recommended_components_cc_probe(tmp_path, monkeypatch):
    # 推荐默认三层：探测自定义 statusLine（do-no-harm，优先于 intent）> 已记录 intent > True。
    from token_tracker import config
    settings_path = _cc_only_home(tmp_path, monkeypatch)  # settings.json 不存在

    assert hooks.recommended_components().cc_statusline is True   # 全新用户 → 接管
    settings_path.write_text(json.dumps(_TT_SL), encoding="utf-8")
    assert hooks.recommended_components().cc_statusline is True   # 已是 tt 的 → 保持
    settings_path.write_text(json.dumps(_CUSTOM_SL), encoding="utf-8")
    assert hooks.recommended_components().cc_statusline is False  # 自定义 → 不接管
    config.save_cc_statusline(True)
    assert hooks.recommended_components().cc_statusline is False  # 探测优先于 intent（防静默再劫持）
    settings_path.write_text("not json{{{", encoding="utf-8")
    assert hooks.recommended_components().cc_statusline is False  # 损坏 → 不可安全触碰
    settings_path.write_text("{}", encoding="utf-8")
    config.save_cc_statusline(False)
    assert hooks.recommended_components().cc_statusline is False  # 无自定义时 intent False 生效


def test_recommended_components_codex_keeps_intent(tmp_path, monkeypatch):
    # SETUP_VERSION bump 后 auto 重配不得把用户的 Codex opt-out 翻回 True。
    from token_tracker import config
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(tmp_path / "no-cc" / "settings.json"))
    assert hooks.recommended_components().codex_faux_statusline is True  # 没表达 → 默认开
    config.save_codex_faux_statusline(False)
    assert hooks.recommended_components().codex_faux_statusline is False  # 已 opt-out → 保留


def test_setup_cc_optout_keeps_custom_statusline(tmp_path, monkeypatch):
    # opt-out 时用户自定义 statusLine 完全不碰；意图 + 引导版本照常落盘。
    from token_tracker import config
    settings_path = _cc_only_home(tmp_path, monkeypatch, json.dumps(_CUSTOM_SL))
    before = settings_path.read_text()

    hooks.setup(components=hooks.SetupComponents(cc_statusline=False), quiet=True)

    assert settings_path.read_text() == before  # settings 一字不动
    assert not os.path.exists(hooks.HOOK_SCRIPT_PATH)
    assert config.cc_statusline_intent() is False
    assert config.setup_version() == config.SETUP_VERSION


def test_setup_cc_optout_restores_backup(tmp_path, monkeypatch):
    # 之前被 tt 接管的用户改选 No → 从 cc-backup.json 还原原 statusLine + 清 tt 产物（脚本/备份/缓存）。
    settings_path = _cc_only_home(tmp_path, monkeypatch, json.dumps(_TT_SL))
    cc_dir = tmp_path / "home" / ".claude"
    (cc_dir / "claude-statusline.py").write_text("x", encoding="utf-8")
    (cc_dir / "tt-status.json").write_text("{}", encoding="utf-8")
    (cc_dir / "cc-backup.json").write_text(json.dumps(_CUSTOM_SL), encoding="utf-8")

    hooks.setup(components=hooks.SetupComponents(cc_statusline=False), quiet=True)

    assert json.loads(settings_path.read_text())["statusLine"] == _CUSTOM_SL["statusLine"]  # 原配置还原
    assert not os.path.exists(hooks.HOOK_SCRIPT_PATH)
    assert not os.path.exists(hooks.CC_BACKUP_PATH)
    assert not os.path.exists(hooks.STATUS_FILE)


def test_setup_cc_optout_tolerates_corrupt_settings(tmp_path, monkeypatch):
    # settings.json 损坏：推荐默认 → False、opt-out 容错不碰 settings——
    # 修掉旧版安装路径 json.load 直接抛异常、每次运行都崩的循环。
    from token_tracker import config
    settings_path = _cc_only_home(tmp_path, monkeypatch, "not json{{{")

    hooks.setup(quiet=True)  # components=None → recommended_components

    assert settings_path.read_text() == "not json{{{"  # 损坏文件原样保留
    assert config.cc_statusline_intent() is False
    assert config.setup_version() == config.SETUP_VERSION
    assert hooks.is_setup() is True  # 止血：之后不再反复触发 setup


def test_setup_default_components_no_hijack(tmp_path, monkeypatch):
    # issue #16/#17 回归主测试：自定义 statusLine + 从没表达过意图 →
    # 默认 setup 绝不抢占 statusLine，且此后 is_setup=True、报表命令不再反复触发 setup。
    settings_path = _cc_only_home(tmp_path, monkeypatch, json.dumps(_CUSTOM_SL))
    before = settings_path.read_text()

    hooks.setup(quiet=True)  # components=None → 探测到自定义 → opt-out

    assert settings_path.read_text() == before
    assert hooks.is_setup() is True
    assert hooks.needs_update() is False


def test_ask_components_asks_cc_then_codex(monkeypatch):
    # 向导：CC 题在前、Codex 题在后；默认值透传自 recommended_components；返回字段映射正确。
    from token_tracker import wizard
    asked: list = []

    monkeypatch.setattr(wizard, "_has_cc", lambda: True)
    monkeypatch.setattr(wizard, "_has_codex", lambda: True)
    monkeypatch.setattr(wizard, "_has_kimi", lambda: False)  # 本机装有 Kimi，固定关掉、问题数稳定
    monkeypatch.setattr(wizard, "recommended_components",
                        lambda: hooks.SetupComponents(cc_statusline=False, codex_faux_statusline=True))

    def fake_ask(message, default):
        asked.append((message, default))
        return default

    monkeypatch.setattr(wizard, "_ask_yes_no", fake_ask)
    c = wizard.ask_components(step_prefix_fn=lambda i: f"[{i}] ")
    assert [d for _, d in asked] == [False, True]  # 默认值来自 recommended（intent 感知）
    assert asked[0][0].startswith("[1] ") and asked[1][0].startswith("[2] ")
    assert c == hooks.SetupComponents(cc_statusline=False, codex_faux_statusline=True)


def test_ask_components_cc_only(monkeypatch):
    # 只有 CC：只问 1 题；codex 字段用推荐默认原样带回（setup 里 has_codex=False 也不会落盘）。
    from token_tracker import wizard
    calls: list = []
    monkeypatch.setattr(wizard, "_has_cc", lambda: True)
    monkeypatch.setattr(wizard, "_has_codex", lambda: False)
    monkeypatch.setattr(wizard, "_has_kimi", lambda: False)  # 本机装有 Kimi，固定关掉、问题数稳定
    monkeypatch.setattr(wizard, "recommended_components", lambda: hooks.SetupComponents())
    monkeypatch.setattr(wizard, "_ask_yes_no", lambda message, default: calls.append(message) or True)
    c = wizard.ask_components()
    assert len(calls) == 1
    assert c.cc_statusline is True and c.codex_faux_statusline is True


# --- Kimi Code sidebar 接线（Skill + config.toml 的 UserPromptSubmit hook） ---

def _patch_kimi_home(monkeypatch, kimi_dir):
    monkeypatch.setattr(hooks, "_KIMI", str(kimi_dir))
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(kimi_dir / "config.toml"))
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(kimi_dir / "tui.toml"))
    monkeypatch.setattr(sidebar_install, "KIMI_SKILL_DIR", str(kimi_dir / "skills" / "tt-sidebar"))


def test_setup_and_unsetup_kimi_sidebar(tmp_path, monkeypatch):
    kimi_dir = tmp_path / "kimi-home"
    kimi_dir.mkdir()
    _patch_kimi_home(monkeypatch, kimi_dir)
    # CC / Codex 目录不存在（autouse fixture 指向 tmp 下不存在的路径），只有 Kimi

    hooks.setup(auto=True, quiet=True)
    assert (kimi_dir / "skills" / "tt-sidebar" / "SKILL.md").exists()
    parsed = tomllib.loads((kimi_dir / "config.toml").read_text(encoding="utf-8"))
    assert any("prompt-hook --agent kimi" in h.get("command", "") for h in parsed["hooks"])

    hooks.unsetup()
    assert not (kimi_dir / "skills" / "tt-sidebar" / "SKILL.md").exists()
    assert "token_tracker" not in (kimi_dir / "config.toml").read_text(encoding="utf-8")


def test_needs_update_detects_missing_kimi_sidebar(tmp_path, monkeypatch):
    kimi_dir = tmp_path / "kimi-home"
    kimi_dir.mkdir()
    _patch_kimi_home(monkeypatch, kimi_dir)
    config.save_setup_version(config.SETUP_VERSION)

    assert hooks.needs_update()  # Kimi 已装但 sidebar 产物缺失
    hooks._setup_kimi_sidebar(quiet=True)
    assert not hooks.needs_update()


def test_update_hook_syncs_kimi_sidebar(tmp_path, monkeypatch):
    kimi_dir = tmp_path / "kimi-home"
    kimi_dir.mkdir()
    _patch_kimi_home(monkeypatch, kimi_dir)
    config.save_setup_version(config.SETUP_VERSION)

    hooks.update_hook()
    assert (kimi_dir / "skills" / "tt-sidebar" / "SKILL.md").exists()
    parsed = tomllib.loads((kimi_dir / "config.toml").read_text(encoding="utf-8"))
    assert any("prompt-hook --agent kimi" in h.get("command", "") for h in parsed["hooks"])


# --- Kimi Code statusline（真 statusline：tui.toml [status_line].command + kimi-statusline.py） ---


def test_kimi_statusline_render_injects_version_and_pricing():
    # 版本号 + 主题配色 + 三档定价注入、占位符不残留、语法正确（脚本零依赖，无 __TT_PYTHON__ 需求）。
    rendered = hooks._render_kimi_statusline_hook()
    assert f'__version__ = "{hooks.KIMI_STATUSLINE_HOOK_VERSION}"' in rendered
    assert "__KIMI_STATUSLINE_HOOK_VERSION__" not in rendered
    assert "__STATUSLINE_TRUECOLOR__" not in rendered
    assert "__KIMI_PRICING__" not in rendered
    assert "kimi-k3" in rendered and "kimi-k2.7-code" in rendered and "kimi-k2.6" in rendered
    assert 'sys.platform == "win32"' in rendered   # Windows stdout UTF-8 防护（同 CC statusline）
    assert "DETACHED_PROCESS" in rendered          # Windows detached 用 creationflags，不用 start_new_session
    compile(rendered, "<kimi-statusline>", "exec")


def test_kimi_statusline_version_roundtrip(tmp_path, monkeypatch):
    # _installed_kimi_statusline_version 读回的版本应与写入的 KIMI_STATUSLINE_HOOK_VERSION 一致。
    script = tmp_path / "kimi-statusline.py"
    monkeypatch.setattr(hooks, "KIMI_STATUSLINE_HOOK_PATH", str(script))
    assert hooks._installed_kimi_statusline_version() is None  # 未装
    hooks._write_kimi_statusline_script()
    assert hooks._installed_kimi_statusline_version() == hooks.KIMI_STATUSLINE_HOOK_VERSION


def _run_kimi_statusline(script, payload, home, kimi_dir, **extra_env):
    env = dict(os.environ, HOME=str(home), KIMI_CODE_HOME=str(kimi_dir))
    env.update(extra_env)
    return subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                          text=True, capture_output=True, env=env)


def test_kimi_statusline_script_renders_one_line_and_accumulates(tmp_path):
    # 脚本级：stdin 快照渲染一行（[项目](分支) | Total | Model）；wire.jsonl 按 offset 增量累计；
    # 终端映射写 tt-terminal-map.json（与 Codex 同文件同 schema），不碰 CC 的 tt-status.json。
    script = tmp_path / "kimi-statusline.py"
    script.write_text(hooks._render_kimi_statusline_hook(), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    kimi_dir = tmp_path / "kimi-home"
    wire = kimi_dir / "sessions" / "wd_x" / "session_abc123" / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    # 真实 git 仓库：1 行已提交文件 → 改 +1 行（未提交）+ 1 个未跟踪文件，验证分支段 diff 统计
    git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(home))
    subprocess.run(["git", "init", "-b", "main"], cwd=proj, check=True, capture_output=True, env=git_env)
    (proj / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=proj, check=True, capture_output=True, env=git_env)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
                   cwd=proj, check=True, capture_output=True, env=git_env)
    (proj / "a.txt").write_text("x\ny\n", encoding="utf-8")   # +1 -0（未提交）
    (proj / "new.txt").write_text("n\n", encoding="utf-8")    # 未跟踪 ?1

    def _record(i, o, cr=0, cc=0):
        return json.dumps({"type": "usage.record", "model": "kimi-code/k3", "time": 1754000000000,
                           "usage": {"inputOther": i, "output": o,
                                     "inputCacheRead": cr, "inputCacheCreation": cc}})

    wire.write_text(_record(1000, 1000) + "\n", encoding="utf-8")
    payload = {"model": "K3", "cwd": str(proj), "gitBranch": "main", "permissionMode": "auto",
               "contextTokens": 82000, "maxContextTokens": 200000,
               "sessionId": "session_abc123", "version": "0.1.0"}
    term_env = {"ITERM_SESSION_ID": "w0t1p0:AAA-111", "TMUX_PANE": "%7"}
    r1 = _run_kimi_statusline(script, payload, home, kimi_dir, **term_env)
    assert r1.returncode == 0
    lines = r1.stdout.splitlines()
    assert len(lines) == 1  # Kimi 只取 stdout 首行：脚本必须单条输出
    line1 = lines[0]
    assert "[proj]" in line1 and "main*" in line1        # 分支 + 脏标记
    assert "+1" in line1 and "?1" in line1               # git diff 统计（+1 -0 ?1，-0 不显示）
    assert "Total: 2k" in line1 and "Cost: $0.02" in line1  # kimi-k3 $3/$15：2000 tok → $0.018
    assert "Model: K3/auto" in line1                       # Model 段拼 permissionMode

    cfg = home / ".config" / "token-tracker"
    term_map = json.loads((cfg / "tt-terminal-map.json").read_text())["_terminal_map"]
    assert term_map["session_abc123"] == {"iterm": "w0t1p0:AAA-111", "tmux": "%7"}
    assert not (cfg / "tt-status.json").exists()      # 不碰 CC 心跳/status 缓存

    # 第二帧：wire 追加一条 usage.record → 按 offset 增量累计（不全量重扫、不重复计数）
    with open(wire, "a", encoding="utf-8") as f:
        f.write(_record(2000, 2000, cr=6000) + "\n")
    r2 = _run_kimi_statusline(script, payload, home, kimi_dir, **term_env)
    line2 = r2.stdout.splitlines()[0]
    assert "Total: 12k" in line2 and "Cost: $0.06" in line2  # 累计 i3000/o3000/cr6000 → $0.0558
    state = json.loads((cfg / "tt-kimi-statusline.json").read_text())
    entry = state["session_abc123"]
    assert entry["wire"] == str(wire)
    assert entry["offset"] == wire.stat().st_size


def test_kimi_statusline_script_partial_line_and_write_skip(tmp_path):
    # 并发写 wire：末尾半截行不消费（offset 不吞字节），补全后下一帧完整计入、不丢不重复；
    # 无新增字节时 state 跳过写盘（mtime 不变）。
    script = tmp_path / "kimi-statusline.py"
    script.write_text(hooks._render_kimi_statusline_hook(), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    kimi_dir = tmp_path / "kimi-home"
    wire = kimi_dir / "sessions" / "wd_x" / "session_abc123" / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    payload = {"model": "K3", "sessionId": "session_abc123"}
    rec1 = json.dumps({"type": "usage.record", "model": "kimi-code/k3",
                       "usage": {"inputOther": 1000, "output": 1000}})
    rec2 = json.dumps({"type": "usage.record", "model": "kimi-code/k3",
                       "usage": {"inputOther": 2000, "output": 2000}})

    # 第一帧：rec1 完整 + rec2 只写了一半（无换行结尾）→ 只计 rec1，半截行不被 offset 吞掉
    wire.write_text(rec1 + "\n" + rec2[:20], encoding="utf-8")
    r1 = _run_kimi_statusline(script, payload, home, kimi_dir)
    assert "Total: 2k" in r1.stdout
    state_file = home / ".config" / "token-tracker" / "tt-kimi-statusline.json"
    entry = json.loads(state_file.read_text())["session_abc123"]
    assert entry["offset"] == len(rec1) + 1  # 只消费到 rec1 的换行为止

    # 第二帧：wire 无变化 → state 跳过写盘（mtime 不变），Total 不变
    mtime1 = state_file.stat().st_mtime_ns
    r2 = _run_kimi_statusline(script, payload, home, kimi_dir)
    assert "Total: 2k" in r2.stdout
    assert state_file.stat().st_mtime_ns == mtime1

    # 第三帧：半截行补全 + 换行 → rec2 完整计入，不丢不重复
    with open(wire, "a", encoding="utf-8") as f:
        f.write(rec2[20:] + "\n")
    r3 = _run_kimi_statusline(script, payload, home, kimi_dir)
    assert "Total: 6k" in r3.stdout  # 累计 i3000/o3000


def test_kimi_statusline_script_fail_open(tmp_path):
    # stdin 损坏 / 空输入：仍输出至多一行（Kimi 取首行），绝不 traceback 到 stdout。
    script = tmp_path / "kimi-statusline.py"
    script.write_text(hooks._render_kimi_statusline_hook(), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    for raw in ("not json{{{", ""):
        r = subprocess.run([sys.executable, str(script)], input=raw, text=True,
                           capture_output=True, env=dict(os.environ, HOME=str(home)))
        assert r.returncode == 0
        assert "Traceback" not in r.stdout
        assert len(r.stdout.splitlines()) <= 1


def _quota_server():
    """本地假 /usages 服务：5h=limits[300min].detail（18%），7d=usage（15%）。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    body = json.dumps({
        "usage": {"limit": "100", "used": "15", "remaining": "85",
                  "resetTime": "2026-08-07T10:26:10Z"},
        "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                    "detail": {"limit": "100", "used": "18", "remaining": "82",
                               "resetTime": "2026-07-31T19:26:10Z"}}],
    }).encode()

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_kimi_statusline_quota_refresh_and_render(tmp_path):
    # Limit 段：--refresh-quota 拉 /usages 写缓存；渲染只读缓存（零网络）；
    # 缓存超 15 分钟失效不显示；detached 后台刷新端到端写回缓存。
    script = tmp_path / "kimi-statusline.py"
    script.write_text(hooks._render_kimi_statusline_hook(), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    kimi_dir = tmp_path / "kimi-home"
    (kimi_dir / "credentials").mkdir(parents=True)
    (kimi_dir / "credentials" / "kimi-code.json").write_text(json.dumps(
        {"access_token": "tok", "expires_at": int(time.time()) + 3600}), encoding="utf-8")
    srv = _quota_server()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/usages"
        env = {"TT_KIMI_QUOTA_URL": url}
        payload = {"model": "K3", "sessionId": "session_q1"}
        cache_file = home / ".config" / "token-tracker" / "tt-kimi-quota.json"

        # 显式刷新：--refresh-quota 只写缓存、不输出
        r = subprocess.run([sys.executable, str(script), "--refresh-quota"],
                           capture_output=True, text=True,
                           env=dict(os.environ, HOME=str(home), KIMI_CODE_HOME=str(kimi_dir), **env))
        assert r.returncode == 0 and not r.stdout
        cache = json.loads(cache_file.read_text())
        assert round(cache["five_hour"]) == 18 and round(cache["seven_day"]) == 15

        # 渲染只读缓存：5h/7d 段出现（阈值色）
        r1 = _run_kimi_statusline(script, payload, home, kimi_dir, **env)
        line1 = r1.stdout.splitlines()[0]
        assert "Limit" not in line1                      # 1.7 起不带 Limit: 前缀
        assert "5h:" in line1 and "18%" in line1
        assert "7d:" in line1 and "15%" in line1

        # 缓存超 15 分钟 → 5h/7d 段消失（本帧仍是旧渲染，后台刷新异步）
        old = time.time() - 1000
        os.utime(cache_file, (old, old))
        r2 = _run_kimi_statusline(script, payload, home, kimi_dir, **env)
        assert "5h:" not in r2.stdout and "7d:" not in r2.stdout

        # detached 后台刷新端到端：删缓存 + 解锁 → 渲染派生子进程写回缓存
        cache_file.unlink()
        lock = str(cache_file) + ".lock"
        if os.path.exists(lock):
            os.remove(lock)
        _run_kimi_statusline(script, payload, home, kimi_dir, **env)
        for _ in range(50):
            if cache_file.exists():
                break
            time.sleep(0.1)
        assert cache_file.exists()
    finally:
        srv.shutdown()


def _kimi_only_home(tmp_path, monkeypatch):
    """Kimi-only 隔离环境：_KIMI/config.toml/tui.toml/Skill 全指向 tmp 下已存在的 kimi 目录；
    CC / Codex 目录不存在（autouse fixture 指向 tmp 下不存在的路径）；脚本/state/config 已由 fixture 隔离。"""
    kimi_dir = tmp_path / "kimi-home"
    kimi_dir.mkdir()
    _patch_kimi_home(monkeypatch, kimi_dir)
    return kimi_dir


def _install_kimi_statusline(tmp_path, kimi_dir):
    """把 tt 的 kimi statusline 实装到隔离环境（脚本 + tui command），返回 tui.toml 路径。"""
    hooks._write_kimi_statusline_script()
    sidebar_install.install_kimi_statusline(hooks._kimi_statusline_command())
    return kimi_dir / "tui.toml"


def test_kimi_statusline_active_double_factor(tmp_path, monkeypatch):
    # 双因素：intent True AND 脚本存在 AND tui.toml command 含 tt token；任一不满足 → False。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)

    assert hooks.kimi_statusline_active() is False  # intent None
    config.save_kimi_statusline(False)
    assert hooks.kimi_statusline_active() is False  # intent False
    config.save_kimi_statusline(True)
    assert hooks.kimi_statusline_active() is False  # 未实装
    tui = _install_kimi_statusline(tmp_path, kimi_dir)
    assert hooks.kimi_statusline_active() is True   # intent True + 实装好

    tui.write_text('[status_line]\ncommand = "/usr/bin/my-own"\n', encoding="utf-8")
    assert hooks.kimi_statusline_active() is False  # command 被改走
    os.remove(hooks.KIMI_STATUSLINE_HOOK_PATH)
    _install_kimi_statusline(tmp_path, kimi_dir)
    os.remove(hooks.KIMI_STATUSLINE_HOOK_PATH)
    assert hooks.kimi_statusline_active() is False  # 脚本缺失


def test_is_setup_kimi_branch(tmp_path, monkeypatch):
    # is_setup Kimi 分支三态：intent None → 未配；False → 放行（不强求文件）；True → 要求实装。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)

    assert hooks.is_setup() is False  # intent None → 触发引导
    config.save_kimi_statusline(False)
    assert hooks.is_setup() is True   # opt-out 放行
    config.save_kimi_statusline(True)
    assert hooks.is_setup() is False  # intent True 但没实装
    _install_kimi_statusline(tmp_path, kimi_dir)
    assert hooks.is_setup() is True   # intent True + 实装好


def test_recommended_components_kimi_probe(tmp_path, monkeypatch):
    # Kimi 推荐默认三层：探测用户自定义 command（do-no-harm，优先于 intent）> 已记录 intent > True。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = kimi_dir / "tui.toml"

    assert hooks.recommended_components().kimi_statusline is True   # 全新（无 tui.toml）→ 接管
    tui.write_text('[status_line]\ncommand = "/usr/bin/my-own"\n', encoding="utf-8")
    assert hooks.recommended_components().kimi_statusline is False  # 用户自定义 → 不接管
    config.save_kimi_statusline(True)
    assert hooks.recommended_components().kimi_statusline is False  # 探测优先于 intent（防静默再劫持）
    tui.unlink()
    assert hooks.recommended_components().kimi_statusline is True   # 无自定义 + intent True
    config.save_kimi_statusline(False)
    assert hooks.recommended_components().kimi_statusline is False  # 无自定义 + intent False


def test_needs_update_kimi_statusline_gate(tmp_path, monkeypatch):
    # setup_version>=5 门控 + 双因素：intent True 才要求脚本版本最新 + tui command 同步；
    # 版本 < 5 或 intent False 都不算 needs_update。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = kimi_dir / "tui.toml"

    config.save_setup_version(4)
    config.save_kimi_statusline(True)
    hooks._setup_kimi_sidebar(quiet=True)  # v4 产物（Skill + UserPromptSubmit hook）先装平
    assert not hooks.needs_update()  # 版本 < 5：kimi statusline 还不算 setup 产物

    config.save_setup_version(5)
    assert hooks.needs_update()      # intent True 但未装 → 待更新
    _install_kimi_statusline(tmp_path, kimi_dir)
    assert not hooks.needs_update()  # 装好后收敛

    tui.write_text("", encoding="utf-8")  # command 漂移（被清掉）
    assert hooks.needs_update()
    sidebar_install.install_kimi_statusline(hooks._kimi_statusline_command())

    script = tmp_path / "_tt" / "kimi-statusline.py"
    script.write_text(script.read_text(encoding="utf-8").replace(
        hooks.KIMI_STATUSLINE_HOOK_VERSION, "0.9"), encoding="utf-8")
    assert hooks.needs_update()      # 脚本版本落后

    config.save_kimi_statusline(False)
    assert not hooks.needs_update()  # intent False 不强求（双因素）


def test_update_hook_syncs_kimi_statusline(tmp_path, monkeypatch):
    # intent True + setup_version>=5：tui command 漂移后 update_hook 重烘焙脚本并重新接线，用户字段保留。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = _install_kimi_statusline(tmp_path, kimi_dir)
    hooks._setup_kimi_sidebar(quiet=True)  # v4 产物装平，隔离 v5 断言
    config.save_setup_version(config.SETUP_VERSION)
    config.save_kimi_statusline(True)

    tui.write_text('theme = "mocha"\n', encoding="utf-8")  # command 被清掉
    assert hooks.needs_update()
    hooks.update_hook()
    assert not hooks.needs_update()
    parsed = tomllib.loads(tui.read_text(encoding="utf-8"))
    assert parsed["theme"] == "mocha"  # 用户字段保留
    assert parsed["status_line"]["command"] == hooks._kimi_statusline_command()


def test_setup_kimi_statusline_install_and_optout(tmp_path, monkeypatch):
    # setup 装：意图落盘 + 脚本 + tui command；再 opt-out：脚本/command 全清、意图翻 False、用户字段保留。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = kimi_dir / "tui.toml"
    tui.write_text('theme = "mocha"\n', encoding="utf-8")

    hooks.setup(auto=True, quiet=True)  # components=None → recommended（无自定义 → 接管）
    assert config.kimi_statusline_intent() is True
    assert os.path.exists(hooks.KIMI_STATUSLINE_HOOK_PATH)
    parsed = tomllib.loads(tui.read_text(encoding="utf-8"))
    assert parsed["theme"] == "mocha"
    assert "kimi-statusline.py" in parsed["status_line"]["command"]
    assert hooks.is_setup() is True

    # 模拟脚本运行产物：state/quota 缓存 + lock
    for path in (
        hooks.KIMI_STATUSLINE_STATE_PATH,
        f"{hooks.KIMI_STATUSLINE_STATE_PATH}.lock",
        hooks.KIMI_STATUSLINE_QUOTA_PATH,
        f"{hooks.KIMI_STATUSLINE_QUOTA_PATH}.lock",
    ):
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")

    hooks.setup(components=hooks.SetupComponents(kimi_statusline=False), quiet=True)
    assert config.kimi_statusline_intent() is False
    assert not os.path.exists(hooks.KIMI_STATUSLINE_HOOK_PATH)
    assert not os.path.exists(hooks.KIMI_STATUSLINE_STATE_PATH)
    assert not os.path.exists(hooks.KIMI_STATUSLINE_QUOTA_PATH)
    assert not os.path.exists(f"{hooks.KIMI_STATUSLINE_STATE_PATH}.lock")
    assert not os.path.exists(f"{hooks.KIMI_STATUSLINE_QUOTA_PATH}.lock")
    parsed = tomllib.loads(tui.read_text(encoding="utf-8"))
    assert "status_line" not in parsed  # tt 的 command 摘净、空表连表头一起删
    assert parsed["theme"] == "mocha"
    assert hooks.is_setup() is True  # opt-out 放行


def test_setup_kimi_statusline_skips_user_custom(tmp_path, monkeypatch):
    # 用户自定义 status_line.command：推荐默认 → opt-out 不碰；显式选 True 也只跳过、绝不覆盖。
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = kimi_dir / "tui.toml"
    original = '[status_line]\ncommand = "/usr/bin/my-own-statusline --foo"\n'
    tui.write_text(original, encoding="utf-8")

    hooks.setup(quiet=True)  # components=None → 探测到自定义 → opt-out
    assert tui.read_text(encoding="utf-8") == original
    assert config.kimi_statusline_intent() is False
    assert not os.path.exists(hooks.KIMI_STATUSLINE_HOOK_PATH)
    assert hooks.is_setup() is True  # 止血：之后不再反复触发 setup

    hooks.setup(components=hooks.SetupComponents(kimi_statusline=True), quiet=True)
    assert tui.read_text(encoding="utf-8") == original  # 显式选 True 也不覆盖用户自定义
    assert not os.path.exists(hooks.KIMI_STATUSLINE_HOOK_PATH)


def test_setup_kimi_statusline_prints_confirmation_when_unchanged(tmp_path, monkeypatch, capsys):
    # 幂等重跑也要输出「已配置」确认行（与 CC/Codex 一致），否则用户无从判断组件状态
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    (kimi_dir / "tui.toml").write_text("", encoding="utf-8")
    hooks.setup(auto=True, quiet=True)
    capsys.readouterr()

    hooks._setup_kimi_statusline(hooks.SetupComponents(kimi_statusline=True))
    assert i18n.t("kimi_statusline_installed") in capsys.readouterr().out


def test_unsetup_removes_kimi_statusline(tmp_path, monkeypatch):
    kimi_dir = _kimi_only_home(tmp_path, monkeypatch)
    tui = kimi_dir / "tui.toml"
    hooks.setup(auto=True, quiet=True)
    state = tmp_path / "_tt" / "tt-kimi-statusline.json"
    state.write_text("{}", encoding="utf-8")  # 模拟运行产物

    hooks.unsetup()
    assert not os.path.exists(hooks.KIMI_STATUSLINE_HOOK_PATH)
    assert not state.exists()
    assert "kimi-statusline" not in tui.read_text(encoding="utf-8")
    assert not (kimi_dir / "skills" / "tt-sidebar" / "SKILL.md").exists()  # sidebar 产物也卸了


def test_ask_components_kimi_question(monkeypatch):
    # 向导：只有 Kimi 时只问 Kimi 题（步号从 1 开始）；默认值透传 recommended_components。
    from token_tracker import wizard
    asked: list = []
    monkeypatch.setattr(wizard, "_has_cc", lambda: False)
    monkeypatch.setattr(wizard, "_has_codex", lambda: False)
    monkeypatch.setattr(wizard, "_has_kimi", lambda: True)
    monkeypatch.setattr(wizard, "recommended_components",
                        lambda: hooks.SetupComponents(kimi_statusline=False))
    monkeypatch.setattr(wizard, "_ask_yes_no",
                        lambda message, default: asked.append((message, default)) or default)
    c = wizard.ask_components(step_prefix_fn=lambda i: f"[{i}] ")
    assert [d for _, d in asked] == [False]  # 默认值来自 recommended（intent 感知）
    assert asked[0][0].startswith("[1] ")
    assert c.kimi_statusline is False
    assert c.cc_statusline is True and c.codex_faux_statusline is True
