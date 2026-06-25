# Token Tracker

Track token usage across local AI agents. Supports **Claude Code** and **Codex**.

Custom StatusLine integration + CLI Dashboard — see token usage, cost, and rate limits at a glance.

![Python](https://img.shields.io/badge/python-3.11+-blue) ![CI](https://github.com/stormzhang/token-tracker/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/license-MIT-green)

[中文](README.md)

![Token Tracker Daily](assets/screenshot-daily.png)

## Highlights

- **Unified multi-agent tracking** — Claude Code + Codex in one place, grouped by source
- **Status line integration** — Claude Code via official StatusLine API; **Codex industry-first faux statusline** (hook-injected two-line truecolor status — bringing an official-unsupported capability to Codex)
- **Rate limit monitoring** — real-time 5h / 7d quota usage with reset countdown
- **Multi-dimensional cost analysis** — per-session, daily, weekly, monthly cost breakdown
- **Pricing resolution** — litellm live pricing + built-in official-price fallback, covering Claude / OpenAI / Gemini / Grok and major Chinese models (Kimi / GLM / Qwen / Doubao / DeepSeek / MiniMax / MiMo); new family members auto-priced, never silently $0
- **Session insights** — project, model, duration, message count per session
- **Unified multi-theme** — 6 themes (Catppuccin family + Nord + Dracula) shared across CLI reports, the CC status line, and the Codex faux statusline; switch with `tt theme`
- **Zero config** — auto-detects installed agents, reads local data directly
- **Privacy first** — all data stays local, no collection or upload

## StatusLine

`tt setup` auto-configures status lines for Claude Code and Codex, auto-upgraded when the script updates.

### Claude Code (official API)

Built on the Claude Code official custom StatusLine API — **all data comes directly from local Claude, zero guesswork**.

![Claude Code StatusLine](assets/screenshot-statusline-cc.png)

<details>
<summary>Four-row layout field details</summary>

| Row | Field | Description |
|-----|-------|-------------|
| 1 | `[project](branch +12 -3)` | Project name (bold) + Git branch (`*` = uncommitted), with added/removed lines vs HEAD in parens |
| 1 | `Total: 1.2M` | Cumulative tokens consumed this session (input+output+cache, parsed from transcript) |
| 1 | `Cost: $35.51` | Session cost (from Claude Code itself, official billing, accurate) |
| 1 | `Code: +208 -8` | Lines of code written / removed by Claude this session (`+` green `-` red, same as git diff) |
| 2 | `Limit: 5h: ██░ 31% (1h19m)` | 5-hour sliding window quota (subscription only; reset countdown in parens) |
| 2 | `7d: ██░ 11% (5d8h)` | 7-day sliding window quota |
| 2 | `1.0M Ctx: ██░ 20%` | Total context window size and usage percentage |
| 3 | `Tokens: in 392k, out 937, cache 388k` | **Current context window** token breakdown (note: not session cumulative; changes on compact) |
| 3 | `Out TPS: 60 tokens/s` | Current-turn output token generation speed (includes thinking; idle frames keep last value) |
| 4 | `Model: Opus 4.8/xhigh/nofast` | Model / reasoning level / fast mode status |
| 4 | `Duration: 1h33m` | Current session elapsed time |
| 4 | `Remote: github` | Code repository host (top-level domain stripped) |

> When terminal width is limited, the display auto-degrades: first hides reset countdowns, then simplifies progress bars to plain percentages. **API mode** has no subscription quota, so row 2 shows only Ctx.

</details>

### Codex (faux statusline — industry-first)

Codex doesn't yet support custom StatusLine. Token Tracker injects a **faux statusline** via a hook — after each turn completes, two truecolor status lines are appended to the response. **This is a rare implementation that brings a status line to Codex despite no official support.**

![Codex StatusLine](assets/screenshot-statusline-codex.png)

**Two-line layout**:

- **L1** `[project](branch +A -D) | Total: <session tokens> | Model: <model reasoning>` — Total in orange, Model in red
- **L2** `Limit: 5h <bar> % (reset <ttl>) | 7d <bar> % (reset <ttl>) | <window> Ctx <bar> %`

Renders 24-bit truecolor, **does not enter the model context** (verified), and **follows the current theme** (same source as the CLI reports / CC status line; `tt theme` switches all three together). `tt unsetup` removes it.

## Reports at a Glance

`tt status` — last-5h real-time panel (merged overview + 5h/7d quota + recent sessions)

![Status](assets/screenshot.png)

`tt weekly` — weekly report: this-week card + daily-trend bars + weekly / project / model trends

![Weekly](assets/screenshot-weekly.png)

`tt monthly` — monthly report: this-month card + weekly bars + monthly trend + project / model breakdown

![Monthly](assets/screenshot-monthly.png)

`tt sessions` — last 20 sessions sorted by cost (use `--sort` to change field)

![Sessions](assets/screenshot-sessions.png)

## Install

```bash
curl -sSL https://raw.githubusercontent.com/stormzhang/token-tracker/main/install.sh | bash
```

The script auto-picks the best install method (uv / pipx / private venv), sidesteps PEP 668, and never pollutes system Python.

> **Upgrade**: re-run the command above (the script is idempotent and pulls the latest).
> **Uninstall**: `tt unsetup`

## Usage

```bash
tt setup          # interactive setup wizard (terminal: language / theme / components); auto full-install on non-tty
tt                # last-12-months heatmap + top tri-section overview (= tt daily)
tt daily          # same (tt with no args enters daily)
tt status         # last-5h real-time panel
tt weekly         # weekly report
tt monthly        # monthly report
tt sessions       # last 20 session details (tt sessions <n> to change count, --sort to change order)
tt quota          # diagnose third-party quota provider
tt theme          # view / switch color theme (show / list / set / preview)
tt unsetup        # uninstall and restore previous config
tt --version      # show version (-v / -V)
```

> 💡 `tt daily` is a GitHub-style token contribution heatmap (shaded green cells). In a Claude Code session, type `!tt daily` to see it in full color — commands you run yourself with `!` have their 24-bit true-color output rendered.

## Color Themes

6 built-in themes, **shared** across CLI reports, the CC status line, and the Codex faux statusline (switching changes all three):

![Supported themes](assets/screenshot-themes.png)

| Theme | Notes |
|-------|-------|
| `mocha` / `latte` / `frappe` / `macchiato` | Full Catppuccin (mocha/latte auto-picked by dark/light terminal) |
| `nord` | Nord |
| `dracula` | Dracula |

```bash
tt theme               # show current theme and its source
tt theme list          # list all themes with color swatches
tt theme preview nord  # preview a theme (CLI sample + status line sample)
tt theme set nord      # switch theme (persist + re-bake status line)
tt monthly --theme nord  # render any report in a theme temporarily (no persist, status line untouched)
```

- Choice persists to `~/.config/token-tracker/config.json`; priority: `--theme` flag > `TT_THEME` env var > config file > auto.
- Truecolor terminals get exact colors; terminals without truecolor (e.g. macOS Terminal.app) fall back to a **256-color approximation**.

## Third-Party Quota Integration

When using API keys with third-party platforms (OpenCode, etc.), CC won't inject quota data automatically. Token Tracker provides an extensible Provider architecture to fetch plan usage via external scripts.

### Configuration

Configure the quota provider in `~/.claude/tt-config.json`:

```json
{
  "rate_provider": {
    "type": "script",
    "command": "python ~/.claude/tt-opencode-quota.py",
    "cache_ttl": 60,
    "timeout": 10
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `type` | — | Must be `script` |
| `command` | — | Command to execute |
| `cache_ttl` | `60` | Cache TTL in seconds |
| `timeout` | `10` | Script timeout in seconds |

### Script Output Format

The script must output JSON to stdout:

```json
{
  "five_hour": {
    "used_percentage": 31.5,
    "resets_at": 1718457600
  },
  "seven_day": {
    "used_percentage": 12.3,
    "resets_at": 1718889600
  },
  "monthly": {
    "used_percentage": 8.7,
    "resets_at": 1719753600
  },
  "source": "OpenCode"
}
```

All window fields are optional. `source` identifies the provider, displayed in `tt status` and the status line.

### opencode Example

Create `~/.claude/tt-opencode-quota.py`:

```python
#!/usr/bin/env python3
"""OpenCode quota fetcher — GET workspace page, regex-extract usage from inline script."""
import json, re, time, urllib.request, ssl, sys

WORKSPACE_ID = "wrk_xxxxxxxxxxxxxxxxxxxxxxxx"
AUTH_COOKIE = "Fe26.2**xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
PAGE_URL = f"https://opencode.ai/workspace/{WORKSPACE_ID}/go"


def fetch_usage():
    req = urllib.request.Request(PAGE_URL)
    req.add_header("cookie", f"oc_locale=en; auth={AUTH_COOKIE.strip()}")
    req.add_header("user-agent",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


def parse(html):
    secs = re.findall(r"resetInSec:(\d+)", html)
    pcts = re.findall(r"usagePercent:([\d.]+)", html)
    if len(secs) < 3 or len(pcts) < 3:
        raise ValueError("Failed to parse usage fields; cookie may be expired")
    return secs[:3], pcts[:3]


def main():
    now = int(time.time())
    try:
        html = fetch_usage()
        secs, pcts = parse(html)
        rolling_s, weekly_s, monthly_s = (int(s) for s in secs)
        rolling_p, weekly_p, monthly_p = (float(p) for p in pcts)
        print(json.dumps({
            "five_hour": {"used_percentage": round(rolling_p, 1), "resets_at": now + rolling_s},
            "seven_day": {"used_percentage": round(weekly_p, 1), "resets_at": now + weekly_s},
            "monthly": {"used_percentage": round(monthly_p, 1), "resets_at": now + monthly_s},
            "source": "OpenCode",
        }))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

> The `auth` cookie uses Fe26.2 encryption and expires. Re-copy from browser when it stops working.

### Diagnostic Command

Verify the setup with:

```bash
tt quota           # show quota status and provider info
tt quota --debug   # detailed debug output (masked config)
```

Data priority: Official CC quota (subscription) > third-party provider > official metadata only (fallback). When no official quota is detected, the CC status line automatically queries the configured provider.

## Advanced

### First-run wizard

The first time you run `tt` (or run `tt setup` in a standalone terminal), an **interactive wizard** kicks in — arrow keys to move, Enter to confirm:

1. **Pick a language** — 中文 / English (saved to `~/.config/token-tracker/config.json`)
2. **Pick a color theme** — 6 themes with an inline color swatch on each option
3. **Enable Codex faux statusline** — Yes/No (only when Codex is detected)

CI / non-tty environments (Docker / scripts / `curl|bash`) auto-install with defaults: **language follows the system setting**, theme mocha, all components on. To change anything later, just run `tt setup` again.

### Report Sorting

All report commands support `--sort` and `--asc/--desc` flags:

```bash
tt weekly --sort cost --desc    # sort by cost, descending
tt sessions --sort tokens --asc # sort by tokens, ascending
```

Available sort fields: `tokens` / `cost` / `messages` / `time` / `input` / `output`

## Data Sources

| Agent | Path | Format |
|-------|------|--------|
| Claude Code | `~/.claude/projects/*/` | JSONL (per-message usage) |
| Codex | `~/.codex/sessions/` | JSONL + SQLite |

Cross-platform paths: on Windows `~` resolves to `%USERPROFILE%`. Honors `CLAUDE_CONFIG_DIR` / `CODEX_HOME` (the official custom-directory env vars) when set.

Token Tracker is **read-only** — it never modifies any agent data.

## Requirements

- Python 3.11+
- [Rich](https://github.com/Textualize/rich) (auto-installed)

## License

Copyright (c) 2026 stormzhang. MIT License.
