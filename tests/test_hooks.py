import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from token_tracker import config, hooks


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
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(home / ".claude" / "settings.json"))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tt / "claude-statusline.py"))
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(home / ".codex" / "config.toml"))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tt / "codex-statusline.py"))
    monkeypatch.setattr(hooks, "STATUS_FILE", str(tt / "tt-status.json"))
    monkeypatch.setattr(hooks, "CC_BACKUP_PATH", str(tt / "cc-backup.json"))
    monkeypatch.setattr(hooks, "CODEX_BACKUP_LEGACY", str(tt / "codex-backup.json"))
    monkeypatch.setattr(hooks, "_LEGACY_PATHS", [])
    cfg = tmp_path / "_cfg"
    monkeypatch.setattr(config, "CONFIG_DIR", str(cfg))
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg / "config.json"))
    monkeypatch.setattr(config, "STATUS_FILE", str(cfg / "tt-status.json"))
    monkeypatch.setattr(config, "_LEGACY_THEME_PATH", str(cfg / "theme.json"))
    monkeypatch.setattr(config, "_LEGACY_LANG_PATH", str(cfg / "lang.json"))


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run_statusline(script_path, project_dir):
    data = {
        "workspace": {"project_dir": str(project_dir)},
        "context_window": {"used_percentage": 30, "context_window_size": 200000,
                           "total_input_tokens": 100, "total_output_tokens": 50},
    }
    r = subprocess.run([sys.executable, str(script_path)], input=json.dumps(data),
                       text=True, capture_output=True)
    return r.stdout.splitlines()[0] if r.stdout.strip() else ""


def test_rendered_hook_script_has_single_version_source():
    # HOOK_VERSION 是唯一版本来源：渲染后脚本里的 __version__ 必须等于它，
    # 且占位符不能残留（否则 needs_update 永远判不相等，每次都重写文件）。
    rendered = hooks._render_hook_script()
    assert f'__version__ = "{hooks.HOOK_VERSION}"' in rendered
    assert "__HOOK_VERSION__" not in rendered


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


def test_codex_statusline_install_uninstall_roundtrip(tmp_path, monkeypatch):
    # 末尾追加 tt Stop 段、保留用户已有 Stop hook；幂等；卸载删净 tt 段、留用户项。
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tmp_path / "tt-statusline.py"))
    base = (
        '[tui]\nstatus_line = ["project"]\n\n'
        '[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "mine"\ntimeout = 5\n'
    )
    installed = hooks._install_codex_statusline(base, "python3")
    assert "tt-statusline" in installed and 'command = "mine"' in installed
    assert hooks._install_codex_statusline(installed, "python3") == installed  # 幂等
    removed = hooks._uninstall_codex_statusline(installed)
    assert "tt-statusline" not in removed and 'command = "mine"' in removed


def test_codex_statusline_install_updates_stale_python(tmp_path, monkeypatch):
    # 回归：用户升级 Python / 切换 conda/venv 后再跑 tt setup，
    # 老的 codex-statusline 段被无脑幂等保留 → command 指向已死 python → 状态栏半残（issue 用户反馈）。
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tmp_path / "codex-statusline.py"))
    first = hooks._install_codex_statusline("", "/old/python")
    assert "/old/python" in first
    # 同 python 再装 → 幂等保持
    assert hooks._install_codex_statusline(first, "/old/python") == first
    # 换 python（用户升级了 Python / 切了环境）→ 必须重写、删旧段、装新段
    second = hooks._install_codex_statusline(first, "/new/python")
    assert "/new/python" in second
    assert "/old/python" not in second  # 死路径清干净
    assert second.count("[[hooks.Stop]]") == 1  # 没残留两段


def test_codex_statusline_windows_path_toml_parses(tmp_path, monkeypatch):
    # 回归 ×2：① Windows 路径含 \U \A \P 等被 TOML basic string 当 unicode 转义起始符（issue 用户反馈）
    # —— 写入的 command 必须用 literal string（单引号）包裹；② command 与 CC 侧同治（#13/#14）：
    # 反斜杠转正斜杠 + 双引号包路径（防 `C:\Program Files\...` 空格断词），tomllib 能原样解析回来。
    import tomllib
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH",
                        r"C:\Users\test\.config\token-tracker\codex-statusline.py")
    monkeypatch.setattr(hooks, "_write_codex_statusline_script", lambda: None)  # 别在 macOS 上真写 Windows 路径
    monkeypatch.setattr(hooks.os, "name", "nt")
    py = r"C:\Program Files\Python313\python.exe"  # 含空格：旧裸拼接会在这里断词
    content = hooks._install_codex_statusline("", py)
    parsed = tomllib.loads(content)
    assert parsed["hooks"]["Stop"][0]["hooks"][0]["command"] == \
        '"C:/Program Files/Python313/python.exe" "C:/Users/test/.config/token-tracker/codex-statusline.py"'


def test_codex_statusline_migrates_legacy_bare_command(tmp_path, monkeypatch):
    # 老用户 config.toml 里是旧裸拼接 command → 再跑 install 必须替换成新引号格式、只留一段。
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tmp_path / "codex-statusline.py"))
    legacy = (
        '[tui]\nstatus_line = ["project"]\n\n'
        '[[hooks.Stop]]\n\n'
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {tmp_path / 'codex-statusline.py'}'\n"
        "timeout = 10\n"
    )
    migrated = hooks._install_codex_statusline(legacy, "python3")
    assert f'command = \'"python3" "{tmp_path / "codex-statusline.py"}"\'' in migrated
    assert migrated.count("[[hooks.Stop]]") == 1  # 旧段删净、无重复
    assert hooks._install_codex_statusline(migrated, "python3") == migrated  # 新格式幂等


def test_codex_command_needs_sync_and_update_hook(tmp_path, monkeypatch):
    # 老用户升级后跑任意 tt 命令：needs_update 检出旧格式 → update_hook 自动重写 config.toml。
    codex_config = tmp_path / "config.toml"
    script = tmp_path / "codex-statusline.py"
    codex_config.write_text(
        '[[hooks.Stop]]\n\n'
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        f"command = 'python3 {script}'\n"
        "timeout = 10\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(script))
    monkeypatch.setattr(hooks, "HOOK_SCRIPT_PATH", str(tmp_path / "claude-statusline.py"))  # CC 未装
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(hooks.sys, "executable", "/new/python3")
    monkeypatch.setattr(hooks.os, "name", "posix")

    assert hooks._codex_command_needs_sync()
    assert hooks.needs_update()
    hooks.update_hook()
    content = codex_config.read_text(encoding="utf-8")
    assert f'command = \'"/new/python3" "{script}"\'' in content
    assert not hooks._codex_command_needs_sync()  # 重写后不再触发
    assert content.count("[[hooks.Stop]]") == 1


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
    # 卸载只移除 tt 自己追加的那段 [[hooks.Stop]]，用户已有的 Stop hook 不动。
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(tmp_path / "tt-statusline.py"))
    user_stop = (
        '\n[[hooks.Stop]]\n\n'
        '[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "/usr/bin/my-other-stop"\ntimeout = 3\n'
    )
    base = '[tui]\nstatus_line = ["project"]\n' + user_stop
    installed = hooks._install_codex_statusline(base, "python3")
    removed = hooks._uninstall_codex_statusline(installed)
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


def test_statusline_quota_display_remaining(tmp_path):
    # quota_display=remaining 时，CC statusline 展示剩余百分比，前缀从 Limit 变 Left。
    script = tmp_path / "tt-statusline.py"
    script.write_text(hooks._render_hook_script(), encoding="utf-8")
    home = tmp_path / "home"
    cfg = home / ".config" / "token-tracker"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"quota_display": "remaining"}), encoding="utf-8")
    payload = {"session_id": "S1", "workspace": {"project_dir": str(tmp_path)},
               "rate_limits": {"five_hour": {"used_percentage": 38}}}

    out = _run_statusline_home(script, payload, home)

    assert "Left:" in out and "62%" in out
    assert "Limit:" not in out and "38%" not in out


def test_codex_statusline_quota_display_remaining(tmp_path, monkeypatch):
    # Codex 伪 statusline 同样按 config 展示剩余百分比；上下文 Ctx 仍保持 used 口径。
    import importlib.util

    script = tmp_path / "codex-statusline.py"
    script.write_text(hooks._render_codex_statusline_hook(), encoding="utf-8")
    home = tmp_path / "home"
    cfg = home / ".config" / "token-tracker"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"quota_display": "remaining"}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("_tt_codex_sl_remaining", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    out = mod._render_limit("5h", 38, None, 0, mod._quota_display_mode())

    assert "62%" in out and "38%" not in out


def test_setup_codex_creates_missing_config(tmp_path, monkeypatch):
    # 装了 Codex（~/.codex 目录在）但还没 config.toml → setup 应创建该文件并写入伪 statusline hook。
    # 新版不再动 [tui].status_line（伪 statusline 比官方更全）。
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)  # 只有目录、无 config.toml
    codex_config = home / ".codex" / "config.toml"
    monkeypatch.setattr(hooks, "CODEX_DIR", str(home / ".codex"))
    monkeypatch.setattr(hooks, "CODEX_CONFIG", str(codex_config))
    monkeypatch.setattr(hooks, "CODEX_STATUSLINE_HOOK_PATH", str(home / ".codex" / "tt-statusline.py"))
    monkeypatch.setattr(hooks.config, "CONFIG_PATH", str(tmp_path / "tt-config.json"))  # 隔离 config.json

    assert not codex_config.exists()
    hooks._setup_codex(hooks.SetupComponents(), quiet=True)
    assert codex_config.exists()  # 已创建
    content = codex_config.read_text()
    assert "five-hour-limit" not in content  # 新版不接管 status_line
    assert "tt-statusline" in content        # 伪 statusline Stop hook 写入


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
    monkeypatch.setattr(wizard, "recommended_components", lambda: hooks.SetupComponents())
    monkeypatch.setattr(wizard, "_ask_yes_no", lambda message, default: calls.append(message) or True)
    c = wizard.ask_components()
    assert len(calls) == 1
    assert c.cc_statusline is True and c.codex_faux_statusline is True
