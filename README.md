# Token Tracker (tt)

本地 AI Agent Token 消耗追踪/分析工具，支持 **Claude Code** 和 **Codex** 。

自定义 StatusLine 状态栏 + CLI Dashboard，实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

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

**Codex**：官方暂不支持自定义 StatusLine，使用官方默认样式，展示项目名、5h/7d 配额、上下文剩余、模型名

![Codex StatusLine](assets/screenshot-statusline-codex.png)

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
- **会话洞察** — 项目、模型、时长、消息数一览
- **零配置** — 自动检测已安装的 Agent，直接读取本地数据
- **隐私安全** — 数据纯本地存储，不采集、不上传任何用户信息，极轻量无后顾之忧

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

或者通过 pip：

```bash
pip install --force-reinstall token-tracker
tt setup
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

## 环境要求

- Python 3.11+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## TODO

未来持续增加更多数据报表，多维度分析。

## License

Copyright (c) 2026 stormzhang. MIT License.
