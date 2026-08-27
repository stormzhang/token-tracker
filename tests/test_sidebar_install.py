import json
import tomllib

import pytest

from token_tracker import sidebar_install


def test_skill_render_uses_installed_python(monkeypatch):
    monkeypatch.setattr(sidebar_install.sys, "executable", "/opt/tt env/bin/python")
    rendered = sidebar_install.render_skill("SKILL.md")
    assert "__TT_SIDEBAR_COMMAND__" not in rendered
    assert '"/opt/tt env/bin/python" -B -m token_tracker.sidebar_command split' in rendered
    assert sidebar_install._SKILL_MARKER in rendered
    assert "ITERM_SESSION_ID" in rendered
    assert "TMUX_PANE" in rendered
    assert "sandbox_permissions" in rendered
    assert "require_escalated" in rendered
    assert "justification" in rendered
    assert "prefix_rule" in rendered
    assert "never approve Python generally" in rendered
    assert "Do not first try the iTerm2 launcher inside the sandbox" in rendered


def test_skill_install_update_uninstall_roundtrip(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".agents" / "skills" / "tt-sidebar"
    monkeypatch.setattr(sidebar_install, "SIDEBAR_SKILL_DIR", str(skill_dir))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/first/python")

    assert sidebar_install.skill_needs_sync()
    assert sidebar_install.install_skill()
    assert not sidebar_install.skill_needs_sync()
    assert "name: tt-sidebar" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "$tt-sidebar" in (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert not sidebar_install.install_skill()  # 幂等

    monkeypatch.setattr(sidebar_install.sys, "executable", "/new/python")
    assert sidebar_install.skill_needs_sync()
    assert sidebar_install.install_skill()
    assert '"/new/python"' in (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    assert sidebar_install.uninstall_skill()
    assert not (skill_dir / "SKILL.md").exists()
    assert not skill_dir.exists()


def test_skill_does_not_overwrite_user_owned_skill(tmp_path, monkeypatch):
    skill_dir = tmp_path / "tt-sidebar"
    skill_dir.mkdir()
    skill = skill_dir / "SKILL.md"
    skill.write_text("---\nname: tt-sidebar\ndescription: mine\n---\n", encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "SIDEBAR_SKILL_DIR", str(skill_dir))

    assert not sidebar_install.skill_needs_sync()
    with pytest.raises(FileExistsError):
        sidebar_install.install_skill()
    assert skill.read_text(encoding="utf-8").endswith("description: mine\n---\n")
    assert not sidebar_install.uninstall_skill()


def test_hook_merge_idempotent_and_uninstall_preserves_user(tmp_path, monkeypatch):
    path = tmp_path / ".codex" / "hooks.json"
    path.parent.mkdir()
    user_group = {
        "hooks": [{"type": "command", "command": "python3 user.py", "timeout": 9}],
    }
    path.write_text(json.dumps({"meta": "keep", "hooks": {"UserPromptSubmit": [user_group]}}), encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/venv/bin/python")

    assert sidebar_install.managed_hooks_need_sync(None)
    assert sidebar_install.install_managed_hooks(None)
    assert not sidebar_install.install_managed_hooks(None)
    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data["hooks"]["UserPromptSubmit"]
    assert data["meta"] == "keep"
    assert groups[0] == user_group
    command = groups[1]["hooks"][0]["command"]
    assert command == '"/venv/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex'

    assert sidebar_install.uninstall_managed_hooks()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "meta": "keep", "hooks": {"UserPromptSubmit": [user_group]},
    }


@pytest.mark.parametrize(
    "command_template",
    [
        '"{executable}" -B -m token_tracker.sidebar_command prompt-hook --agent codex',
        '"$HOME/.local/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex',
        '"${{HOME}}/.local/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex',
        '"~/.local/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex',
    ],
)
def test_managed_hooks_treats_home_and_absolute_python_as_equivalent(tmp_path, monkeypatch, command_template):
    home = tmp_path / "home"
    executable = home / ".local" / "bin" / "python"
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{
        "type": "command",
        "command": command_template.format(executable=executable),
        "timeout": 2,
    }]}]}}), encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", str(executable))
    monkeypatch.setattr(sidebar_install.os.path, "expanduser", lambda value: str(home) if value == "~" else value)

    assert sidebar_install._prompt_hook_handler()["command"].startswith('"$HOME/.local/bin/python"')
    assert not sidebar_install.managed_hooks_need_sync(None)


def test_portable_prompt_hook_install_is_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    executable = home / ".venv" / "bin" / "python"
    path = tmp_path / "hooks.json"
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", str(executable))
    monkeypatch.setattr(sidebar_install.os.path, "expanduser", lambda value: str(home) if value == "~" else value)

    assert sidebar_install.install_managed_hooks(None)
    command = json.loads(path.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert command.startswith('"$HOME/.venv/bin/python"')
    assert not sidebar_install.install_managed_hooks(None)
    assert not sidebar_install.managed_hooks_need_sync(None)


@pytest.mark.parametrize(
    "command",
    [
        '"/other/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex',
        '"$HOME/.venv/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent other',
        '"$HOME/.venv/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex; echo stale',
    ],
)
def test_managed_hooks_rejects_different_python_or_prompt_hook_semantics(tmp_path, monkeypatch, command):
    home = tmp_path / "home"
    executable = home / ".venv" / "bin" / "python"
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{
        "type": "command", "command": command, "timeout": 2,
    }]}]}}), encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", str(executable))
    monkeypatch.setattr(sidebar_install.os.path, "expanduser", lambda value: str(home) if value == "~" else value)

    assert sidebar_install.managed_hooks_need_sync(None)


def test_managed_hooks_merge_both_events_and_uninstall_preserves_user(tmp_path, monkeypatch):
    path = tmp_path / ".codex" / "hooks.json"
    path.parent.mkdir()
    user_prompt = {
        "hooks": [{"type": "command", "command": "python3 user-prompt.py", "timeout": 9}],
    }
    user_stop = {
        "hooks": [{"type": "command", "command": "python3 user-stop.py", "timeout": 7}],
    }
    path.write_text(
        json.dumps({
            "meta": "keep",
            "hooks": {"UserPromptSubmit": [user_prompt], "Stop": [user_stop]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/installed/python")
    statusline_command = '"/installed/python" "/cfg/token-tracker/codex-statusline.py"'

    assert sidebar_install.managed_hooks_need_sync(statusline_command)
    assert sidebar_install.install_managed_hooks(statusline_command)
    assert not sidebar_install.install_managed_hooks(statusline_command)
    assert not sidebar_install.managed_hooks_need_sync(statusline_command)
    assert sidebar_install.statusline_hook_present()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["meta"] == "keep"
    assert data["hooks"]["UserPromptSubmit"][0] == user_prompt
    assert data["hooks"]["Stop"][0] == user_stop
    assert data["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"] == (
        '"/installed/python" -B -m token_tracker.sidebar_command prompt-hook --agent codex'
    )
    assert data["hooks"]["Stop"][1]["hooks"][0] == {
        "type": "command",
        "command": statusline_command,
        "timeout": 10,
    }

    assert sidebar_install.uninstall_managed_hooks()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "meta": "keep",
        "hooks": {"UserPromptSubmit": [user_prompt], "Stop": [user_stop]},
    }


def test_managed_hooks_disable_statusline_keeps_prompt_hook(tmp_path, monkeypatch):
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps({
            "hooks": {
                "Stop": [{
                    "hooks": [{
                        "type": "command",
                        "command": '"/old/python" "/old/tt-statusline.py"',
                        "timeout": 10,
                    }],
                }],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/installed/python")

    assert sidebar_install.install_managed_hooks(None)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "Stop" not in data["hooks"]
    assert len(data["hooks"]["UserPromptSubmit"]) == 1
    assert not sidebar_install.statusline_hook_present()


def test_hook_migrates_local_prototype(tmp_path, monkeypatch):
    path = tmp_path / "hooks.json"
    legacy = (
        "/old/python -B /project/.agents/skills/tt-sidebar/scripts/prompt_hook.py "
        "--agent codex"
    )
    path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": legacy, "timeout": 2},
    ]}]}}), encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))

    assert sidebar_install.install_managed_hooks(None)
    raw = path.read_text(encoding="utf-8")
    assert "prompt_hook.py" not in raw
    assert raw.count("token_tracker.sidebar_command") == 1


def test_hook_refuses_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "hooks.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "CODEX_HOOKS", str(path))

    assert not sidebar_install.managed_hooks_need_sync(None)
    with pytest.raises(ValueError):
        sidebar_install.install_managed_hooks(None)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_kimi_skill_install_update_uninstall_roundtrip(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".kimi-code" / "skills" / "tt-sidebar"
    monkeypatch.setattr(sidebar_install, "KIMI_SKILL_DIR", str(skill_dir))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/first/python")

    assert sidebar_install.kimi_skill_needs_sync()
    assert sidebar_install.install_kimi_skill()
    assert not sidebar_install.kimi_skill_needs_sync()
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: tt-sidebar" in content
    assert '"/first/python" -B -m token_tracker.sidebar_command split' in content
    assert sidebar_install._SKILL_MARKER in content
    assert not sidebar_install.install_kimi_skill()  # 幂等

    monkeypatch.setattr(sidebar_install.sys, "executable", "/new/python")
    assert sidebar_install.kimi_skill_needs_sync()
    assert sidebar_install.install_kimi_skill()
    assert '"/new/python"' in (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    assert sidebar_install.uninstall_kimi_skill()
    assert not (skill_dir / "SKILL.md").exists()
    assert not skill_dir.exists()


def test_kimi_hook_merges_into_config_toml_and_preserves_user(tmp_path, monkeypatch):
    path = tmp_path / ".kimi-code" / "config.toml"
    path.parent.mkdir()
    user_block = (
        'default_model = "kimi-code/k3"\n\n'
        '[[hooks]]\nevent = "PreToolUse"\nmatcher = "Bash"\ncommand = "node user.mjs"\ntimeout = 5\n'
    )
    path.write_text(user_block, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/venv/bin/python")

    assert sidebar_install.kimi_hooks_need_sync()
    assert sidebar_install.install_kimi_hooks()
    assert not sidebar_install.install_kimi_hooks()  # 幂等
    assert not sidebar_install.kimi_hooks_need_sync()

    content = path.read_text(encoding="utf-8")
    assert content.startswith(user_block)  # 用户配置原样保留，托管块追加在末尾
    parsed = tomllib.loads(content)
    assert parsed["default_model"] == "kimi-code/k3"
    tt_hooks = [h for h in parsed["hooks"] if "token_tracker" in h.get("command", "")]
    assert tt_hooks == [{
        "event": "UserPromptSubmit",
        "command": '"/venv/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent kimi',
        "timeout": 2,
    }]
    assert {"event": "PreToolUse", "matcher": "Bash", "command": "node user.mjs", "timeout": 5} in parsed["hooks"]

    # 解释器换了 → 需要重同步；旧托管块被替换而非叠加
    monkeypatch.setattr(sidebar_install.sys, "executable", "/new/python")
    assert sidebar_install.kimi_hooks_need_sync()
    assert sidebar_install.install_kimi_hooks()
    content = path.read_text(encoding="utf-8")
    assert content.count("prompt-hook --agent kimi") == 1
    assert '"/new/python"' in content

    assert sidebar_install.uninstall_kimi_hooks()
    assert path.read_text(encoding="utf-8") == user_block
    assert not sidebar_install.uninstall_kimi_hooks()


def test_kimi_hook_installs_into_missing_config(tmp_path, monkeypatch):
    path = tmp_path / ".kimi-code" / "config.toml"
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/venv/bin/python")

    assert sidebar_install.install_kimi_hooks()
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["hooks"] == [{
        "event": "UserPromptSubmit",
        "command": '"/venv/bin/python" -B -m token_tracker.sidebar_command prompt-hook --agent kimi',
        "timeout": 2,
    }]


def test_kimi_hook_refuses_corrupt_toml(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("default_model = [broken", encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(path))

    assert not sidebar_install.kimi_hooks_need_sync()
    with pytest.raises(ValueError):
        sidebar_install.install_kimi_hooks()
    assert path.read_text(encoding="utf-8") == "default_model = [broken"


def test_kimi_hook_collapses_normalized_duplicate_blocks(tmp_path, monkeypatch):
    """Kimi CLI 重写 config.toml 会把 literal string 归一化成 basic string（单引号→双引号），
    历史上旧正则识别不了导致托管块累积、每个提示词触发多次 hook。"""
    path = tmp_path / "config.toml"
    stale = (
        'default_model = "kimi-code/k3"\n\n'
        "[[hooks]]\n"
        'event = "UserPromptSubmit"\n'
        'command = "\\"/old/venv/bin/python3\\" -B -m token_tracker.sidebar_command prompt-hook --agent kimi"\n'
        "timeout = 2\n\n"
        "[[hooks]]\n"
        'event = "UserPromptSubmit"\n'
        'command = "\\"/another/python\\" -B -m token_tracker.sidebar_command prompt-hook --agent kimi"\n'
        "timeout = 2\n"
    )
    path.write_text(stale, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/venv/bin/python")

    assert sidebar_install.kimi_hooks_need_sync()
    assert sidebar_install.install_kimi_hooks()
    content = path.read_text(encoding="utf-8")
    assert content.count("prompt-hook --agent kimi") == 1  # 两个旧块被收敛成一个
    assert '"/venv/bin/python"' in content
    assert content.startswith('default_model = "kimi-code/k3"')  # 用户配置保留
    assert not sidebar_install.install_kimi_hooks()
    assert not sidebar_install.kimi_hooks_need_sync()

    assert sidebar_install.uninstall_kimi_hooks()
    assert "prompt-hook --agent kimi" not in path.read_text(encoding="utf-8")


def test_kimi_hook_normalized_current_block_is_up_to_date(tmp_path, monkeypatch):
    """双引号归一化后的当前块语义上已是最新：不再判为待同步，避免 tt 与 Kimi 互相重写抖动。"""
    path = tmp_path / "config.toml"
    normalized = (
        'default_model = "kimi-code/k3"\n\n'
        "[[hooks]]\n"
        'event = "UserPromptSubmit"\n'
        'command = "\\"/venv/bin/python\\" -B -m token_tracker.sidebar_command prompt-hook --agent kimi"\n'
        "timeout = 2\n"
    )
    path.write_text(normalized, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_CONFIG", str(path))
    monkeypatch.setattr(sidebar_install.sys, "executable", "/venv/bin/python")

    assert not sidebar_install.kimi_hooks_need_sync()
    assert not sidebar_install.install_kimi_hooks()
    assert path.read_text(encoding="utf-8") == normalized  # 一字节都不动


# --- Kimi Code statusline（tui.toml 的 [status_line].command） ---


def test_kimi_statusline_tui_install_idempotent_and_uninstall_restores(tmp_path, monkeypatch):
    path = tmp_path / ".kimi-code" / "tui.toml"
    path.parent.mkdir()
    user_head = 'theme = "mocha"\n'
    path.write_text(user_head, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/token-tracker/kimi-statusline.py"'

    assert sidebar_install.kimi_statusline_tui_command() is None
    assert sidebar_install.kimi_statusline_needs_sync(command)
    assert sidebar_install.install_kimi_statusline(command)
    assert not sidebar_install.install_kimi_statusline(command)  # 幂等
    assert not sidebar_install.kimi_statusline_needs_sync(command)
    assert sidebar_install.kimi_statusline_hook_present()

    content = path.read_text(encoding="utf-8")
    assert content.startswith(user_head)  # 用户配置原样保留，[status_line] 追加在末尾
    parsed = tomllib.loads(content)
    assert parsed["theme"] == "mocha"
    assert parsed["status_line"]["command"] == command

    # 换了命令（新解释器）→ 需要重同步；原位替换而非叠加
    new_command = '"/new/python" "/cfg/token-tracker/kimi-statusline.py"'
    assert sidebar_install.kimi_statusline_needs_sync(new_command)
    assert sidebar_install.install_kimi_statusline(new_command)
    content = path.read_text(encoding="utf-8")
    assert content.count("kimi-statusline.py") == 1
    assert '"/new/python"' in content

    assert sidebar_install.uninstall_kimi_statusline()
    assert path.read_text(encoding="utf-8") == user_head  # 精确还原（含分隔空行被吃掉）
    assert not sidebar_install.uninstall_kimi_statusline()
    assert not sidebar_install.kimi_statusline_hook_present()


def test_kimi_statusline_tui_installs_into_missing_file(tmp_path, monkeypatch):
    path = tmp_path / "tui.toml"
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/kimi-statusline.py"'

    assert sidebar_install.install_kimi_statusline(command)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["status_line"]["command"] == command


def test_kimi_statusline_tui_preserves_user_items(tmp_path, monkeypatch):
    # [status_line] 表已有用户 items：command 插在表头后、items 不动；卸载只摘 command 行，表保留。
    path = tmp_path / "tui.toml"
    original = '[status_line]\nitems = ["model", "cwd"]\n'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/kimi-statusline.py"'

    assert sidebar_install.install_kimi_statusline(command)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["status_line"]["items"] == ["model", "cwd"]
    assert parsed["status_line"]["command"] == command

    assert sidebar_install.uninstall_kimi_statusline()
    assert path.read_text(encoding="utf-8") == original  # 用户 items 表一字不动


def test_kimi_statusline_tui_user_custom_never_overwritten(tmp_path, monkeypatch):
    # 用户自己的 status_line.command（非空、无 tt token）：装/卸/同步判定全部绕行。
    path = tmp_path / "tui.toml"
    original = '[status_line]\ncommand = "/usr/bin/my-own-statusline --foo"\n'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/kimi-statusline.py"'

    assert sidebar_install.kimi_statusline_user_custom()
    assert not sidebar_install.kimi_statusline_needs_sync(command)
    assert not sidebar_install.install_kimi_statusline(command)
    assert not sidebar_install.uninstall_kimi_statusline()
    assert path.read_text(encoding="utf-8") == original  # 一字节都不动


def test_kimi_statusline_tui_empty_command_is_replaced(tmp_path, monkeypatch):
    # 空 command（用户没配 / 内置占位）不算自定义，可被 tt 接管。
    path = tmp_path / "tui.toml"
    path.write_text('[status_line]\ncommand = ""\nitems = ["model"]\n', encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/kimi-statusline.py"'

    assert not sidebar_install.kimi_statusline_user_custom()
    assert sidebar_install.kimi_statusline_needs_sync(command)
    assert sidebar_install.install_kimi_statusline(command)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["status_line"]["command"] == command
    assert parsed["status_line"]["items"] == ["model"]


def test_kimi_statusline_tui_normalized_command_is_up_to_date(tmp_path, monkeypatch):
    """Kimi CLI 重写 tui.toml 会把 literal string 归一化成 basic string（单引号→双引号）：
    语义上已是最新 → needs_sync 为 False、不做任何重写，避免 tt 与 Kimi 互相重写抖动。"""
    path = tmp_path / "tui.toml"
    command = '"/venv/bin/python" "/cfg/token-tracker/kimi-statusline.py"'
    normalized = (
        'theme = "mocha"\n\n'
        "[status_line]\n"
        'command = "\\"/venv/bin/python\\" \\"/cfg/token-tracker/kimi-statusline.py\\""\n'
    )
    path.write_text(normalized, encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))

    assert sidebar_install.kimi_statusline_tui_command() == command
    assert sidebar_install.kimi_statusline_hook_present()
    assert not sidebar_install.kimi_statusline_user_custom()
    assert not sidebar_install.kimi_statusline_needs_sync(command)
    assert not sidebar_install.install_kimi_statusline(command)
    assert path.read_text(encoding="utf-8") == normalized  # 一字节都不动

    # 归一化形态也能精确卸载（识别按 token，不按引号风格）
    assert sidebar_install.uninstall_kimi_statusline()
    assert path.read_text(encoding="utf-8") == 'theme = "mocha"\n'


def test_kimi_statusline_tui_refuses_corrupt_toml(tmp_path, monkeypatch):
    path = tmp_path / "tui.toml"
    path.write_text("status_line = [broken", encoding="utf-8")
    monkeypatch.setattr(sidebar_install, "KIMI_TUI", str(path))
    command = '"/venv/bin/python" "/cfg/kimi-statusline.py"'

    assert sidebar_install.kimi_statusline_tui_command() is None
    assert not sidebar_install.kimi_statusline_needs_sync(command)  # 损坏 → 不静默覆盖
    with pytest.raises(ValueError):
        sidebar_install.install_kimi_statusline(command)
    with pytest.raises(ValueError):
        sidebar_install.uninstall_kimi_statusline()
    assert path.read_text(encoding="utf-8") == "status_line = [broken"
