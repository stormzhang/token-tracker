# Token Tracker (tt)

本地 AI Agent Token 消耗追踪/分析工具，支持 **Claude Code** 和 **Codex** 。

自定义 StatusLine 状态栏 + CLI Dashboard，实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md)

## StatusLine 状态栏

自动为 Claude Code 和 Codex 配置状态栏，`tt setup` 一键配置，脚本更新时自动升级。

**Claude Code**：项目名、5h/7d 配额进度条、CTX 窗口占比、Token 用量、模型名

![Claude Code StatusLine](assets/screenshot-statusline-cc.png)

**Codex**：项目名、5h/7d 配额、上下文剩余、模型名

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

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/master/install.sh | bash
```

或者通过 pip：

```bash
pip install token-tracker
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

## 环境要求

- Python 3.11+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## TODO

未来持续增加更多数据报表，多维度分析。

## License

Copyright (c) 2026 stormzhang. MIT License.
