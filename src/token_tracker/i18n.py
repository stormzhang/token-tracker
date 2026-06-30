import os

_STRINGS = {
    "zh": {
        # --- cli.py ---
        "unknown_sort_field": "未知排序字段: {key}，可用: {valid}",
        "no_token_data": "暂无 token 使用数据",
        "loading": "加载数据...",
        "no_data": "暂无使用记录，开始使用 Claude Code 或 Codex 后数据会自动出现在这里。",
        "no_agent": "未检测到 Claude Code 或 Codex，请确认已安装并使用过其中之一（使用记录在 ~/.claude 或 ~/.codex ）后再运行。",
        "detected": "检测到: {agents}",
        "unknown_cmd": "未知命令: {cmd}",
        "available_cmds": "可用命令: status, daily, weekly, monthly, sessions, theme, setup, unsetup, --version",
        "sort_time": "时间",
        "sort_token": "Token",
        "sort_cost": "等效成本",
        "sort_messages": "消息数",
        "session_title": "最近会话({limit})  [dim](s)排序:[/dim] {label}{arrow}  [dim](r)反转  (+/-)条数[/dim]",
        # --- tables.py ---
        "reset_at": "重置于 {time}",
        "rate_per_day": "速率: {rate}/天",
        "cost_label": "等效成本",
        "daily_avg": "日均: {cost}",
        "msg_session": "消息      {msgs} 条  会话: {sessions}",
        "tab_help_compact": "  ←→ jk q/ESC退出",
        "tab_help": "  ← → 切换  ↑ ↓ 滚动  q / ESC 退出",
        "history_overview": "历史总览",
        "cost_colon": "等效成本: ",
        "sessions_colon": "会话: ",
        "messages_colon": "消息: ",
        "days_colon": "天数: ",
        "month_overview": "本月概览",
        "daily_avg_colon": "日均: ",
        "recent_sessions": "最近会话",
        "sessions_tips": "Tips: tt sessions <N> 调数量 · --sort cost|tokens|time|messages · --asc/--desc 改排序",
        "col_time": "时间",
        "col_source": "来源",
        "col_agent": "Agent",
        "col_project": "项目",
        "col_model": "模型",
        "col_total_tokens": "总Token",
        "col_cost": "等效成本",
        "col_messages": "消息",
        "col_date": "日期",
        "col_sessions": "会话",
        "col_week": "周",
        "total_row": "合计",
        "col_month": "月份",
        "col_cache_create": "Cache创建",
        "col_cache_read": "Cache读取",
        "model_breakdown": "模型分布",
        "col_ratio": "占比",
        "daily_panel_title": "当日数据面板 (P90)",
        "msg_unit": "{n} 条",
        "session_msg": "会话: {sessions}  消息: {msgs}",
        "rate_per_msg": "速率: {rate}/条",
        "week_token": "本周 Token {tokens}",
        "week_cost": "本周成本",
        "active_panel_title": "当前 5h&7d 数据面板",
        "limit_5h": "5h 限额",
        "time_label": "时间",
        "time_elapsed": "已用 {elapsed}min / 剩余 {h}h{m:02d}m",
        "rate_per_min": "速率: {rate}/min",
        "model_label": "模型: {model}",
        "msg_count": "消息      {n} 条",
        "limit_7d": "7d 限额",
        "idle_panel_title": "限额数据面板",
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
        "wizard_theme_prompt": "选择主题",
        "wizard_q_claude_statusline": "接管 Claude Code statusLine",
        "wizard_q_codex_statusline": "启用 Codex 伪 statusline",
        "theme_recommended": "（推荐）",
        "wizard_done": "配置完成",
        "wizard_summary_lang": "语言",
        "wizard_summary_theme": "主题",
        "wizard_summary_claude_statusline": "Claude 状态栏",
        "wizard_summary_codex_statusline": "Codex 状态栏",
        "wizard_restart": "重启 Claude Code / Codex 生效",
        "wizard_reconfig": "更改配置可再次运行 tt setup",
        "wizard_view_reports": "运行 tt status / daily / weekly / monthly 可直接查看报表",
        "wizard_signoff": "祝你使用愉快",
        # --- hooks.py ---
        "no_agent_install": "未检测到 Claude Code 或 Codex，请先安装其中之一",
        "auto_setup_hint": "非交互环境，已按默认（语言跟随系统 / 主题 mocha / 不接管状态栏）初始化\n如需启用状态栏集成请在终端运行 tt setup",
        "first_setup": "首次使用，正在初始化配置...",
        "cc_not_found": "未检测到 Claude Code，跳过",
        "codex_not_found": "未检测到 Codex，跳过",
        "sl_backup_replace": "检测到已有 statusLine，备份后替换",
        "cc_configured": "Claude Code statusLine 已配置",
        "restart_cc": "重启 Claude Code 后生效",
        "codex_configured": "Codex 已配置",
        "codex_statusline_hint": "已启用伪 statusline（每次回答后追加一行 5h/7d/Ctx）",
        "restart_codex": "重启 Codex 后生效",
        "no_agent_detected": "未检测到 Claude Code 或 Codex",
        "deleted_file": "已删除: {path}",
        "sl_not_tt": "当前 statusLine 不是 tt-statusline，保留现有配置",
        "cc_restored": "Claude Code statusLine 已恢复原配置",
        "cc_removed": "Claude Code statusLine 已移除",
        "deleted_cache": "已删除缓存: {path}",
        "codex_restored": "Codex status_line 已恢复原配置（老用户备份）",
    },
    "en": {
        # --- cli.py ---
        "unknown_sort_field": "Unknown sort field: {key}, available: {valid}",
        "no_token_data": "No token usage data",
        "loading": "Loading...",
        "no_data": "No usage records yet. Start using Claude Code or Codex and your data will show up here.",
        "no_agent": "No Claude Code or Codex usage found. Make sure you've installed and used one of them (logs live in ~/.claude or ~/.codex), then try again.",
        "detected": "Detected: {agents}",
        "unknown_cmd": "Unknown command: {cmd}",
        "available_cmds": "Available commands: status, daily, weekly, monthly, sessions, theme, setup, unsetup, --version",
        "sort_time": "Time",
        "sort_token": "Token",
        "sort_cost": "Cost",
        "sort_messages": "Messages",
        "session_title": "Sessions({limit})  [dim](s)Sort:[/dim] {label}{arrow}  [dim](r)Reverse  (+/-)Count[/dim]",
        # --- tables.py ---
        "reset_at": "Resets at {time}",
        "rate_per_day": "Rate: {rate}/day",
        "cost_label": "Cost",
        "daily_avg": "Avg: {cost}",
        "msg_session": "Messages  {msgs}   Sessions: {sessions}",
        "tab_help_compact": "  ←→ jk q/ESC quit",
        "tab_help": "  ← → switch  ↑ ↓ scroll  q / ESC quit",
        "history_overview": "Overview",
        "cost_colon": "Cost: ",
        "sessions_colon": "Sessions: ",
        "messages_colon": "Messages: ",
        "days_colon": "Days: ",
        "month_overview": "This Month",
        "daily_avg_colon": "Avg: ",
        "recent_sessions": "Recent Sessions",
        "sessions_tips": "Tips: tt sessions <N> for count · --sort cost|tokens|time|messages · --asc/--desc to sort",
        "col_time": "Time",
        "col_source": "Source",
        "col_agent": "Agent",
        "col_project": "Project",
        "col_model": "Model",
        "col_total_tokens": "Tokens",
        "col_cost": "Cost",
        "col_messages": "Msgs",
        "col_date": "Date",
        "col_sessions": "Sessions",
        "col_week": "Week",
        "total_row": "Total",
        "col_month": "Month",
        "col_cache_create": "Cache Create",
        "col_cache_read": "Cache Read",
        "model_breakdown": "Model Distribution",
        "col_ratio": "Ratio",
        "daily_panel_title": "Daily Panel (P90)",
        "msg_unit": "{n}",
        "session_msg": "Sessions: {sessions}  Messages: {msgs}",
        "rate_per_msg": "Rate: {rate}/msg",
        "week_token": "Week Token {tokens}",
        "week_cost": "Week Cost",
        "active_panel_title": "5h & 7d Rate Limits",
        "limit_5h": "5h Limit",
        "time_label": "Time",
        "time_elapsed": "Elapsed {elapsed}min / Remaining {h}h{m:02d}m",
        "rate_per_min": "Rate: {rate}/min",
        "model_label": "Model: {model}",
        "msg_count": "Messages  {n}",
        "limit_7d": "7d Limit",
        "idle_panel_title": "Rate Limits",
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
        "wizard_theme_prompt": "Pick a theme",
        "wizard_q_claude_statusline": "Take over Claude Code statusLine",
        "wizard_q_codex_statusline": "Enable Codex faux statusline",
        "theme_recommended": "(recommended)",
        "wizard_done": "Setup complete",
        "wizard_summary_lang": "Language",
        "wizard_summary_theme": "Theme",
        "wizard_summary_claude_statusline": "Claude statusline",
        "wizard_summary_codex_statusline": "Codex statusline",
        "wizard_restart": "Restart Claude Code / Codex to take effect",
        "wizard_reconfig": "Run tt setup again to change settings",
        "wizard_view_reports": "Run tt status / daily / weekly / monthly to view reports",
        "wizard_signoff": "Enjoy!",
        # --- hooks.py ---
        "no_agent_install": "Claude Code or Codex not detected, please install one first",
        "auto_setup_hint": "Non-interactive env — initialized defaults (language follows system / theme mocha / no statusline takeover)\nRun tt setup in a terminal to enable statusline integrations",
        "first_setup": "First run, initializing config...",
        "cc_not_found": "Claude Code not detected, skipping",
        "codex_not_found": "Codex not detected, skipping",
        "sl_backup_replace": "Existing statusLine detected, backing up and replacing",
        "cc_configured": "Claude Code statusLine configured",
        "restart_cc": "Restart Claude Code to take effect",
        "codex_configured": "Codex configured",
        "codex_statusline_hint": "Faux statusline enabled (appends 5h/7d/Ctx line after each turn)",
        "restart_codex": "Restart Codex to take effect",
        "no_agent_detected": "Claude Code or Codex not detected",
        "deleted_file": "Deleted: {path}",
        "sl_not_tt": "Current statusLine is not tt-statusline, keeping existing config",
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
