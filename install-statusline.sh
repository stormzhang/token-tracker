#!/usr/bin/env bash
set -e

SCRIPT_PATH="$HOME/.claude/tt-statusline.py"
SETTINGS_PATH="$HOME/.claude/settings.json"
REPO_URL="https://raw.githubusercontent.com/nicepkg/token-tracker/master/tt-statusline.py"

if [ ! -d "$HOME/.claude" ]; then
    echo "Error: ~/.claude not found. Is Claude Code installed?"
    exit 1
fi

echo "Downloading tt-statusline.py..."
curl -sS -o "$SCRIPT_PATH" "$REPO_URL"
chmod +x "$SCRIPT_PATH"

if [ -f "$SETTINGS_PATH" ]; then
    python3 -c "
import json
with open('$SETTINGS_PATH') as f:
    s = json.load(f)
s['statusLine'] = {'type': 'command', 'command': '$SCRIPT_PATH'}
with open('$SETTINGS_PATH', 'w') as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
"
else
    echo '{"statusLine":{"type":"command","command":"'"$SCRIPT_PATH"'"}}' | python3 -c "
import json, sys
print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))
" > "$SETTINGS_PATH"
fi

echo "✓ Installed: $SCRIPT_PATH"
echo "✓ Configured: $SETTINGS_PATH"
echo ""
echo "Restart Claude Code to activate."
