# token-tracker ROADMAP

> 当前状态以本文件为准；历史过程见已有证据与 Git。历史验证不代表当前全量通过，旧待办不构成执行授权。

## 当前状态

最新发布记录为 2026-09-28 的 0.5.8，源码 f8e572c 与 tag 已推送，包含 Codex Hook 纯文本、新模型定价／命名空间及 Windows Kimi 命令兼容。发布包、隔离安装与 CI 已在该日核对；用户级工具未自动升级，Windows 真机限制沿用已有记录。

2026-10-10 定价校准已实现并验证，0.5.9 版本与锁文件已同步，wheel／sdist、Twine 校验及隔离安装检查完成，正在发布：补 GPT-6.1 Sol、Claude Sonnet／Haiku 5.5、Mythos 5.1、GLM 5.2／5.3 各档、MiMo V2.6、Qwen Plus／Coder 与 Gemini Flash 离线价，修复 LiteLLM 阶梯表归零、MiniMax 长上下文／highspeed 错档、DeepSeek 2026 年节假日谷价；Gemini 已公告的 2027 年涨价按请求时间处理。详细口径与官方来源见[手册](docs/agent-handbook.md)，新回归见[定价更新测试](tests/test_pricing_updates.py)。

已有报表、三 Agent 状态栏与 sidebar 能力见 [README.md](README.md)，技术事实和计价边界见 [手册](docs/agent-handbook.md)。报价是按已核对来源的估算，不以旧发布记录证明当前外部价格。历史版本以 Git／PyPI 发布记录为准，当前待办只保留下节未实现事项。

## 待办 / 计划

- **Codex 非标准服务档计价（待确认日志字段）**：Astra／GPT-6.1 Sol 官方 Fast 为适用标准价的 2 倍，Flex 为 0.5 倍；GPT-6.1 Sol 另有 6 倍 Ultrafast。此前抽查的 3 个真实 Astra 会话无可核实的 `service_tier`，当前继续按标准价估算。后续以逐请求实际生效档位为准，不从当前用户配置或模型名推断整段历史的倍率。
- **外部定价维护（2026-10-10 核对）**：DeepSeek 峰谷价已覆盖生效后的 2026 年中秋／国庆，后续年份放假安排需待官方公布后补充，未收录年份仅按周末／时段估算。Qwen3.7 Plus 裸 alias 当前为 8 折但官方页未给出截止日，需后续复核；Gemini 3.6／3.8 Flash 已实现 2027-01-01 UTC 切价，届时仍需复核官方是否延期或变更。已知促销不凭模型名反推历史起始日。
- **`tt sidebar` 点击跳转补 Ghostty（未启动）**：自动分屏已支持 Ghostty（2026-08-03），但总览「点头行跳窗格」仍只有 tmux（`TMUX_PANE` 映射）/ iTerm2（`ITERM_SESSION_ID` 映射）----三套 statusline 模板只采集这两类，`ui/sidebar_app._jump_argvs` 也只认这两类，Ghostty 会话头行当前不可点。Ghostty 无 per-pane 环境变量，statusline 渲染期不宜调 osascript（300ms 预算）；可行路径是点击时懒算：AppleScript `every terminal whose working directory is <会话 cwd>` 匹配后 `focus`（多窗格同项目时有歧义，需定优先级，如取 front window 首个匹配），或推动 Ghostty 暴露 surface id 环境变量后再走精确映射。README 对点击跳转的「iTerm2 / tmux」描述在补齐前保持现状（是准确描述，非遗漏）。
- **自动 1/3 分屏跟随原会话退出（独立后续，未实现）**：不监听 `/quit` 文本，改由 `$tt-sidebar` launcher 沿父进程树定位原生 Codex PID 并传给 split；macOS 用 `kqueue` `EVFILT_PROC + NOTE_EXIT`、Linux 用 `pidfd` 阻塞等待真实进程退出，收到事件后退出 Textual 并关闭配对 pane。该路径应覆盖 `/quit`、`/exit`、崩溃和原 pane 关闭，不增加 transcript / SQLite 监听或周期 timer；iTerm2 `jobName` 变量监听只作终端专属备用，shell wrapper 需改变启动方式，均不作为主实现。Claude Code 继续优先使用官方 `SessionEnd`，强杀再走同类进程兜底。
- **GitHub issue / PR 状态重新核对**：本地确认 #16 / #17 / #19 对应功能已经落地；外部 open/closed 状态在实际处理前重新查询，不沿用 2026-07-04 的旧快照。
- 桌面版（Tauri）规划：图表可视化、数据钻取、实时监控、多 Agent 多模型监控（仅规划，未启动）
- **会话内彩色报表 hook -- 桌面多形态适配**（终端部分已落地，见「进行中」）：桌面 app / web GUI 不吃 ANSI（实测乱码），需按形态输出 HTML/markdown（`tt` 加 `--format`、hook 检测形态）。详见本地 `docs/cc-hook-tt-真彩色.md`。
- **Sidebar 状态引擎 v2（独立迭代）**：把普通 `tt sidebar` 的状态判断从当前 `_infer_state` 时间窗口 + 单个 `pending_tool` 布尔值，升级为「生命周期事件为主、registry / rollout 快照恢复、现有启发式兜底」的每会话状态机；自动 1/3 分屏继续保持纯提示词视图，不显示状态或当前动作，也不与本迭代混做。
  - **Claude Code 数据源**：前台会话优先消费 `UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse` / `PostToolUseFailure`、`Stop`、`StopFailure`、`SessionEnd`，用 `tool_name` / `tool_input` / `tool_use_id` 补充当前动作；启动或 sidebar 重启时读取 `~/.claude/sessions/<pid>.json` 的 `status`、`statusUpdatedAt`、`waitingFor` 恢复快照。该 registry 是本机 Claude Code 2.1.207 的内部接口，字段缺失或版本变化时必须防御式降级；`claude agents --json` 面向 Agent View / 后台会话，普通前台会话未 background 前不能作为状态真相。
  - **Codex 数据源**：当前普通 TUI 以 `UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`Stop` hooks 为主，rollout 的 `task_started` / `task_complete` / `turn_aborted` 与带 `turn_id` / `call_id` 的调用、输出配对用于启动恢复和漏事件兜底；`PermissionRequest` 才能精确标记「等授权」，未完成工具不得继续直接等同「待确认」。Codex hooks 暂无 `SessionEnd`，`/quit` / `/exit` 也不会进入 `UserPromptSubmit`；普通总览关闭态继续用进程探活 / TTL 兜底，自动分屏的即时跟随退出按上方独立事项处理。
  - **App Server 后续选项**：协议已提供 `thread/status/changed`、`activeFlags=[waitingOnApproval|waitingOnUserInput]`、`turn/*`、`item/*` 及审批 / 用户输入请求，运行状态精度最高；但需要先启动 App Server，再用 `codex --remote ...` 承载 TUI，不能事后附着普通已运行会话。远程 TUI 执行 `/quit` 只会 `thread/unsubscribe`，最后订阅者离开后默认空闲 30 分钟才发 `thread/closed` 与 `notLoaded`，因此不能替代即时的进程退出监测；仍只作为单独启动架构升级评估，不纳入 v2 首期。
  - **统一状态与工程约束**：目标状态为 `thinking`、`tool_running`、`waiting_approval`、`waiting_input`、`idle`、`closed`、`error`，冲突优先级为「等授权 > 等输入 > 工具运行 > 思考 / 生成 > 空闲 / 关闭」；事件按 `session_id + turn_id + tool_use_id / call_id` 配对，并行工具必须用集合而非单布尔值。hooks 是边沿事件而非查询 API，状态需持久化或可由 registry / rollout 重建；展示工具参数前必须脱敏并截断。
  - **验收标准**：长命令不再误报「待确认」；`PermissionRequest` 到达后立即、准确进入等授权；并行工具完成一个时不得提前切到等输入；Claude Code `SessionEnd` 后及时移除，Codex 异常退出由兜底可靠收敛；sidebar 重启后能恢复正确状态；旧版本、hook 缺失、事件乱序或进程崩溃时不崩溃并安全降级。
- **远程下发通知通道**（📝 规划，新分支 `feature/broadcast` 做）：纯下行----读本地 `~/.claude/tt-broadcast.json`，在 **statusline 第 5 行**（未过期里优先级最高一条、按 level 着色）+ **`tt status` 头图上方横幅**（全部未过期）展示远程下发的公告/告警/进度；level 分流 `urgent`/`info`/`progress` + `expires_at` 自动过期；配 `tt broadcast` 写入命令自测。**只接收不上传、不碰隐私定位**。同步链路（远程→文件）+ 排行榜 PK（需上行、必 opt-in + 脱敏）留后续。详见 `docs/broadcast-plan.md`。

## 阻塞

- 纯 osascript 无法在 iTerm2 原生全屏下调整 pane 列宽；当前安全回滚并提示退出全屏，若以后要求原生全屏 1/3，需重新评估 Python API fallback 或 macOS Accessibility 方案。

## 最近完成

- 2026-10-10 17:12 新模型定价与计价边界更新（已实现验证，待发布）：新增 19 个内置模型／快档／日期版报价并补齐相关报表短名，保留 Opus 5／5.5、Sonnet 5、Haiku 4.5、Mythos 5 等精确价；补 Haiku 100K、GPT-6.1 272K、MiniMax M3 512K、Qwen 32K／128K／256K 阶梯，兼容 LiteLLM `tiered_pricing`，DeepSeek 纳入 2026 年中秋／国庆谷价，Gemini 按请求时间切换已公告新价。证据：[实现](src/token_tracker/analyzer/cost.py)、[回归](tests/test_pricing_updates.py)、[官方依据与限制](docs/agent-handbook.md)。
- 2026-10-03 09:55 规范与进度文档精简：稳定边界保留于 AGENTS.md 与对应手册，ROADMAP 收敛当前状态及最近记录；本次未修改业务实现，未重跑历史运行验收。证据：[项目规范](AGENTS.md)。
- 2026-09-28 14:42 `0.5.8` 已发布 PyPI（源码与 tag 已 push）：包含 Codex Hook 纯文本兼容、新模型定价与 GPT 命名空间识别，以及 Windows Kimi 状态栏命令修复；版本与锁文件、README 中英文已同步；发布 commit `f8e572c` 与 annotated tag `v0.5.8` 已推送；完整 pytest 与英文 dumb terminal pytest 各 456 passed，Ruff、mypy（41 个源文件）、锁文件与 diff 检查通过。证据：[GitHub CI](https://github.com/stormzhang/token-tracker/actions/runs/36387698193)。
- 2026-09-26 18:25 Codex Hook ANSI 乱码兼容修复已随 0.5.8 发布：Codex 0.156.0 起过滤 Hook 消息中的控制字符，旧版彩色 `systemMessage` 的 ESC 被删除后留下 `[38；2；…m` 裸码（[上游变更](https://github.com/openai/codex/pull/46710)）；Codex 伪 statusline 改为两行纯文本，保留项目、token、成本、模型、额度与进度条；本地用户级脚本未覆盖。证据：[38;2;…m` 裸码（[上游变更](https://github.com/openai/codex/pull/46710)。
- 2026-09-23 13:00 新模型定价适配已随 0.5.8 发布：GPT-6 Sol / Luna 增加标准价与单次请求 >272K 阶梯价，Opus 5.5 独立降价且保留 Opus 5 历史价，Grok 4.7 补 200K 阶梯价；DeepSeek `deepseek-flash` 与两个 V4 Flash 旧 ID 从 2026-09-10 04:00 UTC 起按 V4.1 Flash 新峰谷价估算，之前的请求与 V4 Pro 保持原价。
- 2026-09-10 00:09 GPT 模型命名空间识别修复已随 0.5.8 发布：`chatgpt/gpt-5.6-sol` 原先无法匹配已有定价并按 $0 计；现支持 `chatgpt/gpt-*`、`openai/gpt-*` 缺少独立报价时复用裸模型解析，日期后缀和长上下文阶梯价保持一致；完整 ID 及其变体报价优先，完整 ID 精确价也优先于已缓存的裸模型兜底；未知第三方、嵌套前缀和非 GPT 模型不剥除。
- 2026-09-09 19:55 `0.5.7` 已发布 PyPI（源码与 tag 已 push）：包含 Astra / Fable 5.1 定价、Codex 缓存写入计价、此前模型价格校准与扫描性能优化；发布 commit `30a7892`、annotated tag `v0.5.7` 已推送；完整 pytest 与英文 dumb terminal 各 395 passed，Ruff、mypy（41 个源文件）、锁文件和 diff 检查通过。
- 2026-09-09 17:52 GPT-6 Astra / Claude Fable 5.1 适配已随 0.5.7 发布：新增 Astra 标准内置价与 >272K 输入阶梯价，离线／旧缓存不再缺价；Fable 5.1 缓存读取按 $0.25/MTok，保留 Fable 5 / Mythos 5 的 $1/MTok 历史价，更新 Fable 系列兜底并补两款显示名；Codex 从累计和逐请求用量读取 `cache_write_input_tokens`，由普通输入扣除后独立计价，总 token 数不变。证据：[agent-handbook.md](docs/agent-handbook.md)。
- 2026-08-31 20:08 代码性能优化已随 0.5.7 发布（ 完成）：daily/monthly 日期键移除高频 `strftime`，16,508 条 Kimi 真实数据聚合从约 0.71s/0.68s 降至 0.033s/0.027s；Codex 状态栏改由 adapter 单次扫描同时产出元数据、限额和逐请求计价 entry，200MB 真实会话从原 2～3 遍约 0.56～0.84s 收敛为单遍 0.31s，`STATUSLINE_HOOK_VERSION` 升至 1.8。
- 2026-08-30 10:55 模型定价全面校准已随 0.5.7 发布（ 完成）：按最新官方页更新 GPT-5.6 Sol 促销价与三档 >272K 阶梯价、Claude Mythos 5、GLM-5.1、Qwen3-Coder-Next、Doubao Seed 2.0 Code／2.1 Pro、DeepSeek V4 峰谷价、Grok 4.3／4.5／4.6 与 Build 长上下文价，并补 Gemini 3.6／3.7、GLM-5.3 等短名。
- 2026-08-30 03:21 已完成：项目级约束统一以 `AGENTS.md` 为唯一内容源，`CLAUDE.md` 收敛为 AgentSync 兼容入口。
- 2026-07-22 09:40 Codex `$tt-sidebar` iTerm2 沙箱授权修复已随 0.4.11 发布（）：同一份 0.4.10 AppleScript 在 Codex 普通沙箱内因 iTerm2 术语字典不可见而误报 `-2741`，沙箱外纯编译成功，确认不是脚本语法或 macOS Automation 授权问题；发行 Skill 现要求 iTerm2 路径直接以 `sandbox_permissions=require_escalated` 执行 launcher，可记忆授权仅限定完整 launcher 命令、不得放宽到 Python 通用前缀。

## 最近验证

- 2026-10-10 19:33 0.5.9 发布准备：完整 pytest 与英文 dumb terminal pytest 独立串行各 564 passed，Ruff、mypy（41 个源文件）、锁文件与 diff 检查通过；wheel／sdist 通过 Twine 校验，44 个包文件与源码逐字节一致，未混入本地品牌素材、缓存或凭据；隔离 wheel 的版本、导入、两款 Skill、三份 Hook 模板编译与新模型报价检查通过。额外尝试不支持的 `--help` 时触发既有自动 Hook 更新，写入被沙箱拦截，后续仅运行只读检查；未升级用户级安装或修改 Hook 配置。GitHub／PyPI 远端结果待发布后核对。
- 2026-10-10 17:12 定价更新验证：完整 pytest 与英文 dumb terminal pytest 各 564 passed，定价相关测试 236 passed；Ruff、mypy（41 个源文件）、锁文件与 diff 检查通过。最新 LiteLLM 表只读实取后校验 8 个新模型样例及 2 个 Qwen provider 阶梯样例，成本与预期一致且 Qwen 不再归零；离线旧缓存、缓存读写参与上下文阈值、逐请求 segment／不完整降级、历史模型精确价、节假日／调休周末与切价时刻均有回归。英文测试首次受沙箱 `ps` 限制，沙箱外复跑全过；未改用户级缓存、hooks 或安装。证据：[回归](tests/test_pricing_updates.py)。
- 2026-10-03 10:08 文档检查：本项目受影响规范、文档引用、代码围栏、最近记录数量与时间顺序、Git diff 空白检查通过；全工作区 AGENTS 预算及 CLAUDE 兼容入口检查通过。入口：[项目规范](AGENTS.md)。本次未重跑历史业务验收。Markdown 渲染解析通过，Mermaid 示例未产生注释包裹的图源码。
- 2026-09-28 14:42 0.5.8 发布与远端回验完成；完整 pytest、英文 dumb terminal pytest 独立串行各 456 passed，Ruff / mypy / 锁文件 / diff 检查通过；首次并发测试发生同名 FIFO 冲突，独立复跑两套均通过，未改测试或业务逻辑；Twine check、44 个包文件与发布提交逐字节检查通过，产物未混入品牌草稿或临时文件；远端 main 与 `v0.5.8` 均指向发布提交 `f8e572c`。证据：[GitHub CI](https://github.com/stormzhang/token-tracker/actions/runs/36387698193)。
- 2026-09-26 18:25 Codex Hook 纯文本输出；生成脚本及 DeepSeek 会话端到端输出不含 ANSI，项目、进度条和成本字段回归通过；完整 pytest 与英文 dumb terminal pytest 均通过，Ruff、mypy（41 个源文件）、`git diff --check` 通过；英文测试首次因沙箱阻止只读 `ps` 失败，获准后复跑通过；未改本机安装或 Codex 配置。
- 2026-09-23 13:00 新模型定价与显示适配；官方模型页与定价页逐字段核对 Sol / Luna / Opus 5.5 / Grok 4.7、DeepSeek V4.1 Flash；旧缓存注入、272K / 200K 门槛、日期后缀、Opus 5 历史价、DeepSeek 切价时刻和逐请求峰谷用例通过；完整 pytest 与英文 dumb terminal 各 455 passed，Ruff、mypy 41 个源文件、`uv lock --check`、`git diff --check` 通过。
- 2026-09-17 20:20 修复 Windows 下 Kimi 状态栏静默失败（cmd /s 引号问题）；根因：`_kimi_statusline_command()` 复用 CC 的 `"{python}" "{script}"` 引号写法，而 Kimi Code 在 Windows 经 `cmd.exe /d /s /c` 执行（已在 kimi 0.43.1 二进制中核实 spawn 参数），双引号被视作程序名一部分导致命令失败、回退内置 footer，且 `update_hook()` 会覆盖用户手工修复。
- 2026-09-10 00:09 GPT 命名空间定价修复；先复现 `chatgpt/`、`openai/` 前缀导致定价归零，再补最小解析规则；新增 21 项回归覆盖两个前缀、Sol / Astra、日期后缀、272K 上下界、完整 ID 报价优先、缓存兜底后新增精确价，以及未知／嵌套／空前缀边界；完整 pytest 和英文 dumb terminal 各 416 passed，Ruff 全过、mypy 41 个源文件无错误、`git diff --check` 通过。
- 2026-09-09 19:55 0.5.7 打包、发布与远端回验完成；版本与锁文件一致；完整测试和英文 dumb terminal 各 395 passed，Ruff / mypy / `uv lock --check` / diff 检查通过；sdist / wheel 经 Twine 校验，包文件与发布提交一致，未混入本地品牌素材或临时文件；远端 main 和 tag 目标已核对；PyPI 两份产物元数据及下载字节的 SHA-256 与本地一致。
- 2026-09-09 17:52 Astra / Fable 5.1 定价与 Codex 缓存写入适配；完整 pytest 与 `LANG=C LC_ALL=C TERM=dumb` 各 395 passed（新增 28 项参数化回归），Ruff 全过、mypy 41 个源文件无错误、`git diff --check` 通过；覆盖旧缓存／断网、272K 边界、日期后缀、历史价隔离、按请求而非会话累计套档、缺失／非法缓存写入和状态栏回退。
- 2026-08-31 20:08 代码性能优化与 Kimi 裸安装检测修复完成；真实数据基准：16,508 条 Kimi daily/monthly 聚合约 0.71s/0.68s → 0.033s/0.027s；200MB Codex 当前会话状态栏解析由 2～3 遍收敛为单遍 0.31s；三 Agent 默认 `tt sessions 20` 在约 6.5GB 历史下由全量 10.46s 降至 0.80s，前 20 条 session id 逐项一致，并补近期窗口完整会话、长会话扩窗、候选不足全量回退、状态栏单扫描和 Kimi 根目录检测回归。
- 2026-08-30 10:55 模型定价全面校准与动态计价落地（已实现验证，待发版）；官方页确认 DeepSeek 新价自 2026-08-16 16:00 UTC 生效，仅周一至周五 UTC 01:00-04:00、06:00-10:00 为峰时，周末全天谷价；同步更新 GPT-5.6、Claude Mythos／Sonnet、GLM、Qwen、Doubao、Grok 等最新价与模型识别；计价器支持按单次请求选择峰谷／长上下文档，Codex 从 `last_token_usage` 保存逐请求 segment，状态栏同口径并升 1.7。
- 2026-08-25 21:57 版本升至 0.5.6 并完成 GitHub / PyPI 发布与远端回验；`pyproject.toml` / `uv.lock` 同步 0.5.6，release commit `fb27d57` 与 annotated tag `v0.5.6` 已 push；发布前完整 pytest 与英文 dumb terminal CI 模拟均 344 全绿，Ruff 全过、mypy 41 个源文件 0 报错。
- 2026-08-25 21:54 Codex sidebar 注入噪声收口：skip 前缀补三类 + `next_hint` 过滤 raw JSON（已实现验证，未发版）；0.5.5 真机扫描（120h / 54 会话）复核发现自动审批工具（codex-auto-review 类）会话残留三类注入：`The following is the Codex agent history…` 与 `>>> TRANSCRIPT START/DELTA` 段（event 通道本身携带、旧版照收）、goal 模式 `<codex_internal_context source="goal">`。
- 2026-08-25 20:56 版本升至 0.5.5 并完成 GitHub / PyPI 发布与远端回验；`pyproject.toml` / `uv.lock` 同步 0.5.5，release commit `a7ab053` 与 annotated tag `v0.5.5` 已 push；发布前完整 pytest 与英文 dumb terminal CI 模拟均 342 全绿，Ruff 全过、mypy 41 个源文件 0 报错，`uv lock --check` 与 `git diff --check` 通过。
- 2026-08-25 20:19 修复 `response_item` 兼容改动的三个真机 bug（已实现验证，未 commit / 未发版）；上一条（18:15）的兼容改动方向正确，但用本机真实 rollout 复测暴露：① 双写文件（`event_msg` 与 `response_item` 各记一份）同一提示词被收两次（实测「你好 ×2」等成对重复）；② `<recommended_plugins>` 注入前缀未过滤、混进提示词首位。
- 2026-08-25 18:15 修复 Codex 新版 rollout 导致 `$tt-sidebar` 不显示提示词（已实现验证，待同步用户级工具）；根因是新版 Codex 将用户与助手消息记录为 `response_item/message`，旧解析器仅识别 `event_msg/user_message`；现已兼容两种格式并新增回归测试；真实当前会话解析出 28 条提示词，最新一条为「去做哟」。
- 2026-08-24 21:06 Ghostty 分屏加前置探针，精确区分 Codex 沙箱与版本过低（已实现验证，未 commit / 未发版）；痛点：Codex 沙箱内整段 AppleScript 在编译期死于 -2741（读不到 Ghostty 术语字典），脚本内的运行时版本检查根本没机会执行，-2741 只能把「沙箱限制」与「版本低于 1.3.0」混报成一条文案；修复只动 `ghostty_split.py`：新增 `_probe_ghostty()`，跑整段脚本前先发最小探针 `tell application "Ghostty" to get version`（探针编译同样需加载字典，沙箱下同样 -2741）--2741 直接断定沙箱，提示「当前命令运行在 Codex 沙箱中，请以沙箱外权限（require_escalated）重跑 $tt-sidebar」；探针自身失败（osascript 缺失 / 超时）不阻断主流程，原 `_failure_message` 保留作运行时兜底；新增 6 用例（探针沙箱精确文案 / 精确版本号 / 达标放行 / 探针失败不阻断 / `_version_tuple` / 运行时兜底），替换原沙箱混报用例。
- 2026-08-24 15:35 产品主页三版设计对比稿落盘（候选未定，`homepage.html` 仍为正式版）；基于同一质感层（JetBrains Mono webfont + 终端网格肌理 + spotlight 卡片 + 入场编排 + reduced-motion 全降级）产出：① `homepage-live.html` 活终端首屏 -- `uvx asciinema rec`（隔离环境，未全局安装）真实录制 `tt daily/weekly --mock`（110×32，mock 脱敏数据），cast 内联进页面经 Blob URL 喂给 asciinema-player，绕开 `file://` fetch 限制，双击即播。
- 2026-08-24 14:48 产品主页预览落盘（`assets/brand/homepage.html`，静态预览、细节待主人单独优化）；按定稿品牌（方案 A `tt▌` 光标 + Mauve `#cba6f7`，Catppuccin Mocha 深色锁定）实现：非对称 hero（左文案右 `screenshot-daily.png` 真实截图）、三 Agent 支持徽章条（OpenAI logo CDN 已下架 404，弃用 logo 墙改终端风 mono 徽章）、statusline 截图区、7 格 bento 功能矩阵（4+2 / 2+2+2 / 3+3 节奏，侧边栏与主题两格嵌真实截图）、终端风安装命令块（curl 一键脚本 + `tt setup` + `tt daily`）、页脚 GitHub / PyPI / MIT 链接。证据：[homepage.html](assets/brand/homepage.html)。
