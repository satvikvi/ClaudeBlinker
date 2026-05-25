#!/bin/bash
# Install ClaudeBlinker for Ubuntu / WSL (WSLg required for WSL).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="$HOME/.local/share/claude-blinker"
BIN_DIR="$HOME/.local/bin"

echo "Installing ClaudeBlinker for Linux/WSL..."

# System packages -------------------------------------------------------
if command -v apt-get &>/dev/null; then
    echo "Installing system packages (requires sudo)..."
    sudo apt-get update -qq
    sudo apt-get install -y \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gtk-3.0 \
        python3-pip
elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3-gobject python3-gobject-cairo gtk3 python3-pip
elif command -v pacman &>/dev/null; then
    sudo pacman -S --needed python-gobject gtk3 python-pip
else
    echo "WARNING: Unknown package manager. Install python3-gi, python3-gi-cairo, and GTK3 manually."
fi

# Optional: system tray icon support ------------------------------------
python3 -m pip install --user pystray pillow 2>/dev/null || \
    echo "INFO: pystray/pillow not installed — system tray icon disabled (dot + right-click menu still works)."

# Install files ---------------------------------------------------------
mkdir -p "$INSTALL_DIR/hooks" "$BIN_DIR"

cp "$REPO/app_linux.py"  "$INSTALL_DIR/app.py"
cp "$REPO/setstate.py"   "$INSTALL_DIR/hooks/setstate.py"
chmod +x "$INSTALL_DIR/app.py" "$INSTALL_DIR/hooks/setstate.py"

# Launcher --------------------------------------------------------------
cat > "$BIN_DIR/claude-blinker" << 'LAUNCHER'
#!/bin/bash
APP="$HOME/.local/share/claude-blinker/app.py"
TRIGGER="$HOME/.claude-blinker/open-prefs"
mkdir -p "$HOME/.claude-blinker"
if pgrep -f "claude-blinker/app.py" > /dev/null 2>&1; then
    touch "$TRIGGER"
    exit 0
fi
nohup python3 "$APP" >> "$HOME/.claude-blinker/app.log" 2>&1 &
LAUNCHER
chmod +x "$BIN_DIR/claude-blinker"

# Ensure ~/.local/bin is on PATH ----------------------------------------
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo ""
    echo "Add ~/.local/bin to your PATH. Append to ~/.bashrc or ~/.zshrc:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
fi

echo ""
echo "Done."
echo "  Start:       claude-blinker"
echo "  setstate.py: $INSTALL_DIR/hooks/setstate.py"
echo "  Hooks file:  $REPO/hooks-example-linux.json"
echo ""
echo "Merge hooks-example-linux.json into ~/.claude/settings.json to wire up Claude Code."
echo ""
echo "WSL note: requires WSLg (Windows 11+). Set DISPLAY if the dot does not appear."
