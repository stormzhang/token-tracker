# 竞品与参考工具

Token Tracker 功能规划的参考索引，按定位分类。

## 同类工具（历史分析）

### cc-statistics

- **GitHub**: https://github.com/androidZzT/cc-statistics
- **定位**: 多平台 AI Coding 用量统计 CLI
- **技术栈**: Python，零依赖（标准库 + 手写 ANSI）
- **分发**: PyPI / Homebrew
- **数据源**: Claude Code / Codex / Gemini CLI / Cursor（4 平台）
- **Rate Limit**: 基于 output token 速率滑动窗口预测，非真实数据

**值得参考的功能**:

| 功能 | 说明 | 优先级 |
|------|------|--------|
| Gemini CLI 适配器 | `~/.gemini/tmp/*/chats/*.json` 解析 | 高 |
| Cursor 适配器 | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`（SQLite） | 高 |
| 缓存命中率分析 | 按模型分 Excellent/Good/Fair/Poor 四档 | 中 |
| 效率评分 | S-D 五档（代码产出率 40 + 指令精准度 30 + AI 利用率 30） | 中 |
| Git commit 归因 | 将 session 按时间归属到 commit，算每 commit 的 AI 成本 | 中 |
| Skill/MCP 工具统计 | 调用次数、成功率、时间分布 | 低 |
| 会话导出 | Markdown 格式，支持关键词搜索 | 低 |
| Webhook 通知 | 飞书/钉钉/Slack 自动检测推送 | 低 |
| Claude Code Hooks | 16 种事件（Stop/PreToolUse/PermissionRequest 等） | 低 |

**我们已做得更好的地方**:
- Rate Limit 从 `tt-status.json` 读真实服务端数据，cc-statistics 是估算
- Rich 表格 vs 手写 ANSI，渲染质量和可维护性更高
- 交互式 tab 切换 + alternate screen
- Burn rate / 5h 计费块分析
- LiteLLM 动态定价 vs 硬编码价格表

**桌面应用**: SwiftUI 原生 macOS 应用（菜单栏常驻 + 灵动岛），仅 macOS

---

## 实时监控工具

### claude-hud

- **GitHub**: https://github.com/jarrodwatts/claude-hud
- **Stars**: 22k+
- **定位**: Claude Code 实时 HUD 插件，嵌入终端显示当前会话状态
- **技术栈**: TypeScript，零运行时依赖
- **分发**: Claude Code Plugin Marketplace
- **运行方式**: 作为 Claude Code statusLine 子进程常驻，通过 stdin 管道接收数据
- **数据源**: 仅 Claude Code（绑定其插件体系）
- **Rate Limit**: 从 stdin 直接读取服务端原生 `rate_limits` 字段，准确

**值得参考的功能**:

| 功能 | 说明 | 适用场景 |
|------|------|---------|
| Context 健康度 | 进度条 + 百分比，绿→黄→红 | 桌面版实时监控 |
| 工具活动追踪 | 实时显示正在执行的 Read/Edit/Bash | 桌面版实时监控 |
| 子 Agent 追踪 | explore/agent 子任务及耗时 | 桌面版实时监控 |
| Todo 进度 | 实时显示任务完成情况 | 桌面版实时监控 |
| 输出速度 | tok/s 实时显示 | CLI status 命令 |
| Prompt Cache 倒计时 | 缓存过期剩余时间 | CLI / 桌面版 |
| Autocompact 感知 | 检测 compact 事件，防 zero 闪烁 | 数据准确性 |

**与我们的关系**: 互补。claude-hud 解决"现在用了多少"（实时），token-tracker 解决"总共用了多少"（历史）。claude-hud 仅支持 Claude Code，我们支持多 Agent。

**技术启示**: claude-hud 的 stdin 管道方式比我们的 hook → 写文件 → 读文件少一层中转，但这是独立工具的固有限制，无法避免。

---

## API 流量透视工具

### claude-tap

- **GitHub**: https://github.com/liaohch3/claude-tap
- **Stars**: 268
- **定位**: AI CLI 工具的 API 流量拦截器，通过本地中间人代理记录完整请求/响应
- **技术栈**: Python + aiohttp，自签名 CA + 动态证书实现 HTTPS 拦截
- **分发**: PyPI（`pip install claude-tap`）
- **运行方式**: 必须通过 `claude-tap` 启动目标 CLI 工具，拦截所有 API 通信
- **数据源**: Claude Code / Codex / Kimi CLI / OpenCode / Cursor CLI（5 平台）
- **Rate Limit**: 不追踪
- **成本计算**: 不计算

**两种代理模式**:
- 反向代理：设置 `ANTHROPIC_BASE_URL` 等环境变量指向本地，适用于 Claude/Codex/Kimi
- 正向代理：HTTPS CONNECT 隧道 + TLS 终止，适用于 OpenCode/Cursor

**核心能力**:

| 功能 | 说明 |
|------|------|
| SSE 流重组 | 支持 Anthropic 和 OpenAI 两种协议，将分片重组为完整响应 |
| WebSocket 代理 | Codex 的 WebSocket 通信双向代理与重组 |
| 自包含 HTML 查看器 | 180KB 单文件，结构化 diff、路径过滤、工具检查器、暗色模式 |
| 实时查看 | `--tap-live` 启动 SSE 服务，边运行边在浏览器看流量 |
| 会话对比 | 相邻请求的字符级 diff 高亮 |
| 多语言 | 8 种语言 |

**与我们的关系**: 完全互补，零功能重叠。claude-tap 解决"Agent 内部在干什么"（prompt 构建、工具选择、对话管理），token-tracker 解决"花了多少钱、还剩多少额度"。claude-tap 不做 rate limit、成本、趋势分析。

**可参考的点**: 自包含 HTML 查看器的实现思路（懒加载大数据集、结构化 diff），可作为桌面版数据可视化的参考。

---

## 对比总览

| 维度 | token-tracker | cc-statistics | claude-hud | claude-tap |
|------|--------------|---------------|------------|------------|
| 定位 | 历史分析 CLI | 历史分析 CLI | 实时 HUD 插件 | API 流量透视 |
| 平台覆盖 | Claude + Codex | Claude + Codex + Gemini + Cursor | 仅 Claude | Claude + Codex + Kimi + OpenCode + Cursor |
| Rate Limit | 真实数据（hook） | 滑动窗口预测 | 真实数据（stdin） | 不追踪 |
| 成本计算 | LiteLLM 定价 | 硬编码定价 | 原生 + 回退估算 | 不计算 |
| 运行方式 | 独立 CLI | 独立 CLI | Claude Code 子进程 | 中间人代理 |
| 侵入性 | 零（只读文件） | 零（只读文件） | 低（插件注册） | 高（必须改启动方式） |
| 渲染 | Rich 表格 | 手写 ANSI | ANSI 进度条 | HTML 查看器 |
| 交互 | tab 切换 | 无 | 无（嵌入式） | 浏览器 |
| 桌面版 | 计划中（Tauri） | 已有（SwiftUI） | 无 | 无 |
| 语言 | Python | Python | TypeScript | Python |

## 功能差距与规划方向

综合竞品分析，token-tracker 可优先补齐的方向：

1. **扩展数据源** — Gemini CLI / Cursor 适配器（参考 cc-statistics）
2. **缓存命中率** — 按模型计算 cache hit ratio，给出优化建议
3. **效率指标** — 代码产出率、指令精准度等维度评分
4. **Git 归因** — session → commit 关联，量化 AI 成本
5. **实时能力** — 桌面版中加入类似 claude-hud 的实时状态面板
