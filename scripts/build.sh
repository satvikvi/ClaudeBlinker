#!/bin/bash
# Assemble dist/ClaudeBlinker.app from source files at the repo root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$REPO/dist"
APP="$DIST/ClaudeBlinker.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/hooks"

cp "$REPO/Info.plist"          "$APP/Contents/Info.plist"
cp "$REPO/launcher.sh"         "$APP/Contents/MacOS/launcher"
cp "$REPO/app.py"              "$APP/Contents/Resources/app.py"
cp "$REPO/setstate.py"         "$APP/Contents/Resources/hooks/setstate.py"
cp "$REPO/ClaudeBlinker.icns"  "$APP/Contents/Resources/ClaudeBlinker.icns"

chmod +x "$APP/Contents/MacOS/launcher"
chmod +x "$APP/Contents/Resources/hooks/setstate.py"

touch "$APP"   # nudge Finder/Dock icon cache

echo "Built $APP"
