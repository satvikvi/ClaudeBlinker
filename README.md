# ClaudeBlinker

A tiny macOS menu-bar utility that surfaces the live state of every Claude Code
session you have open, across every terminal, as a coloured dot you can park
anywhere on the screen edge.

| State    | Colour  | Meaning                                          |
| -------- | ------- | ------------------------------------------------ |
| idle     | hidden  | No active session, or all sessions are quiet     |
| thinking | yellow  | Claude is reasoning between tool calls           |
| coding   | blue    | A tool call is in flight                         |
| waiting  | red     | Permission needed or interactive prompt          |
| done     | green   | Turn finished                                    |

The `waiting` dot blinks so it's hard to miss.

## How it works

Claude Code hooks run `setstate.py` on every prompt, tool, notification and
stop event. The hook writes the current state to
`~/.claude-blinker/sessions/<id>/state` along with `cwd` and `tty` in
`info.json`. The PyObjC app (`app.py`) polls those files every 250 ms and
paints the dot plus the menu-bar icon accordingly.

## Features

- Floating dot overlay visible across spaces and fullscreen apps.
- Menu-bar status icon (`●` coloured, `○` idle).
- Preferences window for dot size, blink speed, and screen-edge position
  (with magnetic snapping at the side midpoints).
- Per-terminal tracking. Track everything, or pick specific sessions.
- Native `NSTableView` session list with alternating row colours.
- Stuck-state promotion: if a tool stays "coding" past 4 seconds (most likely
  blocked on a permission prompt), the dot flips to red automatically.
- Click the dot to jump to the matching Terminal.app tab. Falls back to
  Preferences > Terminals when the target is ambiguous.

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

`dist/` is gitignored. Build it locally.

## Install

```bash
./scripts/build.sh                            # produces dist/ClaudeBlinker.app
cp -R dist/ClaudeBlinker.app /Applications/   # or anywhere persistent
open /Applications/ClaudeBlinker.app          # first run; lives in the menu bar
```

Merge `hooks-example.json` into `~/.claude/settings.json`. It assumes the
bundle lives at `/Applications/ClaudeBlinker.app`. If you put it elsewhere,
adjust the paths.

The first click on the floating dot triggers a macOS Automation permission
prompt for Terminal. Click **OK**.

## Update

```bash
./scripts/build.sh && cp -R dist/ClaudeBlinker.app /Applications/
pkill -f "ClaudeBlinker.app/Contents/Resources/app.py"
open /Applications/ClaudeBlinker.app
```

### Dev workflow (skip the bundle)

If you iterate on `setstate.py` often, point the hooks straight at the source
file so edits go live without rebuilding:

```
/usr/bin/python3 /path/to/claude-blinker/setstate.py <state>
```

For `app.py` changes you still need to rebuild and relaunch. The bundle loads
it once at startup.

## Uninstall

```bash
pkill -f "ClaudeBlinker.app/Contents/Resources/app.py"
rm -rf /Applications/ClaudeBlinker.app
rm -rf ~/.claude-blinker                      # runtime state (sessions, config)
```

Then remove the 6 hook entries from `~/.claude/settings.json`, drag the Dock
tile off, and revoke the Automation permission in System Settings > Privacy
& Security > Automation.

## Requirements

- macOS 11 or newer.
- A Python 3 install with `pyobjc` available. The default is
  `/opt/anaconda3/bin/python3`. If you use a different interpreter, change
  the path in `launcher.sh` and rebuild.

## Known caveats

- Permission dialogs name the process **python3.13** because the binary is
  the interpreter, not a code-signed bundle. Functionality is unaffected.
  Bundling via `py2app` would fix the cosmetics.
- Click-to-focus uses AppleScript against Terminal.app. iTerm2 is not handled
  yet.
- Clicks landing exactly on a screen edge or corner can be eaten by macOS
  gestures. Reposition the dot via the Position slider if this bites.

## License

MIT. See [LICENSE](LICENSE).
