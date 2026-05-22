# ClaudeBlinker

A tiny macOS menu-bar utility that surfaces the live state of every Claude Code
session you have open — across every terminal — as a coloured dot you can park
anywhere on the screen edge.

| State    | Colour  | Meaning                                           |
| -------- | ------- | ------------------------------------------------- |
| idle     | hidden  | No active session, or all sessions are quiet      |
| thinking | yellow  | Claude is reasoning between tool calls            |
| coding   | blue    | A tool call is in flight                          |
| waiting  | red 🔴  | Permission needed / interactive prompt — act now! |
| done     | green   | Turn finished                                     |

`waiting` blinks at a configurable interval so it's hard to miss.

## How it works

- Claude Code hooks fire `Contents/Resources/hooks/setstate.py` on every
  prompt / tool / notification / stop event.
- The hook writes the current state to `~/.claude-blinker/sessions/<id>/state`
  along with cwd + tty in `info.json`.
- The bundled PyObjC app (`Contents/Resources/app.py`) polls those files every
  250 ms and paints the dot + menu-bar icon accordingly.

## Features

- **Floating dot overlay** visible across spaces and fullscreen apps.
- **Menu-bar status icon** (`●` coloured / `○` idle).
- **Preferences window** to tune dot size, blink speed, and screen-edge
  position (with magnetic side-midpoint snapping).
- **Per-terminal tracking** — track everything or pick specific sessions.
- **Native NSTableView** session list with alternating row colours.
- **Stuck-state promotion** — if a tool stays "coding" past 4 s (probably
  blocked on a permission prompt), the dot auto-flips to red.
- **Click the dot to jump** — opens the matching Terminal.app tab via tty
  match (falls back to Prefs ▸ Terminals when ambiguous).

## Install

1. Move/copy `ClaudeBlinker.app` somewhere persistent (e.g. `/Applications`).
2. Edit `~/.claude/settings.json` to wire the hooks (see `hooks-example.json`
   in this repo or copy from the snippets below).
3. Double-click the app once to launch it; it lives in the menu bar from then on.

### Hook snippet

```json
{
  "hooks": {
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py idle"     }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py thinking" }] }],
    "PreToolUse":       [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py coding"   }] }],
    "PostToolUse":      [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py thinking" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py waiting"  }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "/usr/bin/python3 /path/to/ClaudeBlinker.app/Contents/Resources/hooks/setstate.py done"     }] }]
  }
}
```

## Requirements

- macOS 11 +
- A Python 3 install with `pyobjc` available
  (default: `/opt/anaconda3/bin/python3` — change the path in
  `Contents/MacOS/launcher` if you use a different interpreter).

## Known caveats

- Permission dialogs name the process **python3.13** because the binary is the
  interpreter, not a code-signed bundle. Functionality is unaffected; bundling
  via `py2app` would fix the cosmetics.
- Click-to-focus uses AppleScript against Terminal.app. iTerm2 is not handled
  yet.
- Clicks landing exactly on a screen edge or corner can be eaten by macOS
  gestures — reposition the dot via the Position slider if this bites.
