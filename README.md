# Token Tracker

本地 AI Agent Token 消耗追踪工具，CLI Dashboard 一目了然。

支持 **Claude Code** 和 **Codex** — 实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.10+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

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
tt claude         # 仅 Claude Code
tt codex          # 仅 Codex
```

## 环境要求

- Python 3.10+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## License

MIT
