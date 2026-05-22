#!/bin/bash
# ClaudeBlinker launcher
# - If the daemon is running: signal it to open the Preferences window
# - If not: start the daemon (app.py inside the bundle), then signal it
BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PY="$BUNDLE_DIR/Resources/app.py"
PYTHON="/opt/anaconda3/bin/python3"
TRIGGER="$HOME/.claude-blinker/open-prefs"

mkdir -p "$HOME/.claude-blinker"

if pgrep -f "$APP_PY" > /dev/null; then
    touch "$TRIGGER"
    exit 0
fi

# exec (don't background) so the LaunchServices bundle registration that
# macOS gave THIS process transfers to python — otherwise the Dock tile
# shows "python3.13" with a blank icon when the prefs window opens.
exec "$PYTHON" "$APP_PY" > "$HOME/.claude-blinker/app.log" 2>&1
