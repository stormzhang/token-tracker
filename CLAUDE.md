# Token Tracker

本地 AI Agent Token 消耗追踪工具。

## 技术栈

- Python 3.12+
- Rich（CLI 表格渲染）
- 无其他外部依赖（标准库处理 JSON/SQLite/HTTP）

## 项目结构

```
src/
├── cli.py              # CLI 入口，命令路由
├── adapters/           # 数据源适配器
│   ├── types.py        # 统一数据模型
│   ├── claude.py       # Claude Code JSONL 解析
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
python -m src.cli daily
python -m src.cli monthly
python -m src.cli sessions
python -m src.cli blocks
```

## 开发规范

- 数据源只读，不修改任何 Agent 的本地文件
- 去重：message_id + request_id
- 成本计算：4 种 token 分别定价（input / output / cache_creation / cache_read）
- 定价数据缓存在项目根目录 pricing_cache.json
