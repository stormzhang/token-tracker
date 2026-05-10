# Token Tracker

本地 AI Agent Token 消耗追踪工具。

## 技术栈

- Python 3.12+
- Rich（CLI 表格渲染）
- 无其他外部依赖（标准库处理 JSON/SQLite/HTTP）

## 项目结构

```
docs/
├── terminal-integrations.md  # 终端状态集成规划
└── competitors.md            # 竞品与参考工具分析
src/
├── cli.py              # CLI 入口，命令路由，交互式 tab 切换
├── hooks.py            # statusLine hook 管理（安装/卸载/脚本生成）
├── adapters/           # 数据源适配器
│   ├── types.py        # 统一数据模型
│   ├── claude.py       # Claude Code JSONL 解析
│   ├── codex.py        # Codex JSONL + SQLite 解析
│   ├── rate_limits.py  # Claude Code rate limits（tt-status.json）
│   └── registry.py     # Agent 自动探测
├── analyzer/           # 数据分析
│   ├── aggregator.py   # 按日/月/会话/块聚合
│   ├── blocks.py       # 5h 计费块 + burn rate
│   └── cost.py         # LiteLLM 定价 + 成本计算
└── ui/
    └── tables.py       # Rich 表格渲染
```

## 命令

```bash
tt setup                         # 配置 Claude Code + Codex 状态栏
tt unsetup                       # 卸载并恢复原始配置
tt                               # 交互式 dashboard（多 Agent 时左右切换）
tt claude                        # Claude Code dashboard
tt codex                         # Codex dashboard
tt status                        # 单行状态输出
tt daily / weekly / monthly      # 按时间维度汇总
tt sessions                      # 会话明细
tt blocks                        # 5h 计费块
```

## statusLine 管理

`tt setup` 一键配置 Claude Code 和 Codex 的状态栏，`tt unsetup` 卸载并恢复原始配置。自动检测已安装的 Agent，未安装的跳过。

### 设计原则

- **不自动安装**：`tt` / `tt claude` 等查看命令不触发 setup
- **备份恢复**：安装时备份原有配置，卸载时自动恢复

### Claude Code

- 脚本：`~/.claude/tt-statusline.py`（渲染状态栏 + 持久化数据）
- 数据流：Claude Code stdin → tt-statusline.py → stdout（状态栏）+ tt-status.json（供 dashboard 读取）
- 原有 statusLine 备份到 `settings.json` 的 `tokenTracker.previousStatusLine`

**状态栏内容**：项目名 → 5h/7d 进度条（订阅制）或 Cost（API 模式）→ CTX 窗口 → Token 用量 → 模型名
**进度条颜色**：绿色（< 50%）→ 黄色（50-80%）→ 红色（> 80%）

### Codex

- 配置：写入 `~/.codex/config.toml` 的 `[tui].status_line` 预设项
- 预设项：project, five-hour-limit, weekly-limit, context-remaining, model-with-reasoning
- 原有配置备份到 `~/.codex/tt-backup.json`

## 数据源

| Agent | 路径 | 格式 | 备注 |
|-------|------|------|------|
| Claude Code | `~/.claude/projects/*/` | JSONL（每条 assistant 消息一个 usage） | rate limits 从 `~/.claude/tt-status.json` 读取 |
| Codex | `~/.codex/sessions/` | JSONL（`total_token_usage` 为会话累计值） | 模型从 `state_5.sqlite` threads 表获取 |

### Codex 注意事项

- `input_tokens` 包含 `cached_input_tokens`（子集关系），解析时需减去
- `reasoning_output_tokens` 归入 output_tokens
- 每个 JSONL 文件产出一条 UsageEntry（使用最终 `total_token_usage`，不累加 `last_token_usage`）
- rate limits 从 JSONL 的 `token_count` 事件中提取（primary=5h, secondary=7d）

## 交互模式

- 多 Agent 时进入 alternate screen buffer（`\033[?1049h`）
- 每次渲染用 `\033[2J\033[H]` 全屏清除后重绘，避免 tab 切换残留
- 退出时恢复主屏幕（`\033[?1049l`）

## 开发规范

- 数据源只读，不修改任何 Agent 的本地文件
- 去重：message_id + request_id
- 成本计算：4 种 token 分别定价（input / output / cache_creation / cache_read）
- 定价数据缓存在项目根目录 pricing_cache.json

## Roadmap：桌面应用

计划基于 Tauri + TypeScript 开发桌面版，面向不熟悉命令行的普通用户，打开即用。

### 候选功能

- 可视化图表（用量趋势、模型占比、成本曲线）
- 会话详情钻取（日 → 会话列表 → 单轮 token 明细）
- 实时监控（状态栏常驻用量，接近限额弹通知）
- 多 Agent 对比（同一视图横向对比用量、成本、效率）
- 数据导出（CSV/PDF 报告）
- 历史数据持久化（SQLite，支持长时间趋势分析）

### 多 Agent / 多模型监控

- 本地日志适配器：为 Cursor、Cline、Aider 等写适配器，数据粒度细（到会话级）
- API 提供商 Usage API：用户配置 API Key，从 OpenAI、DeepSeek、阿里云等拉取用量，覆盖所有使用该 Key 的 Agent
- 两者结合：有本地日志的走适配器，自有 API Key 的走 Usage API

## Git 配置

- commit 邮箱使用 GitHub no-reply 地址，确保 commit 正确关联账号
