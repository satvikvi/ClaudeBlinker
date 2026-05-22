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

## Repo layout

```
.
├── app.py              PyObjC menu-bar/overlay app
├── setstate.py         Hook script (writes per-session state)
├── launcher.sh         Bundle entry point (Contents/MacOS/launcher)
├── Info.plist          Bundle metadata
├── ClaudeBlinker.icns  Icon
├── hooks-example.json  Drop-in snippet for ~/.claude/settings.json
└── scripts/build.sh    Assembles dist/ClaudeBlinker.app from the above
```

`dist/` is gitignored; build it locally.

## Install

```bash
./scripts/build.sh
mv dist/ClaudeBlinker.app /Applications/   # or anywhere persistent
```

Merge `hooks-example.json` into `~/.claude/settings.json` (it assumes the
bundle lives at `/Applications/ClaudeBlinker.app` — adjust if you put it
elsewhere). Then double-click the app; it lives in the menu bar from then on.

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
