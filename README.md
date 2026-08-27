# Token Tracker (tt)

本地 AI Agent Token 消耗追踪/分析工具，支持 **Claude Code**、**Codex**、**Kimi Code** 和 **Pi**。

自定义 StatusLine 状态栏 + CLI Dashboard，实时查看 token 用量、等效成本、限额状态。

![Python](https://img.shields.io/badge/python-3.11+-blue) ![CI](https://github.com/stormzhang/token-tracker/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md)

![Token Tracker Daily](assets/screenshot-daily.png)

## 功能亮点

- **多 Agent 统一追踪** — Claude Code + Codex + Kimi Code + Pi 统一读取，多 Agent 按来源分组
- **状态栏集成** — Claude Code 用官方 StatusLine 接口；**Codex 业界首创伪 statusline 方案**（hook 注入两行真彩色状态栏，把官方未开放的能力在 Codex 里做了出来）；Kimi Code 用官方 `status_line` 接口；Pi 用官方扩展系统（`ctx.ui.setStatus`）
- **实时侧边栏** — `tt sidebar` 窄窗格常驻面板：全部活跃会话一屏总览（状态灯 + 最近提示词 + 「下一步」建议），点击会话直达对应 iTerm2 / tmux 窗格
- **当前会话自动分屏** — Codex 中显式执行 `$tt-sidebar`，在原会话右侧自动打开 1/3 宽度的独立提示词侧边栏
- **限额监控** — 实时 5h / 7d 配额百分比 + 重置倒计时
- **多维成本分析** — 会话 / 日 / 周 / 月多维报表，等效成本统计
- **定价识别** — litellm 在线定价 + 内置官方价双层兜底，覆盖 Claude / OpenAI / Gemini / Grok 及国产主流（Kimi / GLM / Qwen / 豆包 / DeepSeek / MiniMax / MiMo）；新模型自动套用同系列定价、不静默归零
- **会话洞察** — 项目、模型、时长、消息数一览
- **多主题统一配色** — 6 套主题（Catppuccin 全家 + Nord + Dracula），CLI 报表与各 Agent 状态栏**同源**，`tt theme` 一键切换
- **零配置** — 自动检测已安装的 Agent，直接读取本地数据
- **隐私安全** — 数据纯本地存储，不采集、不上传

## StatusLine 状态栏

`tt setup` 自动为 Claude Code、Codex 和 Kimi Code 配置状态栏，脚本更新时自动升级。

### Claude Code（官方接口）

基于 Claude Code 官方自定义 StatusLine 接口，**数据完全来自本地 Claude，无任何推测**。

> 状态栏接管是**可选**的：已有自定义 statusLine 时默认保留你的配置（向导里也可随时选 No），报表命令完全不受影响。注意：不接管时 `tt status` 的 CC 订阅额度段将没有数据来源（CC 配额只经状态栏脚本落盘）。

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

- **L1** `[项目](分支 +A -D) | Total: <会话累计 token> | Model: <模型 推理强度>` —— Total 橙、Model 红；第三方 API provider（如 DeepSeek）无订阅配额，L1 加显示会话 Cost（内置官方定价估算）
- **L2** `Limit: 5h <进度条> % (reset <倒计时>) | 7d <进度条> % (reset <倒计时>) | <窗口> Ctx <进度条> %` —— 配额按当前会话 / 同 model_provider 取数，多账号多 provider 混跑不串数据；无配额数据时不挂 `Limit:` 前缀

渲染 24-bit 真彩色、**不进模型上下文**（实测），**配色跟随当前主题**（与 CLI 报表 / CC 状态栏同源，`tt theme` 切换三者一起变）。`tt unsetup` 一并移除。

### Kimi Code（官方接口）

基于 Kimi Code 官方 `status_line` 接口（`tui.toml`），单行真彩色状态栏：

`[项目](分支* +A -D ?U) | Total: 21.2M | Cost: $9.08 | 5h: 18% | 7d: 15% | Model: K3/high/auto`（与 CC 状态栏同风格；5h/7d 限额走云端 `/usages` 缓存，每 2 分钟后台刷新一次）

- 项目 / 分支 / 模型 / 权限模式来自 Kimi 官方快照（快照无思考档位，effort 取自会话 wire 里实际请求的 `thinkingEffort`），分支段的未提交增删行 / 未跟踪数按 `git diff --numstat` + `git ls-files` 统计（同 CC 状态栏）；Total 与 Cost 由状态栏脚本按会话 `wire.jsonl` **增量**累计（offset 缓存，每秒级调用只读新增部分），成本按内置 Kimi 官方定价估算
- macOS / Linux / Windows 全平台支持（Windows 控制台 GBK 编码、后台刷新进程 detach 方式均已适配）
- 已有自定义 `status_line.command` 时默认不覆盖（向导里选 No 也完全不碰）；`tt unsetup` 精确还原

## 报表速览

`tt status` — 今日实时面板（今日合并概览 + 5h/7d 额度 + 今日会话）

![Status](assets/screenshot.png)

`tt weekly` — 周报：本周分析卡片 + 每日趋势柱状图 + 周 / 项目 / 模型趋势

![Weekly](assets/screenshot-weekly.png)

`tt monthly` — 月报：本月分析卡片 + 周柱状图 + 月趋势 + 项目 / 模型分布

![Monthly](assets/screenshot-monthly.png)

`tt sessions` — 最近 20 条会话明细（按 cost 倒序，支持 `--sort` 改字段）

![Sessions](assets/screenshot-sessions.png)

## 实时侧边栏（`tt sidebar`）

在终端分屏 / tmux 窄窗格里常驻，一屏总览本机所有 AI 会话：

- **活跃会话列表** — 过去 5h 内有动静的会话（Claude Code + Codex + Kimi Code），按最近活动排序取前 10；头行显示状态灯、`项目名(分支)`、agent、模型与距上次活动时间
- **三地时钟** — 北京、洛杉矶、伦敦合并为一行，只显示城市与 `HH:MM`
- **提示词历史** — 运行中、待确认、等输入会话保留最近 5 条，历史最多 1 行、最新最多 2 行；空闲会话只显示最新 1 条，不显示「下一步」
- **状态灯** — 运行中（星形动画）/ 需要关注（工具调用无结果，大概率在等授权）/ 等输入 / 空闲
- **「下一步」建议** — 从 AI 最后一条回复的收尾段提取征询 / 待办（纯规则，不走模型）；AI 正在用 AskUserQuestion 提问时直接显示问题与选项
- **点击跳转** — 点会话头行直接切换到它所在的 iTerm2 / tmux 窗格（需启用对应 Agent 的状态栏组件提供映射；Codex 在下一次回答完成后建立映射；iTerm2 首次点击会弹 macOS 自动化授权，属预期）

操作：鼠标拖拽选择文字，松开后自动复制；滚轮 / 方向键 / PgUp/PgDn 滚动，`q` / `Esc` / `Ctrl+C` 退出；`tt sidebar --once` 打印一帧快照即退。数据每 5s 刷新，只读本地会话记录、不写任何产物；加 `--claude` / `--codex` 可只看单个 agent。

### Codex 当前会话自动 1/3 分屏（`$tt-sidebar`）

`tt setup` 会把 `$tt-sidebar` 安装为用户级 Codex Skill。它与普通 `tt sidebar` 相互独立：普通命令继续显示全部活跃会话；Skill 只显示发起命令的当前 Codex 会话，右侧自动占 1/3 宽度，完整提示词按时间倒序展示，最新永远置顶。

在 Codex 输入：

```text
$tt-sidebar
```

- 支持 iTerm2、Ghostty（≥ 1.3.0，macOS）与 tmux；iTerm2 无需启用 Python API。首次使用会先出现 Codex 的沙箱外执行确认，随后 macOS 可能再请求「自动化」授权，请允许 Token Tracker 控制 iTerm2 / Ghostty；两次确认均属预期，后续可复用授权。原会话窗格保持焦点。iTerm2 原生全屏会拒绝 AppleScript 调整列宽，需先退出全屏再执行。
- `tt setup` 把 Codex 的伪 statusline `Stop` 与 sidebar `UserPromptSubmit` 统一安装到用户级 `hooks.json`；后者用本地 FIFO 把新提示词推给已打开的分屏，无 sidebar 时立即返回，不轮询 transcript、不上传或持久化提示词。
- Codex 会要求审查非托管 Hook：安装后运行 `/hooks`，信任 Token Tracker 对应项。Skill 未立即出现时重启 Codex。
- `tt unsetup` 会一并移除 Token Tracker 管理的 Skill 与 Hook；若 `~/.agents/skills/tt-sidebar` 已是用户自己的同名 Skill，安装与卸载都不会覆盖它。

### Kimi Code 当前会话自动 1/3 分屏（`/skill:tt-sidebar`）

`tt setup` 同样会把 `tt-sidebar` 安装为 Kimi Code 用户级 Skill（`~/.kimi-code/skills/tt-sidebar`），并在 `~/.kimi-code/config.toml` 追加一条 `UserPromptSubmit` hook（只追加 / 替换 tt 自己的托管块，用户其它配置原样保留）。行为与 Codex 版一致：当前会话右侧 1/3 宽度分屏，完整提示词实时倒序展示。

在 Kimi Code 输入：

```text
/skill:tt-sidebar
```

- 支持 iTerm2、Ghostty（≥ 1.3.0，macOS）与 tmux；iTerm2 / Ghostty 的 macOS「自动化」授权提示属预期。Kimi 会话内没有会话 ID 环境变量，启动器按「`cwd`（兼容旧版 `workDir`）等于当前目录、`updatedAt` 最新」定位当前会话。
- hook 同样走本地 FIFO 推送，无 sidebar 时立即返回；新会话生效，`tt unsetup` 一并移除。

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

脚本自动选最优安装方式（uv / pipx / 私有 venv），绕开 PEP 668、不污染系统 Python。

> **升级**：重跑上面的命令即可（脚本幂等、自动升到最新）。包含新 Agent 集成的版本升级后，再运行一次 `tt setup`；例如 `$tt-sidebar` 需要由 setup 安装到 Codex 用户级 Skill 目录。
> **卸载**：`tt unsetup`

**升级后 `tt --version` 还是旧版？** 多半是旧版装在别的 Python 环境里遮蔽了新版（常见于 Windows、或早期用 `pip install` 装过）。卸载旧版后重装一次即可：

```bash
pip uninstall token-tracker
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

## 使用

```bash
tt setup          # 配置状态栏，并安装 Codex / Kimi Code 的 tt-sidebar Skill / 提示词 Hook
tt                # 过去一年 token 热力图 + 顶部三段概览（= tt daily）
tt daily          # 同上（tt 无参即进 daily）
tt status         # 今日消耗、5h/7d 额度与今日会话
tt weekly         # 周报
tt monthly        # 月报
tt sessions       # 最近 20 条会话明细（tt sessions <正整数> 改条数、--sort 改排序）
tt sidebar        # 常驻侧边栏：活跃会话总览 + 提示词历史 + 状态灯 + 点击跳转（--once 一帧快照）
tt theme          # 查看 / 切换配色主题（show / list / set / preview）
tt unsetup        # 卸载并恢复安装前的配置
tt --version      # 查看版本（-v / -V 同义）
```

> 多 agent 环境下想只看某一个 agent 的报表，加 `--claude` / `--codex` / `--kimi` 即可（互斥），对 `status` / `daily` / `weekly` / `monthly` / `sessions` 均生效。例如 `tt daily --kimi` 只显示 Kimi Code 的热力图。会话内的 `daily` / `weekly` 默认已自动跟随当前会话的 agent，显式 flag 会覆盖该行为。

> 💡 `tt daily` 是 GitHub 风格的 token 贡献热力图（深浅绿方格）。在 Claude Code 会话里输入 `!tt daily` 即可看到彩色热力图 —— 用户主动用 `!` 执行的命令，Claude Code 会渲染其 24-bit 真彩色输出。

## 配色主题

内置 6 套主题，CLI 报表与各 Agent 状态栏（CC / Codex / Kimi Code）**统一同源**（切主题一起变）：

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

## 高级

### 首次运行向导

第一次跑 `tt`（或在独立终端跑 `tt setup`）会进入**交互式配置向导**，全程上下键选 + 回车确认：

1. **选语言** — 中文 / English（落 `~/.config/token-tracker/config.json`）
2. **选配色主题** — 6 套主题上下键选择，每个选项右侧内联色板预览
3. **接管 Claude Code 状态栏** — Yes/No（仅检测到 Claude Code 时；已有自定义 statusLine 会先备份、选 No 完全不碰）
4. **启用 Codex 伪 statusline** — Yes/No（仅检测到 Codex 时）
5. **启用 Kimi Code 状态栏** — Yes/No（仅检测到 Kimi Code 时；已有自定义 `status_line.command` 默认不覆盖）

CI / 非 tty 环境（Docker / 脚本 / `curl|bash`）自动按**推荐默认**配置：语言跟随系统设置、主题 mocha、组件默认开启但**不替换已有自定义 statusLine**。装好后想改任何一项，再跑一次 `tt setup` 即可。

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
| Kimi Code | `~/.kimi-code/sessions/` | wire JSONL（每 turn 增量） |

路径跨平台：Windows 下 `~` 解析到 `%USERPROFILE%`。设了 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `KIMI_CODE_HOME` 环境变量（官方支持的自定义目录）时自动跟随。

Token Tracker 对 Agent 数据**只读**，不做任何修改。

## 环境要求

- Python 3.11+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## License

Copyright (c) 2026 stormzhang. MIT License.
