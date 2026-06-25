# Token Tracker (tt)

本地 AI Agent Token 消耗追踪/分析工具，支持 **Claude Code** 和 **Codex** 。

自定义 StatusLine 状态栏 + CLI Dashboard，实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.11+-blue) ![CI](https://github.com/stormzhang/token-tracker/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md)

![Token Tracker Daily](assets/screenshot-daily.png)

## 功能亮点

- **多 Agent 统一追踪** — Claude Code + Codex 统一读取，多 Agent 按来源分组
- **状态栏集成** — Claude Code 用官方 StatusLine 接口；**Codex 业界首创伪 statusline 方案**（hook 注入两行真彩色状态栏，把官方未开放的能力在 Codex 里做了出来）
- **限额监控** — 实时 5h / 7d 配额百分比 + 重置倒计时
- **多维成本分析** — 会话 / 日 / 周 / 月多维报表，等效成本统计
- **定价识别** — litellm 在线定价 + 内置官方价双层兜底，覆盖 Claude / OpenAI / Gemini / Grok 及国产主流（Kimi / GLM / Qwen / 豆包 / DeepSeek / MiniMax / MiMo）；新模型自动套用同系列定价、不静默归零
- **会话洞察** — 项目、模型、时长、消息数一览
- **多主题统一配色** — 6 套主题（Catppuccin 全家 + Nord + Dracula），CLI 报表 / CC 状态栏 / Codex 伪 statusline **三者同源**，`tt theme` 一键切换
- **零配置** — 自动检测已安装的 Agent，直接读取本地数据
- **隐私安全** — 数据纯本地存储，不采集、不上传

## StatusLine 状态栏

`tt setup` 自动为 Claude Code 和 Codex 配置状态栏，脚本更新时自动升级。

### Claude Code（官方接口）

基于 Claude Code 官方自定义 StatusLine 接口，**数据完全来自本地 Claude，无任何推测**。

![Claude Code StatusLine](assets/screenshot-statusline-cc.png)

<details>
<summary>四行布局字段详解</summary>

| 行 | 字段 | 说明 |
|----|------|------|
| 1 | `[项目](分支 +12 -3)` | 项目名（加粗）+ Git 分支（未提交修改标 `*`），括号内附工作区相对 HEAD 的增删行数 |
| 1 | `Total: 1.2M` | 本次会话累计消耗 token（输入+输出+cache，解析 transcript 得出） |
| 1 | `Cost: $35.51` | 本次会话等效成本（Claude Code 自带，按官方计费，准确） |
| 1 | `Code: +208 -8` | 本会话 Claude 写 / 删的代码行数（`+` 绿 `-` 红，与 git 变动同配色） |
| 2 | `Limit: 5h: ██░ 31% (1h19m)` | 5 小时滑动窗口配额（仅订阅模式；括号内重置倒计时） |
| 2 | `7d: ██░ 11% (5d8h)` | 7 天滑动窗口配额 |
| 2 | `1.0M Ctx: ██░ 20%` | 上下文窗口总大小及已用占比 |
| 3 | `Tokens: in 392k, out 937, cache 388k` | **当前上下文窗口**的 token 构成（注意：非会话累计，会随 compact 变化） |
| 3 | `Out TPS: 60 tokens/s` | 本轮 output token 生成速度（含 thinking；空闲帧保留上次值） |
| 4 | `Model: Opus 4.8/xhigh/nofast` | 模型名 / reasoning 级别 / 是否 fast 模式 |
| 4 | `Duration: 1h33m` | 当前会话已持续时间 |
| 4 | `Remote: github` | 代码仓库 host（去顶级域） |

> 终端宽度不足时会自动降级：先隐藏重置倒计时，再将进度条简化为百分比数字。**API 模式**无订阅配额，第 2 行只显示 Ctx。

</details>

### Codex（伪 statusline，业界首创）

Codex 官方暂不支持自定义 StatusLine。Token Tracker 通过 hook 注入了一个**伪 statusline**——每次回答完成后，在回答尾部追加两行真彩色状态栏。**这是目前业界少见的把状态栏能力在 Codex 里做出来的实现方案**。

![Codex StatusLine](assets/screenshot-statusline-codex.png)

**两行布局**：

- **L1** `[项目](分支 +A -D) | Total: <会话累计 token> | Model: <模型 推理强度>` —— Total 橙、Model 红
- **L2** `Limit: 5h <进度条> % (reset <倒计时>) | 7d <进度条> % (reset <倒计时>) | <窗口> Ctx <进度条> %`

渲染 24-bit 真彩色、**不进模型上下文**（实测），**配色跟随当前主题**（与 CLI 报表 / CC 状态栏同源，`tt theme` 切换三者一起变）。`tt unsetup` 一并移除。

## 报表速览

`tt status` — 过去 5h 实时面板（合并概览 + 5h/7d 额度 + 近期会话）

![Status](assets/screenshot.png)

`tt weekly` — 周报：本周分析卡片 + 每日趋势柱状图 + 周 / 项目 / 模型趋势

![Weekly](assets/screenshot-weekly.png)

`tt monthly` — 月报：本月分析卡片 + 周柱状图 + 月趋势 + 项目 / 模型分布

![Monthly](assets/screenshot-monthly.png)

`tt sessions` — 最近 20 条会话明细（按 cost 倒序，支持 `--sort` 改字段）

![Sessions](assets/screenshot-sessions.png)

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

脚本自动选最优安装方式（uv / pipx / 私有 venv），绕开 PEP 668、不污染系统 Python。

> **升级**：重跑上面的命令即可（脚本幂等、自动升到最新）。
> **卸载**：`tt unsetup`

## 使用

```bash
tt setup          # 交互配置向导（终端：上下键选语言 / 主题 / 各组件）；非 tty 环境自动全装
tt                # 过去一年 token 热力图 + 顶部三段概览（= tt daily）
tt daily          # 同上（tt 无参即进 daily）
tt status         # 过去 5h 实时面板
tt weekly         # 周报
tt monthly        # 月报
tt sessions       # 最近 20 条会话明细（tt sessions <n> 改条数、--sort 改排序）
tt quota          # 第三方配额提供者诊断
tt theme          # 查看 / 切换配色主题（show / list / set / preview）
tt unsetup        # 卸载并恢复安装前的配置
tt --version      # 查看版本（-v / -V 同义）
```

> 💡 `tt daily` 是 GitHub 风格的 token 贡献热力图（深浅绿方格）。在 Claude Code 会话里输入 `!tt daily` 即可看到彩色热力图 —— 用户主动用 `!` 执行的命令，Claude Code 会渲染其 24-bit 真彩色输出。

## 配色主题

内置 6 套主题，CLI 报表、CC 状态栏与 Codex 伪 statusline **统一同源**（切主题三者一起变）：

![支持的主题](assets/screenshot-themes.png)

| 主题 | 说明 |
|------|------|
| `mocha` / `latte` / `frappe` / `macchiato` | Catppuccin 全家（暗 / 亮终端自动选 mocha / latte） |
| `nord` | Nord |
| `dracula` | Dracula |

```bash
tt theme               # 显示当前主题及来源
tt theme list          # 列出全部主题 + 色块预览
tt theme preview nord  # 预览某主题（CLI 样例 + 状态栏样例行）
tt theme set nord      # 切换主题（持久化 + 重烘焙状态栏）
tt monthly --theme nord  # 任意报表临时换主题渲染（不持久化、不动状态栏，适合对比）
```

- 切换持久化到 `~/.config/token-tracker/config.json`；优先级 `--theme` 参数 > `TT_THEME` 环境变量 > 配置文件 > 自动。
- 终端支持 truecolor 用精确配色；不支持的（如 macOS 自带 Terminal.app）自动降级到 **256 色近似**。

## 第三方配额对接

通过 API Key 使用第三方平台（如 OpenCode 等）时，CC 不自动注入配额数据。Token Tracker 提供可扩展的 Provider 架构，通过外部脚本对接第三方平台的套餐用量。

### 配置方式

在 `~/.claude/tt-config.json` 中配置配额提供者：

```json
{
  "rate_provider": {
    "type": "script",
    "command": "python ~/.claude/tt-opencode-quota.py",
    "cache_ttl": 60,
    "timeout": 10
  }
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `type` | — | 固定为 `script` |
| `command` | — | 要执行的命令 |
| `cache_ttl` | `60` | 缓存秒数，避免频繁调用脚本 |
| `timeout` | `10` | 脚本执行超时秒数 |

### 脚本输出格式

自定义脚本需要输出标准 JSON 格式到 stdout：

```json
{
  "five_hour": {
    "used_percentage": 31.5,
    "resets_at": 1718457600
  },
  "seven_day": {
    "used_percentage": 12.3,
    "resets_at": 1718889600
  },
  "monthly": {
    "used_percentage": 8.7,
    "resets_at": 1719753600
  },
  "source": "OpenCode"
}
```

各窗口字段均可选。`source` 标识来源，显示在 `tt status` 和状态栏中。

### opencode 配置样例

创建 `~/.claude/tt-opencode-quota.py`：

```python
#!/usr/bin/env python3
"""opencode 用量查询脚本：GET workspace 页面 HTML，正则抠出 inline script 的三项用量。"""
import json
import re
import time
import urllib.request
import ssl
import sys

WORKSPACE_ID = "wrk_xxxxxxxxxxxxxxxxxxxxxxxx"
AUTH_COOKIE = "Fe26.2**xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
PAGE_URL = f"https://opencode.ai/workspace/{WORKSPACE_ID}/go"


def fetch_usage():
    req = urllib.request.Request(PAGE_URL, method="GET")
    req.add_header("cookie", f"oc_locale=zh; auth={AUTH_COOKIE.strip()}")
    req.add_header("user-agent",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


def parse(html):
    secs = re.findall(r"resetInSec:(\d+)", html)
    pcts = re.findall(r"usagePercent:([\d.]+)", html)
    if len(secs) < 3 or len(pcts) < 3:
        raise ValueError("用量字段解析失败，cookie 可能已过期或页面结构变化")
    return secs[:3], pcts[:3]


def main():
    now = int(time.time())
    try:
        html = fetch_usage()
        secs, pcts = parse(html)
        rolling_sec, weekly_sec, monthly_sec = (int(s) for s in secs)
        rolling_pct, weekly_pct, monthly_pct = (float(p) for p in pcts)
        print(json.dumps({
            "five_hour": {"used_percentage": round(rolling_pct, 1), "resets_at": now + rolling_sec},
            "seven_day": {"used_percentage": round(weekly_pct, 1), "resets_at": now + weekly_sec},
            "monthly": {"used_percentage": round(monthly_pct, 1), "resets_at": now + monthly_sec},
            "source": "OpenCode",
        }, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

> `auth` cookie 是 Fe26.2 加密、会过期，失效后需重新从浏览器复制覆盖。

### 诊断命令

配置完成后，使用诊断命令验证：

```bash
tt quota           # 查看配额状态和提供者信息
tt quota --debug   # 详细调试信息（含脱敏后的配置内容）
```

数据优先级：官方注入配额（CC 订阅模式）> 第三方提供者 > 官方仅模型信息（降级）。CC 状态栏在检测到无官方配额时自动走 provider 链式查询。

## 高级

### 首次运行向导

第一次跑 `tt`（或在独立终端跑 `tt setup`）会进入**交互式配置向导**，全程上下键选 + 回车确认：

1. **选语言** — 中文 / English（落 `~/.config/token-tracker/config.json`）
2. **选配色主题** — 6 套主题上下键选择，每个选项右侧内联色板预览
3. **启用 Codex 伪 statusline** — Yes/No（仅检测到 Codex 时）

CI / 非 tty 环境（Docker / 脚本 / `curl|bash`）自动按默认全装：**语言跟随系统设置**、主题 mocha、组件全开。装好后想改任何一项，再跑一次 `tt setup` 即可。

### 报告排序

所有报告命令支持 `--sort` 和 `--asc/--desc` 参数：

```bash
tt weekly --sort cost --desc    # 按成本降序
tt sessions --sort tokens --asc # 按 token 升序
```

可选排序字段：`tokens` / `cost` / `messages` / `time` / `input` / `output`

## 数据来源

| Agent | 路径 | 格式 |
|-------|------|------|
| Claude Code | `~/.claude/projects/*/` | JSONL（逐消息用量） |
| Codex | `~/.codex/sessions/` | JSONL + SQLite |

路径跨平台：Windows 下 `~` 解析到 `%USERPROFILE%`。设了 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 环境变量（官方支持的自定义目录）时自动跟随。

Token Tracker 对 Agent 数据**只读**，不做任何修改。

## 环境要求

- Python 3.11+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## License

Copyright (c) 2026 stormzhang. MIT License.
