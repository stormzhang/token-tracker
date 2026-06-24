# Token Tracker (tt)

本地 AI Agent Token 消耗追踪/分析工具，支持 **Claude Code** 和 **Codex** 。

自定义 StatusLine 状态栏 + CLI Dashboard，实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.11+-blue) ![CI](https://github.com/stormzhang/token-tracker/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md)

## StatusLine 状态栏

自动为 Claude Code 和 Codex 配置状态栏，`tt setup` 一键配置，脚本更新时自动升级。

**Claude Code**：基于官方自定义 StatusLine 接口，数据完全来自本地 Claude，准确无任何推测

![Claude Code StatusLine](assets/screenshot-statusline-cc.png)

状态栏共三行，从左到右：

| 行 | 字段 | 说明 |
|----|------|------|
| 1 | `项目名(分支)` | 当前项目目录 + Git 分支，未提交的修改会标 `*` |
| 1 | `5h: ██░ 31% (1h19m)` | 5 小时滑动窗口配额用量，括号内为重置倒计时 |
| 1 | `7d: ██░ 11% (5d8h)` | 7 天滑动窗口配额用量 |
| 1 | `1.0M Context: ██░ 20%` | 上下文窗口总大小及已用占比 |
| 2 | `Tokens: in 155k, out 128k` | 本次会话累计输入/输出 Token |
| 2 | `(本轮: in 1, out 15)` | 当前对话轮次的 Token 用量 |
| 2 | `Cached: 204k` | 当前轮次命中的 Prompt Cache Token 数 |
| 2 | `Cost: $35.51` | 本次会话等效成本（按官方定价计算） |
| 3 | `Model: Opus 4.6/high/nofast` | 模型名 / thinking 级别 / 是否 fast 模式 |
| 3 | `Duration: 1h33m` | 当前会话已持续时间 |

> 终端宽度不足时会自动降级：先隐藏重置倒计时，再将进度条简化为百分比数字。

**Codex**：官方暂不支持自定义 StatusLine 渲染，沿用官方默认样式，`tt setup` 仅写入字段配置

![Codex StatusLine](assets/screenshot-statusline-codex.png)

| 字段 | 说明 |
|------|------|
| `project` | 当前项目目录名 |
| `five-hour-limit` | 5 小时滑动窗口配额用量 |
| `weekly-limit` | 7 天滑动窗口配额用量 |
| `context-remaining` | 上下文窗口剩余占比 |
| `model-with-reasoning` | 模型名 + 推理强度（如 `gpt-5-codex/high`） |

## Dashboard 数据面板和 日/周/月 数据报表分析

![Token Tracker Dashboard](assets/screenshot.png)

![Token Tracker Daily](assets/screenshot-daily.png)

![Token Tracker Weekly](assets/screenshot-weekly.png)

![Token Tracker Monthly](assets/screenshot-monthly.png)

## 功能

- **多 Agent 追踪** — Claude Code + Codex 统一面板，左右键切换
- **状态栏集成** — Claude Code statusLine + Codex status_line，首次运行自动配置，脚本更新自动升级
- **限额监控** — 实时 5h / 7d 配额百分比 + 重置倒计时
- **成本分析** — 按会话、日、周、月维度的等效成本统计，多 Agent 按来源分组展示
- **定价识别** — litellm 在线定价 + 内置官方价双层兜底；同系列新模型自动套用本档定价（含 Claude Fable 5 / Opus 4.8），全新系列缺价时明确提示，不静默按 $0 统计
- **会话洞察** — 项目、模型、时长、消息数一览
- **零配置** — 自动检测已安装的 Agent，直接读取本地数据
- **隐私安全** — 数据纯本地存储，不采集、不上传任何用户信息，极轻量无后顾之忧

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

或者通过 pip：

```bash
pip install --force-reinstall token-tracker && tt setup
```

## 使用

```bash
tt setup          # 初始化配置 Claude Code + Codex status_line
tt                # 交互式 Dashboard（方向键切换 Agent）
tt claude         # 仅展示 Claude Code
tt codex          # 仅展示 Codex
tt daily          # 按日汇总（按 token 消耗排序）
tt weekly         # 按周汇总（多 Agent 分组展示）
tt monthly        # 按月汇总（多 Agent 分组展示）
tt sessions       # 最近 20 条会话明细数据
tt unsetup        # 卸载并恢复安装前的配置
```

### 报告排序

所有报告命令支持 `--sort` 和 `--asc/--desc` 参数：

```bash
tt daily --sort cost --desc     # 按成本降序
tt sessions --sort tokens --asc # 按 token 升序
```

可选排序字段：`tokens` / `cost` / `messages` / `time` / `input` / `output`

### Dashboard 快捷键

| 按键 | 功能 |
|------|------|
| `←` `→` | 切换 Agent |
| `↑` `↓` | 滚动内容 |
| `s` | 切换排序字段（时间 → Token → 等效成本 → 消息数） |
| `r` | 反转排序方向 |
| `+` / `-` | 调整会话显示条数（±10，最少 10 条） |
| `q` | 退出 |

## 第三方 Coding Plan 配额对接

官方 API 会自动注入配额数据到状态栏，但第三方平台（如火山方舟）不支持此机制。Token Tracker 提供可扩展的 Provider 架构，通过脚本或 API 对接第三方平台的 Coding Plan 用量数据。

### 配置方式

在 `~/.claude/tt-config.json` 中配置配额提供者：

```json
{
  "rate_provider": {
    "type": "script",
    "command": "python ~/.claude/tt-ark-quota.py",
    "cache_ttl": 60,
    "timeout": 10
  }
}
```

配置项说明：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `type` | — | 固定为 `script` |
| `command` | — | 要执行的命令 |
| `cache_ttl` | `60` | 缓存秒数，避免频繁调用脚本（建议 ≥ 30） |
| `timeout` | `10` | 脚本执行超时秒数 |

### 脚本输出格式

自定义脚本需要输出标准 JSON 格式：

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
  "source": "火山方舟"
}
```

### 火山方舟配置样例

创建 `~/.claude/tt-ark-quota.py`：

```python
#!/usr/bin/env python3
import json
import urllib.request
import urllib.error
import sys

# 从浏览器开发者工具复制 Cookie 和 x-csrf-token
COOKIE = "monitor_huoshan_web_id=xxx; connect.sid=xxx; ..."
X_CSRF_TOKEN = "xxx"

API_URL = "https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/GetCodingPlanUsage?"

def main():
    try:
        req = urllib.request.Request(API_URL, method="POST")
        req.add_header("cookie", COOKIE)
        req.add_header("x-csrf-token", X_CSRF_TOKEN)

        with urllib.request.urlopen(req, data=b"{}", timeout=10) as resp:
            data = json.loads(resp.read().decode())

        quota_map = {}
        for item in data.get("Result", {}).get("QuotaUsage", []):
            level = item.get("Level")
            percent = item.get("Percent")
            reset = item.get("ResetTimestamp")
            if level == "session":
                quota_map["five_hour"] = {"used_percentage": percent, "resets_at": reset}
            elif level == "weekly":
                quota_map["seven_day"] = {"used_percentage": percent, "resets_at": reset}
            elif level == "monthly":
                quota_map["monthly"] = {"used_percentage": percent, "resets_at": reset}

        print(json.dumps({**quota_map, "source": "火山方舟"}))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### opencode 配置样例

opencode 的用量数据不是独立 REST API，而是内嵌在 workspace 页面 HTML 的 inline script 里（SolidJS hydration stream），结构为 `rollingUsage` / `weeklyUsage` / `monthlyUsage`。鉴权用登录后的 `auth` cookie，脚本 GET 页面后再用正则抠出三项用量。

创建 `~/.claude/tt-opencode-quota.py`：

```python
#!/usr/bin/env python3
"""opencode 用量查询脚本：GET workspace 页面 HTML，正则抠出 inline script 的三项用量。"""
import json
import re
import time
import urllib.request
import urllib.error
import ssl
import sys

# ========== 配置区域 ==========
# 工作区 ID（URL 里的 wrk_xxx）
WORKSPACE_ID = "wrk_xxxxxxxxxxxxxxxxxxxxxxxx"

# 从浏览器复制 auth cookie 的值（Fe26.2**... 那一长串），会过期需定期覆盖
AUTH_COOKIE = "Fe26.2**xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

PAGE_URL = f"https://opencode.ai/workspace/{WORKSPACE_ID}/go"
# =============================


def fetch_usage():
    req = urllib.request.Request(PAGE_URL, method="GET")
    req.add_header("cookie", f"oc_locale=zh; auth={AUTH_COOKIE.strip()}")
    req.add_header("user-agent",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=15, context=ssl_context) as resp:
        return resp.read().decode()


def parse(html):
    """hydration 流里顺序固定为 rollingUsage -> weeklyUsage -> monthlyUsage。"""
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
            "source": "opencode",
        }, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

对应的 `tt-config.json`：

```json
{
  "rate_provider": {
    "type": "script",
    "command": "python ~/.claude/tt-opencode-quota.py",
    "cache_ttl": 60
  }
}
```

> 注意：`auth` cookie 是 Fe26.2 加密、会过期，失效后需重新从浏览器复制覆盖。

### 诊断命令

配置完成后，使用诊断命令验证：

```bash
tt quota           # 查看配额状态和提供者信息
tt quota --debug   # 详细调试信息（含配置内容）
```

## 数据来源

| Agent | 路径 | 格式 |
|-------|------|------|
| Claude Code | `~/.claude/projects/*/` | JSONL（逐消息用量） |
| Codex | `~/.codex/sessions/` | JSONL + SQLite |

Token Tracker 对 Agent 数据**只读**，不做任何修改。

## 环境要求

- Python 3.11+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## 开发

```bash
git clone https://github.com/stormzhang/token-tracker && cd token-tracker
uv run --extra dev pytest                # 运行测试
uv run --extra dev ruff check src tests  # Lint
```

包采用标准 src layout（`src/token_tracker/`）：发行名 `token-tracker`，导入名 `token_tracker`（0.4.0 起）。

## TODO

未来持续增加更多数据报表，多维度分析。

## License

Copyright (c) 2026 stormzhang. MIT License.
