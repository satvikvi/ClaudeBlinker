#!/usr/bin/env python3
"""Claude Blinker — floating dot overlay for Claude Code state.

The floating dot is visible over fullscreen apps. Click the menu bar
icon or the ClaudeBlinker.app to open the Preferences UI.

State is per-Claude-Code-session. Hooks write to
  ~/.claude-blinker/sessions/<session_id>/{state, info.json}
and the app aggregates the state of all tracked sessions.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSBezierPath,
    NSButton,
    NSButtonCell,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSScreen,
    NSScrollView,
    NSSlider,
    NSStatusBar,
    NSSwitchButton,
    NSTabView,
    NSTabViewItem,
    NSTableColumn,
    NSTableView,
    NSTableViewSelectionHighlightStyleRegular,
    NSTextField,
    NSTextFieldCell,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from AppKit import NSImage
from Foundation import NSBundle, NSObject

BASE = Path.home() / ".claude-blinker"
CONFIG_FILE = BASE / "config.json"
SESSIONS_DIR = BASE / "sessions"
TRIGGER_FILE = BASE / "open-prefs"

POLL_INTERVAL = 0.25
STATUS_WINDOW_LEVEL = 25  # NSStatusWindowLevel

# Hide sessions that haven't pinged a hook in this long — almost certainly
# the terminal was closed. Hooks fire on every prompt/response/notification,
# so an active terminal will keep this well under the threshold.
STALE_AFTER_SECONDS = 30 * 60

# Claude Code's Notification hook doesn't reliably fire when a tool blocks on
# user permission — only PreToolUse (which we map to "coding") and then a long
# pause. If "coding" persists past this threshold, assume permission-blocked
# and promote to "waiting" so the dot blinks red for attention.
CODING_TO_WAITING_SECONDS = 4

DEFAULT_CONFIG = {
    "size": 16,
    "position_angle": 0,  # -180..+180 degrees around the screen perimeter; 0 = top-center
    "blink_interval": 0.5,
    "track_all": True,
    "tracked_sessions": [],
}

TEST_SESSION = "_test"  # session id used by menu Test items

# Magnetic snap on the position slider (side-midpoint angles in degrees)
SNAP_POINTS = [-180, -90, 0, 90, 180]
SNAP_ZONE_DEG = 10

# Aggregate priority (highest first) and human labels for the 5 states
STATE_PRIORITY = ("waiting", "coding", "thinking", "done", "idle")
STATE_LABELS = {
    "idle": "Idle",
    "thinking": "Thinking",
    "coding": "Coding",
    "waiting": "Waiting for input",
    "done": "Done",
}


class State:
    config = dict(DEFAULT_CONFIG)
    current = "idle"
    blink_on = True
    preview_mode = False
    window = None
    view = None
    status_item = None
    prefs_window = None
    controller = None
    blink_timer = None
    size_label = None
    blink_label = None
    position_label = None
    status_menu_item = None
    terminals_view = None
    terminals_table = None
    terminals_datasource = None
    track_all_checkbox = None
    tracked_session_list = []  # ordered by display, used to map tag -> id
    session_state_labels = {}  # legacy, unused with NSTableView
    last_snap = None  # most recent slider snap point (for haptic edge-trigger)


# ---------- config ----------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg):
    BASE.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------- sessions ----------

def list_sessions():
    out = []
    if not SESSIONS_DIR.exists():
        return out
    for sd in SESSIONS_DIR.iterdir():
        if not sd.is_dir():
            continue
        info = {}
        info_file = sd / "info.json"
        if info_file.exists():
            try:
                info = json.loads(info_file.read_text())
            except Exception:
                pass
        state_file = sd / "state"
        sess_state = "idle"
        state_mtime = 0.0
        if state_file.exists():
            try:
                sess_state = state_file.read_text().strip() or "idle"
                state_mtime = state_file.stat().st_mtime
            except OSError:
                pass
        # Promote stuck "coding" to "waiting" — most likely permission-blocked,
        # since real tool calls progress to PostToolUse quickly.
        if sess_state == "coding" and state_mtime > 0:
            if (time.time() - state_mtime) > CODING_TO_WAITING_SECONDS:
                sess_state = "waiting"
        last_seen = float(info.get("last_seen") or sd.stat().st_mtime)
        out.append({
            "id": sd.name,
            "cwd": info.get("cwd", ""),
            "last_seen": last_seen,
            "state": sess_state,
        })
    cutoff = time.time() - STALE_AFTER_SECONDS
    out = [s for s in out if s["last_seen"] >= cutoff or s["id"] == TEST_SESSION]
    out.sort(key=lambda s: -s["last_seen"])
    return out


def aggregate_state():
    cfg = State.config
    track_all = bool(cfg.get("track_all", True))
    selected = set(cfg.get("tracked_sessions", []))

    states = set()
    for sess in list_sessions():
        if track_all or sess["id"] in selected:
            states.add(sess["state"])
    for s in STATE_PRIORITY:
        if s in states:
            return s
    return "idle"


# ---------- floating dot ----------

def _read_session_tty(session_id):
    info_file = SESSIONS_DIR / session_id / "info.json"
    try:
        return json.loads(info_file.read_text()).get("tty", "")
    except (OSError, json.JSONDecodeError):
        return ""


def focus_terminal_by_tty(tty_path):
    """Activate the Terminal.app tab whose tty matches. Returns True on success."""
    if not tty_path:
        return False
    script = f'''
    tell application "Terminal"
        activate
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is "{tty_path}" then
                    set selected of t to true
                    set frontmost of w to true
                    return "ok"
                end if
            end repeat
        end repeat
    end tell
    return "miss"
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=3
        )
        return r.returncode == 0 and r.stdout.strip() == "ok"
    except (subprocess.SubprocessError, OSError):
        return False


class DotView(NSView):
    def mouseDown_(self, event):
        if State.controller is not None:
            State.controller.dotClicked_(self)

    def drawRect_(self, rect):
        s = State.current
        if s == "waiting":
            if not State.blink_on:
                return
            color = NSColor.systemRedColor()
        elif s == "coding":
            color = NSColor.systemBlueColor()
        elif s == "thinking":
            color = NSColor.systemYellowColor()
        elif s == "done":
            color = NSColor.systemGreenColor()
        elif State.preview_mode:
            if not State.blink_on:
                return
            color = NSColor.blackColor()
        else:
            return
        path = NSBezierPath.bezierPathWithOvalInRect_(self.bounds())
        color.setFill()
        path.fill()


def compute_dot_origin(size, angle_deg):
    """Place the dot center along the screen perimeter at the given angle.
    0° = top-center, +90° = right midpoint, ±180° = bottom-center, -90° = left.
    Returns (x, y) window origin (bottom-left of dot in screen coords)."""
    screen = NSScreen.mainScreen()
    sf = screen.frame()
    W = sf.size.width
    H = sf.size.height
    inset = 5 + size / 2.0  # keep dot fully inside screen
    left, right = inset, W - inset
    bottom, top = inset, H - inset

    seg_top_right = right - W / 2.0     # top-center -> top-right
    seg_right = top - bottom            # top-right  -> bottom-right
    seg_bottom = right - left           # bottom-right -> bottom-left
    seg_left = top - bottom             # bottom-left -> top-left
    seg_top_left = W / 2.0 - left       # top-left -> top-center (back)
    total = seg_top_right + seg_right + seg_bottom + seg_left + seg_top_left

    theta = float(angle_deg) % 360.0
    p = (theta / 360.0) * total

    if p <= seg_top_right:
        cx, cy = W / 2.0 + p, top
    elif p <= seg_top_right + seg_right:
        cx, cy = right, top - (p - seg_top_right)
    elif p <= seg_top_right + seg_right + seg_bottom:
        cx, cy = right - (p - seg_top_right - seg_right), bottom
    elif p <= seg_top_right + seg_right + seg_bottom + seg_left:
        cx, cy = left, bottom + (p - seg_top_right - seg_right - seg_bottom)
    else:
        cx, cy = left + (p - seg_top_right - seg_right - seg_bottom - seg_left), top

    x = sf.origin.x + cx - size / 2.0
    y = sf.origin.y + cy - size / 2.0
    return x, y


def make_overlay_window(size):
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, size, size), 0, NSBackingStoreBuffered, False
    )
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    # Capture clicks so users can tap the dot to focus the matching Terminal
    # tab. Note: at extreme screen edges/corners macOS gestures may eat the
    # click before AppKit sees it — that's a positioning trade-off.
    win.setIgnoresMouseEvents_(False)
    win.setLevel_(STATUS_WINDOW_LEVEL)
    win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle
    )
    return win


def apply_config():
    cfg = State.config
    size = max(4, int(cfg.get("size", 14)))
    angle = float(cfg.get("position_angle", 0))

    x, y = compute_dot_origin(size, angle)
    State.window.setFrame_display_(NSMakeRect(x, y, size, size), True)
    State.view.setFrame_(NSMakeRect(0, 0, size, size))
    State.view.setNeedsDisplay_(True)

    if State.blink_timer is not None:
        State.blink_timer.invalidate()
    State.blink_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        max(0.05, float(cfg.get("blink_interval", 0.5))),
        State.controller,
        "blinkTick:",
        None,
        True,
    )


# ---------- controller ----------

class Controller(NSObject):
    def pollState_(self, timer):
        new_state = aggregate_state()
        if new_state != State.current:
            State.current = new_state
            State.blink_on = True
            if State.view is not None:
                State.view.setNeedsDisplay_(True)
            if State.status_menu_item is not None:
                State.status_menu_item.setTitle_(f"Status: {STATE_LABELS.get(new_state, new_state)}")
            self.refreshStatusIcon()

        if State.prefs_window is not None and State.prefs_window.isVisible():
            ds = getattr(State, "terminals_datasource", None)
            if ds is not None:
                old = [(s["id"], s["state"]) for s in ds.sessions()]
                ds.refresh()
                new = [(s["id"], s["state"]) for s in ds.sessions()]
                if old != new and State.terminals_table is not None:
                    State.terminals_table.reloadData()

        if TRIGGER_FILE.exists():
            try:
                TRIGGER_FILE.unlink()
            except OSError:
                pass
            self.menuOpenPrefs_(None)

    def blinkTick_(self, timer):
        should_blink = State.current == "waiting" or (
            State.preview_mode and State.current == "idle"
        )
        if should_blink and State.view is not None:
            State.blink_on = not State.blink_on
            State.view.setNeedsDisplay_(True)

    @objc.python_method
    def refreshStatusIcon(self):
        if State.status_item is None:
            return
        button = State.status_item.button()
        if button is None:
            return
        s = State.current
        if s == "waiting":
            symbol, color = "●", NSColor.systemRedColor()
        elif s == "coding":
            symbol, color = "●", NSColor.systemBlueColor()
        elif s == "thinking":
            symbol, color = "●", NSColor.systemYellowColor()
        elif s == "done":
            symbol, color = "●", NSColor.systemGreenColor()
        else:
            symbol, color = "○", NSColor.secondaryLabelColor()
        attrs = {
            NSForegroundColorAttributeName: color,
            NSFontAttributeName: NSFont.systemFontOfSize_(15),
        }
        button.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(symbol, attrs)
        )

    @objc.python_method
    def _write_session_state(self, session_id, sess_state, cwd_label=None):
        sd = SESSIONS_DIR / session_id
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "state").write_text(sess_state)
        info = {
            "cwd": cwd_label or "(menu test)",
            "last_seen": time.time(),
        }
        (sd / "info.json").write_text(json.dumps(info))

    def menuTestThinking_(self, sender):
        self._write_session_state(TEST_SESSION, "thinking")

    def menuTestCoding_(self, sender):
        self._write_session_state(TEST_SESSION, "coding")

    def menuTestWaiting_(self, sender):
        self._write_session_state(TEST_SESSION, "waiting")

    def menuTestDone_(self, sender):
        self._write_session_state(TEST_SESSION, "done")

    def menuTestIdle_(self, sender):
        self._write_session_state(TEST_SESSION, "idle")

    def menuOpenPrefs_(self, sender):
        if State.prefs_window is None:
            State.prefs_window = make_prefs_window(self)
        else:
            populate_terminals_tab(self)
        State.preview_mode = True
        if State.view is not None:
            State.view.setNeedsDisplay_(True)
        # Temporarily promote to a regular app so the window reliably comes to
        # the front and takes focus. windowWillClose_ flips it back to accessory.
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if State.prefs_window.isMiniaturized():
            State.prefs_window.deminiaturize_(None)
        # Force-center on main screen so it can't end up off-screen / on a
        # disconnected display.
        State.prefs_window.center()
        State.prefs_window.orderFrontRegardless()
        State.prefs_window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
        print(f"[prefs] visible={State.prefs_window.isVisible()} frame={State.prefs_window.frame()}", flush=True)

    def menuOpenConfigFile_(self, sender):
        os.system(f'open -e "{CONFIG_FILE}"')

    def menuQuit_(self, sender):
        NSApplication.sharedApplication().terminate_(None)

    def dotClicked_(self, sender):
        cfg = State.config
        track_all = bool(cfg.get("track_all", True))
        sessions = list_sessions()
        target = None
        if not track_all:
            tracked = set(cfg.get("tracked_sessions", []))
            picked = [s for s in sessions if s["id"] in tracked]
            if len(picked) == 1:
                target = picked[0]

        if target is not None:
            tty = _read_session_tty(target["id"])
            if focus_terminal_by_tty(tty):
                return
            # Fall through to Prefs if focusing failed (no Terminal tab matched,
            # or user is on a different terminal app).

        # Default: open the Prefs window on the Terminals tab.
        self.menuOpenPrefs_(None)
        self._select_terminals_tab()

    @objc.python_method
    def _select_terminals_tab(self):
        tv = getattr(State, "prefs_tab_view", None)
        if tv is not None:
            try:
                tv.selectTabViewItemWithIdentifier_("terminals")
            except Exception:
                pass

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        # Fires when user clicks the Dock icon of the already-running app.
        # Without this, accessory-mode apps just no-op on Dock clicks.
        print(f"[reopen] has_visible={has_visible}", flush=True)
        self.menuOpenPrefs_(None)
        return True

    def windowWillClose_(self, notification):
        State.preview_mode = False
        State.session_state_labels = {}
        if State.view is not None:
            State.view.setNeedsDisplay_(True)
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

    # Blinker tab actions
    def sizeChanged_(self, sender):
        State.config["size"] = int(sender.intValue())
        save_config(State.config)
        apply_config()
        if State.size_label is not None:
            State.size_label.setStringValue_(f"{State.config['size']} px")

    def blinkChanged_(self, sender):
        State.config["blink_interval"] = round(float(sender.doubleValue()), 2)
        save_config(State.config)
        apply_config()
        if State.blink_label is not None:
            State.blink_label.setStringValue_(f"{State.config['blink_interval']:.2f} s")

    def positionAngleChanged_(self, sender):
        raw = int(sender.intValue())
        snapped = raw
        for sp in SNAP_POINTS:
            if abs(raw - sp) <= SNAP_ZONE_DEG:
                snapped = sp
                break
        if snapped != raw:
            sender.setIntValue_(snapped)
        # Haptic edge-trigger: tap only when entering a new snap point
        if snapped in SNAP_POINTS and snapped != State.last_snap:
            try:
                from AppKit import NSHapticFeedbackManager
                NSHapticFeedbackManager.defaultPerformer().performFeedbackPattern_performanceTime_(1, 0)
            except Exception:
                pass
            State.last_snap = snapped
        elif snapped not in SNAP_POINTS:
            State.last_snap = None
        State.config["position_angle"] = snapped
        save_config(State.config)
        apply_config()
        if State.position_label is not None:
            State.position_label.setStringValue_(f"{snapped}°")

    def resetDefaults_(self, sender):
        State.config = dict(DEFAULT_CONFIG)
        save_config(State.config)
        apply_config()
        if State.prefs_window is not None:
            State.prefs_window.close()
            State.prefs_window = None
        self.menuOpenPrefs_(None)

    # Terminals tab actions
    def trackAllChanged_(self, sender):
        State.config["track_all"] = bool(sender.state())
        save_config(State.config)
        populate_terminals_tab(self)
        self._recompute_state()

    def sessionToggled_(self, sender):
        idx = sender.tag()
        if not (0 <= idx < len(State.tracked_session_list)):
            return
        sid = State.tracked_session_list[idx]
        tracked = State.config.setdefault("tracked_sessions", [])
        if sender.state() == 1:
            if sid not in tracked:
                tracked.append(sid)
        else:
            if sid in tracked:
                tracked.remove(sid)
        save_config(State.config)
        self._recompute_state()

    def refreshTerminals_(self, sender):
        populate_terminals_tab(self)

    @objc.python_method
    def _recompute_state(self):
        new_state = aggregate_state()
        if new_state != State.current:
            State.current = new_state
            State.blink_on = True
            if State.view is not None:
                State.view.setNeedsDisplay_(True)
            if State.status_menu_item is not None:
                State.status_menu_item.setTitle_(f"Status: {STATE_LABELS.get(new_state, new_state)}")
            self.refreshStatusIcon()


# ---------- prefs window ----------

def _label(text, x, y, w=120, h=18, secondary=False):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    if secondary:
        f.setTextColor_(NSColor.secondaryLabelColor())
    return f


def make_blinker_tab(controller):
    width, height = 480, 340
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    cfg = State.config
    y = height - 40

    # Size
    view.addSubview_(_label("Dot size:", 20, y))
    slider = NSSlider.alloc().initWithFrame_(NSMakeRect(130, y - 4, 220, 24))
    slider.setMinValue_(6); slider.setMaxValue_(60)
    slider.setIntValue_(int(cfg["size"]))
    slider.setContinuous_(True)
    slider.setTarget_(controller)
    slider.setAction_("sizeChanged:")
    view.addSubview_(slider)
    lbl = _label(f"{int(cfg['size'])} px", 360, y, w=60)
    view.addSubview_(lbl)
    State.size_label = lbl
    y -= 40

    # Blink
    view.addSubview_(_label("Blink speed:", 20, y))
    bs = NSSlider.alloc().initWithFrame_(NSMakeRect(130, y - 4, 220, 24))
    bs.setMinValue_(0.1); bs.setMaxValue_(2.0)
    bs.setDoubleValue_(float(cfg["blink_interval"]))
    bs.setContinuous_(True)
    bs.setTarget_(controller)
    bs.setAction_("blinkChanged:")
    view.addSubview_(bs)
    bl = _label(f"{cfg['blink_interval']:.2f} s", 360, y, w=60)
    view.addSubview_(bl)
    State.blink_label = bl
    y -= 40

    # Position angle (walks the dot around the screen perimeter)
    view.addSubview_(_label("Position:", 20, y))
    pos_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(130, y - 4, 220, 24))
    pos_slider.setMinValue_(-180); pos_slider.setMaxValue_(180)
    pos_slider.setIntValue_(int(cfg.get("position_angle", 0)))
    pos_slider.setContinuous_(True)
    pos_slider.setNumberOfTickMarks_(9)   # every 45° — corners + side midpoints
    pos_slider.setTickMarkPosition_(1)    # NSTickMarkPositionBelow
    try:
        pos_slider.cell().setTrackFillColor_(NSColor.clearColor())
    except Exception:
        pass
    pos_slider.setTarget_(controller)
    pos_slider.setAction_("positionAngleChanged:")
    view.addSubview_(pos_slider)
    pos_lbl = _label(f"{int(cfg.get('position_angle', 0))}°", 360, y, w=60)
    view.addSubview_(pos_lbl)
    State.position_label = pos_lbl
    y -= 22

    # Fixed side labels under the slider, centered on each side-midpoint angle
    slider_x = 130.0
    slider_w = 220.0
    for ang, name in [(-180, "Bot"), (-90, "Left"), (0, "Top"), (90, "Right"), (180, "Bot")]:
        cx = slider_x + (ang + 180) / 360.0 * slider_w
        lbl = _label(name, cx - 25, y, w=50, secondary=True)
        lbl.setAlignment_(2)  # NSTextAlignmentCenter
        view.addSubview_(lbl)
    y -= 30

    # Reset + Quit
    reset = NSButton.alloc().initWithFrame_(NSMakeRect(20, 14, 130, 28))
    reset.setTitle_("Reset defaults")
    reset.setBezelStyle_(1)
    reset.setTarget_(controller)
    reset.setAction_("resetDefaults:")
    view.addSubview_(reset)

    quit_btn = NSButton.alloc().initWithFrame_(NSMakeRect(340, 14, 120, 28))
    quit_btn.setTitle_("Quit Blinker")
    quit_btn.setBezelStyle_(1)
    quit_btn.setTarget_(controller)
    quit_btn.setAction_("menuQuit:")
    view.addSubview_(quit_btn)

    return view


class TerminalsDataSource(NSObject):
    def initWithController_(self, controller):
        self = objc.super(TerminalsDataSource, self).init()
        if self is None:
            return None
        self._controller = controller
        self._sessions = []
        return self

    @objc.python_method
    def refresh(self):
        self._sessions = list_sessions()

    @objc.python_method
    def sessions(self):
        return self._sessions

    def numberOfRowsInTableView_(self, tv):
        return len(self._sessions)

    def tableView_objectValueForTableColumn_row_(self, tv, col, row):
        sess = self._sessions[row]
        ident = col.identifier()
        if ident == "track":
            cfg = State.config
            if cfg.get("track_all", True):
                return 1
            return 1 if sess["id"] in cfg.get("tracked_sessions", []) else 0
        if ident == "folder":
            cwd = sess["cwd"]
            return os.path.basename(cwd.rstrip("/")) if cwd else sess["id"][:10]
        if ident == "state":
            return sess["state"]
        return ""

    def tableView_setObjectValue_forTableColumn_row_(self, tv, value, col, row):
        if col.identifier() != "track":
            return
        sess = self._sessions[row]
        tracked = State.config.setdefault("tracked_sessions", [])
        if value:
            if sess["id"] not in tracked:
                tracked.append(sess["id"])
        else:
            if sess["id"] in tracked:
                tracked.remove(sess["id"])
        save_config(State.config)
        self._controller._recompute_state()

    # Delegate: per-cell enable/disable for the checkbox column
    def tableView_willDisplayCell_forTableColumn_row_(self, tv, cell, col, row):
        if col.identifier() == "track":
            cell.setEnabled_(not bool(State.config.get("track_all", True)))


def make_terminals_tab(controller):
    width, height = 480, 340
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    State.terminals_view = view

    # Header controls (track-all toggle + refresh)
    y = height - 36
    cb_all = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 260, 22))
    cb_all.setButtonType_(3)  # NSButtonTypeSwitch
    cb_all.setTitle_("Track all sessions")
    cb_all.setTarget_(controller)
    cb_all.setAction_("trackAllChanged:")
    cb_all.setState_(1 if State.config.get("track_all", True) else 0)
    view.addSubview_(cb_all)
    State.track_all_checkbox = cb_all

    refresh_btn = NSButton.alloc().initWithFrame_(NSMakeRect(360, y - 3, 100, 26))
    refresh_btn.setTitle_("Refresh")
    refresh_btn.setBezelStyle_(1)
    refresh_btn.setTarget_(controller)
    refresh_btn.setAction_("refreshTerminals:")
    view.addSubview_(refresh_btn)

    y -= 26
    view.addSubview_(_label(
        "When off, only checked sessions trigger the blinker.",
        20, y, w=440, secondary=True,
    ))

    # Scrollable native table fills the rest of the tab
    table_top = y - 8
    scroll = NSScrollView.alloc().initWithFrame_(
        NSMakeRect(20, 12, width - 40, table_top - 12)
    )
    scroll.setBorderType_(NSBezelBorder)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setFocusRingType_(1)  # NSFocusRingTypeNone

    table = NSTableView.alloc().initWithFrame_(scroll.contentView().bounds())
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setGridStyleMask_(0)
    table.setRowHeight_(22)
    table.setSelectionHighlightStyle_(NSTableViewSelectionHighlightStyleRegular)
    table.setAllowsColumnResizing_(True)
    table.setColumnAutoresizingStyle_(1)  # NSTableViewUniformColumnAutoresizingStyle
    table.setFocusRingType_(1)  # NSFocusRingTypeNone

    # Track column (checkbox)
    col_track = NSTableColumn.alloc().initWithIdentifier_("track")
    col_track.headerCell().setStringValue_("Track")
    col_track.setWidth_(56)
    col_track.setMinWidth_(48)
    col_track.setMaxWidth_(72)
    cb_cell = NSButtonCell.alloc().init()
    cb_cell.setButtonType_(NSSwitchButton)
    cb_cell.setTitle_("")
    cb_cell.setControlSize_(1)  # small
    col_track.setDataCell_(cb_cell)
    col_track.setEditable_(True)
    table.addTableColumn_(col_track)

    # Folder column
    col_folder = NSTableColumn.alloc().initWithIdentifier_("folder")
    col_folder.headerCell().setStringValue_("Folder")
    col_folder.setWidth_(260)
    col_folder.setMinWidth_(120)
    tf_cell = NSTextFieldCell.alloc().init()
    col_folder.setDataCell_(tf_cell)
    col_folder.setEditable_(False)
    table.addTableColumn_(col_folder)

    # State column
    col_state = NSTableColumn.alloc().initWithIdentifier_("state")
    col_state.headerCell().setStringValue_("State")
    col_state.setWidth_(90)
    col_state.setMinWidth_(60)
    tf_cell2 = NSTextFieldCell.alloc().init()
    col_state.setDataCell_(tf_cell2)
    col_state.setEditable_(False)
    table.addTableColumn_(col_state)

    ds = TerminalsDataSource.alloc().initWithController_(controller)
    ds.refresh()
    table.setDataSource_(ds)
    table.setDelegate_(ds)
    scroll.setDocumentView_(table)
    view.addSubview_(scroll)

    State.terminals_table = table
    State.terminals_datasource = ds  # retain
    return view


def populate_terminals_tab(controller):
    # Refresh table data + the "Track all" checkbox state.
    if getattr(State, "terminals_table", None) is None:
        return
    ds = State.terminals_datasource
    ds.refresh()
    State.terminals_table.reloadData()
    if getattr(State, "track_all_checkbox", None) is not None:
        State.track_all_checkbox.setState_(
            1 if State.config.get("track_all", True) else 0
        )


def make_prefs_window(controller):
    width, height = 520, 420
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
    )
    w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
    )
    w.setTitle_("ClaudeBlinker")
    w.setReleasedWhenClosed_(False)
    w.setDelegate_(controller)

    tab_view = NSTabView.alloc().initWithFrame_(
        NSMakeRect(10, 10, width - 20, height - 20)
    )
    w.contentView().addSubview_(tab_view)
    State.prefs_tab_view = tab_view

    blinker_view = make_blinker_tab(controller)
    tab1 = NSTabViewItem.alloc().initWithIdentifier_("blinker")
    tab1.setLabel_("Blinker Settings")
    tab1.setView_(blinker_view)
    tab_view.addTabViewItem_(tab1)

    terminals_view = make_terminals_tab(controller)
    tab2 = NSTabViewItem.alloc().initWithIdentifier_("terminals")
    tab2.setLabel_("Terminals")
    tab2.setView_(terminals_view)
    tab_view.addTabViewItem_(tab2)

    w.center()
    return w


# ---------- menu ----------

def make_menu(controller):
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)

    status = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"Status: {STATE_LABELS.get(State.current, State.current)}", "", ""
    )
    status.setEnabled_(False)
    menu.addItem_(status)
    State.status_menu_item = status

    menu.addItem_(NSMenuItem.separatorItem())

    for title, sel in [
        ("Test: Thinking (yellow)", "menuTestThinking:"),
        ("Test: Coding (blue)", "menuTestCoding:"),
        ("Test: Waiting (red)", "menuTestWaiting:"),
        ("Test: Done (green)", "menuTestDone:"),
        ("Test: Clear (idle)", "menuTestIdle:"),
    ]:
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
        mi.setTarget_(controller)
        menu.addItem_(mi)

    menu.addItem_(NSMenuItem.separatorItem())

    prefs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Preferences…", "menuOpenPrefs:", ","
    )
    prefs.setTarget_(controller)
    menu.addItem_(prefs)

    open_cfg = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open config file…", "menuOpenConfigFile:", ""
    )
    open_cfg.setTarget_(controller)
    menu.addItem_(open_cfg)

    menu.addItem_(NSMenuItem.separatorItem())

    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit ClaudeBlinker", "menuQuit:", "q"
    )
    quit_item.setTarget_(controller)
    menu.addItem_(quit_item)

    return menu


# ---------- main ----------

def main():
    BASE.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    State.config = load_config()

    # The running executable is /opt/anaconda3/bin/python3, so without this
    # patch macOS shows the prefs-window Dock tile as "python3.13" with a
    # blank icon. Rewriting the main bundle's info dict + setting the app
    # icon image makes AppKit present us as ClaudeBlinker instead.
    info = NSBundle.mainBundle().infoDictionary()
    info["CFBundleName"] = "ClaudeBlinker"
    info["CFBundleDisplayName"] = "ClaudeBlinker"
    info["CFBundleIdentifier"] = "com.sevo.claudeblinker"

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    icon_path = os.path.join(os.path.dirname(__file__), "ClaudeBlinker.icns")
    if os.path.exists(icon_path):
        icon = NSImage.alloc().initByReferencingFile_(icon_path)
        if icon is not None:
            app.setApplicationIconImage_(icon)

    size = int(State.config["size"])
    window = make_overlay_window(size)
    view = DotView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
    window.setContentView_(view)
    State.window = window
    State.view = view
    window.orderFrontRegardless()

    controller = Controller.alloc().init()
    State.controller = controller
    app.setDelegate_(controller)

    apply_config()

    State.current = aggregate_state()
    State.view.setNeedsDisplay_(True)

    poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        POLL_INTERVAL, controller, "pollState:", None, True
    )

    main._refs = (window, view, controller, poll_timer)
    app.run()


if __name__ == "__main__":
    main()
