import os

_STRINGS = {
    "zh": {
        # --- cli.py ---
        "unknown_sort_field": "未知排序字段: {key}，可用: {valid}",
        "no_token_data": "暂无 token 使用数据",
        "no_data": "暂无使用记录，开始使用 Claude Code 或 Codex 后数据会自动出现在这里。",
        "detected": "检测到: {agents}",
        "unknown_cmd": "未知命令: {cmd}",
        "agent_filter_conflict": "--claude / --codex / --kimi / --pi 互斥，只能指定其中一个",
        "agent_not_detected": "未检测到 {flag} 的使用数据，请先在该 agent 中使用后再查询",
        "sessions_limit_invalid": "会话条数必须是正整数，收到: {value}",
        "available_cmds": "可用命令: status, daily, weekly, monthly, sessions, sidebar, theme, setup, unsetup, --version",
        # --- sidebar（cli.py / ui/sidebar.py）---
        "sidebar_empty": "窗口期内没有活跃会话",
        "sidebar_waiting_prompt": "等待当前会话的第一条提示词…",
        "sidebar_state_running": "运行中",
        "sidebar_state_attention": "待确认",
        "sidebar_state_waiting": "等输入",
        "sidebar_state_idle": "空闲",
        "sidebar_jump_no_target": "该会话暂无终端定位（其状态栏渲染过一帧后才有）",
        "sidebar_jump_failed": "跳转失败: {err}",
        "sidebar_next": "下一步",
        "sidebar_active_count": "最近活跃会话 {n} 条",
        "sidebar_tz_bj": "北京",
        "sidebar_tz_la": "洛杉矶",
        "sidebar_tz_ldn": "伦敦",
        "sidebar_jump_gone": "目标终端窗格不存在（可能已关闭）",
        "sidebar_update_hint": "Claude Code 版本较旧，无法识别已关闭的会话，建议升级",
        # --- status.py（会话表列名与 tips）---
        "recent_sessions": "最近会话",
        "sessions_tips": "Tips: tt sessions <N> 调数量 · --sort cost|tokens|time|messages · --asc/--desc 改排序",
        "col_time": "时间",
        "col_agent": "Agent",
        "col_project": "项目",
        "col_model": "模型",
        "col_total_tokens": "总Token",
        "col_cost": "等效成本",
        "col_messages": "消息",
        # --- heatmap.py ---（图例 Less / More 不翻译、硬编码英文）
        "daily_peak": "峰值",
        "daily_streak": "连续/最长",
        "active_days": "活跃天数",
        "weekday_grid": "周日,周一,周二,周三,周四,周五,周六",  # 热力图左侧行标签（周日开头）
        "month_short": "1月,2月,3月,4月,5月,6月,7月,8月,9月,10月,11月,12月",  # 热力图月份表头
        "unit_day": "天",   # 连续天数单位（daily streak）
        # --- theme (cli.py) ---
        "theme_current": "当前主题: {name}{src}",
        "theme_src_env": "（来自环境变量 TT_THEME）",
        "theme_src_config": "（来自配置文件）",
        "theme_src_auto": "（自动选择）",
        "theme_unknown": "未知主题: {name}",
        "theme_options": "可选主题: {names}",
        "theme_set_ok": "已切换到主题 {name}",
        "theme_set_statusline": "状态栏已重新生成，重启会话后生效",
        "theme_env_override": "注意：环境变量 TT_THEME 已设置，会覆盖此次切换",
        "theme_usage": "用法: tt theme [show | list | set <主题名> | preview <主题名>]",
        # --- wizard (wizard.py) ---
        "wizard_pick_theme": "选择配色主题",
        "wizard_q_cc_statusline": "接管 Claude Code 状态栏",
        "wizard_q_codex_statusline": "启用 Codex 伪 statusline",
        "wizard_q_kimi_statusline": "启用 Kimi Code 状态栏",
        "wizard_q_pi_statusline": "启用 Pi 状态栏",
        "theme_recommended": "（推荐）",
        "wizard_done": "配置完成",
        "wizard_summary_lang": "语言",
        "wizard_summary_theme": "主题",
        "wizard_summary_cc_statusline": "Claude Code 状态栏",
        "wizard_summary_statusline": "Codex 状态栏",
        "wizard_summary_kimi_statusline": "Kimi Code 状态栏",
        "wizard_summary_pi_statusline": "Pi 状态栏",
        "wizard_restart": "重启 Claude Code / Codex / Kimi Code / Pi 生效",
        "wizard_reconfig": "更改配置可再次运行 tt setup",
        "wizard_view_reports": "运行 tt status / daily / weekly / monthly 可直接查看报表",
        "wizard_signoff": "祝你使用愉快",
        # --- hooks.py ---
        "no_agent_install": "未检测到 Claude Code、Codex、Kimi Code 或 Pi，请先安装其中之一",
        "auto_setup_hint": "非交互环境，已按推荐默认（语言跟随系统 / 主题 mocha / 不替换已有自定义 statusLine）配置\n如需自定义请在终端运行 tt setup",
        "first_setup": "首次使用，正在配置状态栏...",
        "cc_not_found": "未检测到 Claude Code，跳过",
        "codex_not_found": "未检测到 Codex，跳过",
        "sl_backup_replace": "检测到已有 statusLine，备份后替换",
        "cc_statusline_skipped": "已跳过 Claude Code statusLine 接管（保留你现有配置）",
        "cc_settings_corrupt": "无法解析 {path}（JSON 损坏），已跳过 Claude Code 配置；请修复后重跑 tt setup",
        "cc_settings_corrupt_unsetup": "无法解析 {path}（JSON 损坏），statusLine 未改动；请手动检查该文件",
        "cc_backup_corrupt": "备份文件 {path} 无法解析（已保留供手动恢复），statusLine 将直接移除",
        "cc_configured": "Claude Code statusLine 已配置",
        "restart_cc": "重启 Claude Code 后生效",
        "codex_configured": "Codex 已配置",
        "codex_statusline_hint": "已启用伪 statusline（每次回答后追加一行 5h/7d/Ctx）",
        "restart_codex": "重启 Codex 后生效",
        "sidebar_skill_installed": "已安装 Codex $tt-sidebar Skill：{path}",
        "sidebar_skill_conflict": "{path} 已存在非 Token Tracker 管理的同名 Skill，已保留且未覆盖",
        "codex_hooks_synced": "已统一更新 Codex 用户级 Hooks 配置",
        "sidebar_hook_trust": "在 Codex 中运行 /hooks 检查并信任新增或变更的 Hook；新 Skill 未出现时重启 Codex",
        "codex_hooks_removed": "已移除 Token Tracker 的 Codex Hooks",
        "codex_hooks_corrupt": "无法解析 {path}（JSON 损坏），Token Tracker Hooks 未改动；请修复后重跑 tt setup",
        "codex_hooks_corrupt_unsetup": "无法解析 {path}（JSON 损坏），Token Tracker Hooks 未改动；请手动检查该文件",
        "kimi_skill_installed": "已安装 Kimi Code tt-sidebar Skill：{path}",
        "kimi_hooks_synced": "已更新 Kimi Code Hooks（config.toml 的 UserPromptSubmit）",
        "kimi_hook_hint": "新会话生效；在 Kimi Code 中用 /skill:tt-sidebar 打开当前会话侧边栏",
        "kimi_hooks_removed": "已移除 Token Tracker 的 Kimi Code Hooks",
        "kimi_hooks_corrupt": "无法解析 {path}（TOML 损坏），Token Tracker Hooks 未改动；请修复后重跑 tt setup",
        "kimi_hooks_corrupt_unsetup": "无法解析 {path}（TOML 损坏），Token Tracker Hooks 未改动；请手动检查该文件",
        "kimi_statusline_installed": "Kimi Code 状态栏已配置（tui.toml 的 status_line.command）",
        "kimi_statusline_synced": "已更新 Kimi Code 状态栏命令",
        "kimi_statusline_hint": "新会话生效；状态栏显示 项目 │ 模型 │ Ctx │ 会话累计 token 与成本",
        "kimi_statusline_skipped": "已跳过 Kimi Code 状态栏接管（保留你现有配置）",
        "kimi_statusline_skipped_custom": "检测到 tui.toml 已有自定义 status_line.command，已保留且未覆盖",
        "kimi_statusline_removed": "已移除 Token Tracker 的 Kimi Code 状态栏命令",
        "kimi_tui_corrupt": "无法解析 {path}（TOML 损坏），Kimi Code 状态栏未配置；请修复后重跑 tt setup",
        "kimi_tui_corrupt_unsetup": "无法解析 {path}（TOML 损坏），status_line 未改动；请手动检查该文件",
        "kimi_not_found": "未检测到 Kimi Code，跳过",
        "pi_statusline_installed": "Pi 状态栏已配置（extensions/tt-statusline.ts 扩展）",
        "pi_statusline_synced": "已更新 Pi 状态栏扩展",
        "pi_statusline_hint": "新会话生效；状态栏显示 项目 │ Total │ Cost │ Model │ Ctx（无订阅限额段）",
        "pi_statusline_skipped": "已跳过 Pi 状态栏安装（保留你现有配置）",
        "pi_statusline_skipped_custom": "检测到 {path} 已存在且非 Token Tracker 管理，已保留且未覆盖",
        "pi_statusline_removed": "已移除 Token Tracker 的 Pi 状态栏扩展",
        "pi_not_found": "未检测到 Pi，跳过",
        "no_agent_detected": "未检测到 Claude Code、Codex、Kimi Code 或 Pi",
        "deleted_file": "已删除: {path}",
        "cc_restored": "Claude Code statusLine 已恢复原配置",
        "cc_removed": "Claude Code statusLine 已移除",
        "deleted_cache": "已删除缓存: {path}",
        "codex_restored": "Codex status_line 已恢复原配置（老用户备份）",
    },
    "en": {
        # --- cli.py ---
        "unknown_sort_field": "Unknown sort field: {key}, available: {valid}",
        "no_token_data": "No token usage data",
        "no_data": "No usage records yet. Start using Claude Code or Codex and your data will show up here.",
        "detected": "Detected: {agents}",
        "unknown_cmd": "Unknown command: {cmd}",
        "agent_filter_conflict": "--claude, --codex, --kimi and --pi are mutually exclusive; please pick one",
        "agent_not_detected": "No usage data for {flag}; use it in that agent first, then query again",
        "sessions_limit_invalid": "Session count must be a positive integer, got: {value}",
        "available_cmds": "Available commands: status, daily, weekly, monthly, sessions, sidebar, theme, setup, unsetup, --version",
        # --- sidebar（cli.py / ui/sidebar.py）---
        "sidebar_empty": "No active sessions in the window",
        "sidebar_waiting_prompt": "Waiting for the first prompt in this session…",
        "sidebar_state_running": "running",
        "sidebar_state_attention": "needs you",
        "sidebar_state_waiting": "awaiting input",
        "sidebar_state_idle": "idle",
        "sidebar_jump_no_target": "No terminal mapping for this session yet (appears after its statusline renders a frame)",
        "sidebar_jump_failed": "Jump failed: {err}",
        "sidebar_next": "Next",
        "sidebar_active_count": "{n} active sessions",
        "sidebar_tz_bj": "Beijing",
        "sidebar_tz_la": "LA",
        "sidebar_tz_ldn": "London",
        "sidebar_jump_gone": "Target terminal pane no longer exists (probably closed)",
        "sidebar_update_hint": "Claude Code is too old to detect closed sessions; consider updating",
        # --- status.py（会话表列名与 tips）---
        "recent_sessions": "Recent Sessions",
        "sessions_tips": "Tips: tt sessions <N> for count · --sort cost|tokens|time|messages · --asc/--desc to sort",
        "col_time": "Time",
        "col_agent": "Agent",
        "col_project": "Project",
        "col_model": "Model",
        "col_total_tokens": "Tokens",
        "col_cost": "Cost",
        "col_messages": "Msgs",
        # --- heatmap.py ---（图例 Less / More 不翻译、硬编码英文）
        "daily_peak": "Peak",
        "daily_streak": "Current/Longest Streak",
        "active_days": "Active Days",
        "weekday_grid": "Sun,Mon,Tue,Wed,Thu,Fri,Sat",  # 热力图左侧行标签（Sun 开头）
        "month_short": "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec",  # 热力图月份表头
        "unit_day": "d",   # 连续天数单位（daily streak）
        # --- theme (cli.py) ---
        "theme_current": "Current theme: {name}{src}",
        "theme_src_env": " (from env TT_THEME)",
        "theme_src_config": " (from config file)",
        "theme_src_auto": " (auto-selected)",
        "theme_unknown": "Unknown theme: {name}",
        "theme_options": "Available themes: {names}",
        "theme_set_ok": "Switched to theme {name}",
        "theme_set_statusline": "Status line regenerated, restart session to take effect",
        "theme_env_override": "Note: env TT_THEME is set and overrides this change",
        "theme_usage": "Usage: tt theme [show | list | set <name> | preview <name>]",
        # --- wizard (wizard.py) ---
        "wizard_pick_theme": "Pick a theme",
        "wizard_q_cc_statusline": "Take over Claude Code status line",
        "wizard_q_codex_statusline": "Enable Codex faux statusline",
        "wizard_q_kimi_statusline": "Enable Kimi Code statusline",
        "wizard_q_pi_statusline": "Enable Pi statusline",
        "theme_recommended": "(recommended)",
        "wizard_done": "Setup complete",
        "wizard_summary_lang": "Language",
        "wizard_summary_theme": "Theme",
        "wizard_summary_cc_statusline": "Claude Code statusline",
        "wizard_summary_statusline": "Codex statusline",
        "wizard_summary_kimi_statusline": "Kimi Code statusline",
        "wizard_summary_pi_statusline": "Pi statusline",
        "wizard_restart": "Restart Claude Code / Codex / Kimi Code / Pi to take effect",
        "wizard_reconfig": "Run tt setup again to change settings",
        "wizard_view_reports": "Run tt status / daily / weekly / monthly to view reports",
        "wizard_signoff": "Enjoy!",
        # --- hooks.py ---
        "no_agent_install": "Claude Code, Codex, Kimi Code or Pi not detected, please install one first",
        "auto_setup_hint": "Non-interactive env — configured with recommended defaults (language follows system / theme mocha / existing custom statusLine kept)\nRun tt setup in a terminal to customize",
        "first_setup": "First run, configuring status bar...",
        "cc_not_found": "Claude Code not detected, skipping",
        "codex_not_found": "Codex not detected, skipping",
        "sl_backup_replace": "Existing statusLine detected, backing up and replacing",
        "cc_statusline_skipped": "Skipped Claude Code statusLine takeover (your existing config kept)",
        "cc_settings_corrupt": "Cannot parse {path} (invalid JSON); skipped Claude Code setup — fix it and re-run tt setup",
        "cc_settings_corrupt_unsetup": "Cannot parse {path} (invalid JSON); statusLine untouched — please check the file",
        "cc_backup_corrupt": "Backup {path} is unreadable (kept for manual recovery); statusLine will be removed",
        "cc_configured": "Claude Code statusLine configured",
        "restart_cc": "Restart Claude Code to take effect",
        "codex_configured": "Codex configured",
        "codex_statusline_hint": "Faux statusline enabled (appends 5h/7d/Ctx line after each turn)",
        "restart_codex": "Restart Codex to take effect",
        "sidebar_skill_installed": "Codex $tt-sidebar Skill installed: {path}",
        "sidebar_skill_conflict": "A non-Token Tracker skill already exists at {path}; kept without changes",
        "codex_hooks_synced": "Codex user-level Hooks configuration unified and updated",
        "sidebar_hook_trust": "Run /hooks in Codex to review and trust new or changed hooks; restart Codex if the new Skill is not visible",
        "codex_hooks_removed": "Token Tracker Codex hooks removed",
        "codex_hooks_corrupt": "Cannot parse {path} (invalid JSON); Token Tracker hooks untouched — fix it and re-run tt setup",
        "codex_hooks_corrupt_unsetup": "Cannot parse {path} (invalid JSON); Token Tracker hooks untouched — check it manually",
        "kimi_skill_installed": "Kimi Code tt-sidebar Skill installed: {path}",
        "kimi_hooks_synced": "Kimi Code hooks updated (UserPromptSubmit in config.toml)",
        "kimi_hook_hint": "Takes effect in new sessions; run /skill:tt-sidebar in Kimi Code to open the session sidebar",
        "kimi_hooks_removed": "Token Tracker Kimi Code hooks removed",
        "kimi_hooks_corrupt": "Cannot parse {path} (invalid TOML); Token Tracker hooks untouched — fix it and re-run tt setup",
        "kimi_hooks_corrupt_unsetup": "Cannot parse {path} (invalid TOML); Token Tracker hooks untouched — check it manually",
        "kimi_statusline_installed": "Kimi Code statusline configured (status_line.command in tui.toml)",
        "kimi_statusline_synced": "Kimi Code statusline command updated",
        "kimi_statusline_hint": "Takes effect in new sessions; shows project │ model │ Ctx │ session tokens & cost",
        "kimi_statusline_skipped": "Skipped Kimi Code statusline takeover (your existing config kept)",
        "kimi_statusline_skipped_custom": "Custom status_line.command found in tui.toml; kept without changes",
        "kimi_statusline_removed": "Token Tracker Kimi Code statusline command removed",
        "kimi_tui_corrupt": "Cannot parse {path} (invalid TOML); Kimi Code statusline not configured — fix it and re-run tt setup",
        "kimi_tui_corrupt_unsetup": "Cannot parse {path} (invalid TOML); status_line untouched — please check the file",
        "kimi_not_found": "Kimi Code not detected, skipping",
        "pi_statusline_installed": "Pi statusline configured (extensions/tt-statusline.ts extension)",
        "pi_statusline_synced": "Pi statusline extension updated",
        "pi_statusline_hint": "Takes effect in new sessions; shows project │ Total │ Cost │ Model │ Ctx (no subscription-limit segment)",
        "pi_statusline_skipped": "Skipped Pi statusline install (your existing config kept)",
        "pi_statusline_skipped_custom": "{path} exists and is not Token Tracker managed; kept without changes",
        "pi_statusline_removed": "Token Tracker Pi statusline extension removed",
        "pi_not_found": "Pi not detected, skipping",
        "no_agent_detected": "Claude Code, Codex, Kimi Code or Pi not detected",
        "deleted_file": "Deleted: {path}",
        "cc_restored": "Claude Code statusLine restored",
        "cc_removed": "Claude Code statusLine removed",
        "deleted_cache": "Deleted cache: {path}",
        "codex_restored": "Codex status_line restored (legacy backup)",
    },
}


def _detect_system_lang() -> str:
    """检测系统语言设置，**绕过 CLI 的 `LANG` 环境变量**（主人 CLI 多设 en，但系统可能是中文，
    同时区那套：读系统设置而非环境变量）。macOS 读 `defaults -g AppleLanguages` 首选语言、
    Windows 读用户界面语言（`GetUserDefaultUILanguage`）；其它平台 / 失败回退 `LANG` 等环境变量。
    zh 开头 → 中文，否则英文。"""
    import sys
    if sys.platform == "darwin":
        try:
            import re
            import subprocess
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True, text=True, timeout=2,
            ).stdout
            m = re.search(r'"([^"]+)"', out)  # 取数组首项，如 "zh-Hans-US"
            if m:
                return "zh" if m.group(1).lower().startswith("zh") else "en"
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            # GetUserDefaultUILanguage 返回 LANGID；主语言 ID = 低 10 位，0x04 = 中文（简/繁均是）
            if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04:
                return "zh"
            return "en"
        except Exception:
            pass
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        if os.environ.get(var, "").lower().startswith("zh"):
            return "zh"
    return "en"


def _detect_lang() -> str:
    # 1. 用户配置文件优先（wizard 选过）。延迟 import 避免顶层循环。
    try:
        from . import config
        saved = config.resolve_lang()
        if saved:
            return saved
    except Exception:
        pass
    # 2. TT_LANG 显式覆盖
    env_lang = os.environ.get("TT_LANG", "").lower()
    if env_lang:
        return "zh" if env_lang.startswith("zh") else "en"
    # 3. 系统语言设置（绕过 CLI LANG，见 _detect_system_lang）
    return _detect_system_lang()


LANG = _detect_lang()
_CURRENT = _STRINGS.get(LANG, _STRINGS["en"])


def set_lang(lang: str) -> None:
    """运行时切换语言（wizard 选完即时生效，后续 t() 调用返回新语言文案）。"""
    global LANG, _CURRENT
    LANG = lang if lang in _STRINGS else "en"
    _CURRENT = _STRINGS[LANG]


def t(msg_key: str, **kwargs) -> str:
    # 形参不能叫 key：unknown_sort_field 等字符串带 {key} 占位符，会与 t(..., key=...) 撞名
    s = _CURRENT.get(msg_key, msg_key)
    return s.format(**kwargs) if kwargs else s
