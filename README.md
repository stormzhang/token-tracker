# Token Tracker

CLI dashboard to track token usage across local AI agents.

Supports **Claude Code** and **Codex** — see how many tokens you burn, what it costs, and how close you are to rate limits.

![Token Tracker Dashboard](https://img.shields.io/badge/python-3.12+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Multi-agent tracking** — Claude Code + Codex in one place, interactive tab switching
- **Rate limit monitoring** — real-time 5h / 7d quota usage with reset countdown
- **Cost analysis** — per-session, daily, weekly, monthly cost breakdown (LiteLLM pricing)
- **Session insights** — project, model, duration, message count per session
- **5h billing block analysis** — burn rate, active/idle detection
- **Zero config** — auto-detects installed agents, reads local data directly

## Install

```bash
pip install git+https://github.com/stormzhang/token-tracker.git
```

## Usage

```bash
tt                # interactive dashboard (arrow keys to switch agents)
tt claude         # Claude Code only
tt codex          # Codex only
tt daily          # daily breakdown
tt weekly         # weekly breakdown
tt monthly        # monthly breakdown
tt sessions       # recent sessions list
tt blocks         # 5h billing block analysis
```

## Data Sources

| Agent | Path | Format |
|-------|------|--------|
| Claude Code | `~/.claude/projects/*/` | JSONL (per-message usage) |
| Codex | `~/.codex/sessions/` | JSONL + SQLite |

Token Tracker is **read-only** — it never modifies any agent data.

## Requirements

- Python 3.12+
- [Rich](https://github.com/Textualize/rich) (auto-installed)

## License

MIT
