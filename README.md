# Token Tracker

本地 AI Agent Token 消耗追踪工具，CLI Dashboard 一目了然。

支持 **Claude Code** 和 **Codex** — 实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.12+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md)

![Token Tracker Dashboard](screenshot.png)

## 功能

- **多 Agent 追踪** — Claude Code + Codex 统一面板，左右键切换
- **限额监控** — 实时 5h / 7d 配额百分比 + 重置倒计时
- **成本分析** — 按会话、日、周、月维度的等效成本统计
- **会话洞察** — 项目、模型、时长、消息数一览
- **5h 计费块分析** — burn rate、活跃/空闲检测
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
tt                # 交互式 Dashboard（方向键切换 Agent）
tt status         # 单行状态输出（适合 tmux / prompt）
tt status claude  # 仅 Claude Code 状态
tt status codex   # 仅 Codex 状态
tt status --format plain  # 不显示进度条的紧凑输出
tt status --no-color  # 禁用颜色，适合 prompt / tmux
tt claude         # 仅 Claude Code
tt codex          # 仅 Codex
tt setup          # 安装 Claude Code statusLine hook
tt unsetup        # 卸载 hook，并恢复安装前的 statusLine 配置
```

## 环境要求

- Python 3.12+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## Roadmap

- 🖥️ **桌面应用** — 基于 Tauri 的跨平台桌面版，打开即用，可视化图表 + 更多数据分析
- 🔌 **更多 Agent 支持** — Cursor、Cline、Aider 等本地日志适配 + API 提供商用量拉取
- 🧩 **终端集成** — 规划见 [Terminal Integrations Roadmap](docs/terminal-integrations.md)

## License

MIT
