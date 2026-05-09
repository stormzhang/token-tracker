# Terminal Integrations Roadmap

Token Tracker 的终端集成目标不是强行做一个适配所有终端的“全局状态栏”，而是提供稳定、快速、可组合的状态输出，再按用户终端环境接入 tmux、shell prompt、终端模拟器或独立 TUI。

## 目标

- 让终端用户在不打开完整 dashboard 的情况下，快速看到 AI token、成本和限额状态。
- 覆盖大多数常见终端工作流：tmux、zsh/bash/fish、WezTerm/iTerm2/Kitty、独立终端窗口。
- 保持核心能力通用，避免把 Claude/Codex 展示逻辑绑定到某个终端前端。
- 状态命令必须足够快，避免拖慢 prompt 或 tmux status bar。

## 非目标

- 不尝试注入所有终端底部状态栏。普通 CLI 进程没有跨终端常驻 UI 能力。
- 不在第一阶段实现复杂插件系统。
- 不优先引入后台 daemon，除非实际测得日志扫描成本已经影响体验。
- 不为单一终端模拟器写重逻辑，终端模拟器配置只作为适配层。

## 核心设计

先提供一个稳定的一行状态命令，所有终端集成都消费它。

```bash
tt status
tt status --agent claude
tt status --agent codex
tt status claude
tt status codex
tt status --fields limits,cost,tokens
tt status --format compact
tt status --format plain
tt status --format tmux
tt status --format shell
tt status --color
tt status --no-color
```

示例输出：

```text
Claude 5h:██░░░░░░ 23% 7d:█████░░░ 61% | Codex 5h:█░░░░░░░ 12%
```

第一阶段不提供 JSON 输出，避免给普通用户增加不必要的选择。后续如果终端模拟器或桌面应用需要结构化数据，再单独设计稳定的机器可读接口。

## 架构分层

```text
本地 Agent 日志
    ↓
adapters: Claude / Codex / future agents
    ↓
analyzer: token / cost / limits / sessions
    ↓
status service: compact status model
    ↓
frontends: tmux / shell prompt / terminal config / watch TUI
```

关键原则：

- `tt status` 只负责输出状态，不负责修改用户终端配置。
- `tt setup <target>` 可以生成或安装配置，但必须可预览、可撤销。
- 终端特定逻辑放在适配层，核心统计模型保持终端无关。
- 错误输出要短，失败不能阻塞用户终端工作流。

## 集成优先级

### 1. tmux status bar

tmux 是最接近“终端状态栏”的通用方案，适合重度终端用户。

候选配置：

```tmux
set -g status-right '#(tt status --format tmux)'
set -g status-interval 30
```

需要支持：

- `tt setup tmux --print`
- `tt setup tmux --install`
- `tt setup tmux --uninstall`
- 检测已有 `status-right`，避免直接覆盖。

### 2. shell prompt

覆盖面最大，适合不使用 tmux 的用户。它不是实时常驻，但足够轻量。

候选目标：

- zsh: `RPROMPT`
- bash: `PROMPT_COMMAND` 或 `PS1`
- fish: `fish_right_prompt`

需要支持：

- `tt setup zsh --print`
- `tt setup bash --print`
- `tt setup fish --print`
- 默认只输出配置片段，安装前必须提示会改哪个 shell rc 文件。

### 3. 终端模拟器集成

WezTerm、iTerm2、Kitty 等终端能力不统一，只做官方示例配置，不作为第一核心。

候选方式：

- WezTerm: Lua status update hook 调用 `tt status`
- iTerm2: status bar component 或 shell integration
- Kitty: watcher / tab title / shell integration

原则：

- 示例配置放在文档或模板中。
- 不把终端模拟器专有 API 写进核心逻辑。
- 优先复用 `tt status`。

### 4. 独立 watch TUI

给所有用户兜底，不依赖终端状态栏能力。

候选命令：

```bash
tt watch
tt watch --interval 5
tt watch --agent claude
tt watch --agent codex
```

适用场景：

- 用户不使用 tmux。
- 用户不想改 shell 配置。
- 用户希望单独开一个终端窗口持续观察。

## 性能策略

状态栏命令可能每 10-30 秒执行一次，也可能每次 prompt 刷新时执行，必须控制耗时。

第一阶段先直接计算，并增加耗时观测：

```bash
tt status --debug-timing
```

如果真实数据下耗时过高，再增加轻量缓存：

```text
读取本地日志 -> 生成 ~/.cache/token-tracker/status.json -> tt status 快速读取缓存
```

缓存策略：

- 默认 TTL: 15-30 秒。
- 状态栏优先读缓存。
- dashboard 和显式刷新命令可以强制重新扫描。
- 缓存必须只存派生统计结果，不复制原始对话内容。

后台 daemon 作为第三阶段选择：

```bash
tt daemon start
tt daemon stop
tt daemon status
```

只有当缓存仍不能满足性能要求时再做。

## 诊断与安装

提供环境检测命令，帮助用户选择最合适的集成方式。

```bash
tt doctor
```

检测项：

- 是否检测到 Claude Code / Codex 数据源。
- 当前是否在 tmux 中。
- 当前 shell 类型：zsh / bash / fish。
- `tt status` 执行耗时。
- 是否存在可写缓存目录。
- 是否已配置过 Token Tracker 集成。

推荐输出：

```text
Detected: Claude Code, Codex
Shell: zsh
tmux: yes
Recommended: tt setup tmux --print
Status latency: 120ms
```

## 分阶段计划

### Phase 1: 通用状态输出

实现：

- `tt status`
- `tt status claude`
- `tt status codex`
- `tt status --agent`
- `tt status --fields`
- `tt status --format compact`
- `tt status --format plain`
- `tt status --color`
- `tt status --no-color`

验收标准：

- 正常数据下输出一行状态。
- 没有数据源时短提示，不抛长 traceback。
- 执行耗时可观测。

### Phase 2: tmux 和 shell 集成

实现：

- `tt setup tmux --print`
- `tt setup zsh --print`
- `tt setup bash --print`
- `tt setup fish --print`
- `tt doctor`

验收标准：

- 默认不直接改用户配置文件。
- 安装模式必须明确提示修改路径和覆盖风险。
- 已有配置不会被静默覆盖。

### Phase 3: watch TUI 与缓存

实现：

- `tt watch`
- 状态缓存文件。
- `tt status --refresh`
- `tt status --debug-timing`

验收标准：

- 状态栏调用不会频繁全量扫描大日志。
- 缓存不包含原始对话内容。
- watch TUI 能按固定间隔刷新。

### Phase 4: 终端模拟器示例和 daemon

实现：

- WezTerm 配置示例。
- iTerm2 配置说明。
- Kitty 配置说明。
- 必要时实现 `tt daemon`。

验收标准：

- 所有终端模拟器示例都只依赖 `tt status`。
- daemon 是可选能力，不影响普通 CLI 使用。

## 待决策问题

- `tt status` 默认展示字段：限额优先、成本优先，还是 token 优先。
- 多 Agent 同时存在时的排序和截断策略。
- 是否默认显示美元成本，还是允许隐藏成本只显示 token。
- 彩色输出在 tmux、prompt、普通终端中的默认策略。
- 缓存目录是否遵循 XDG：`~/.cache/token-tracker/`。
- `setup --install` 是否第一版提供，还是只提供 `--print`。
