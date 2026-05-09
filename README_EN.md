# Token Tracker

CLI dashboard to track token usage across local AI agents.

Supports **Claude Code** and **Codex** — see how many tokens you burn, what it costs, and how close you are to rate limits.

![Python](https://img.shields.io/badge/python-3.12+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

[中文](README.md)

![Token Tracker Dashboard](screenshot.png)

## Features

- **Multi-agent tracking** — Claude Code + Codex in one place, interactive tab switching
- **Rate limit monitoring** — real-time 5h / 7d quota usage with reset countdown
- **Cost analysis** — per-session, daily, weekly, monthly cost breakdown (LiteLLM pricing)
- **Session insights** — project, model, duration, message count per session
- **5h billing block analysis** — burn rate, active/idle detection
- **Zero config** — auto-detects installed agents, reads local data directly

## Install

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/master/install.sh | bash
```

Or via pip:

```bash
pip install token-tracker
```

## Usage

```bash
tt                # interactive dashboard (arrow keys to switch agents)
tt status         # one-line status output (for tmux / prompt)
tt status claude  # Claude Code status only
tt status codex   # Codex status only
tt status --format plain  # compact output without progress bars
tt status --no-color  # disable colors for prompt / tmux
tt claude         # Claude Code only
tt codex          # Codex only
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
