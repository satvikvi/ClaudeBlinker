#!/usr/bin/env python3
"""Claude Blinker (Linux/WSL) — floating dot overlay for Claude Code state.

State is per-Claude-Code-session. Hooks write to
  ~/.claude-blinker/sessions/<session_id>/{state, info.json}
and the app aggregates the state of all tracked sessions.
"""
import json
import math
import os
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo

BASE = Path.home() / ".claude-blinker"
CONFIG_FILE = BASE / "config.json"
SESSIONS_DIR = BASE / "sessions"
TRIGGER_FILE = BASE / "open-prefs"

POLL_INTERVAL_MS = 250
STALE_AFTER_SECONDS = 30 * 60
CODING_TO_WAITING_SECONDS = 4

DEFAULT_CONFIG = {
    "size": 16,
    "position_angle": 0,
    "blink_interval": 0.5,
    "track_all": True,
    "tracked_sessions": [],
}

TEST_SESSION = "_test"
SNAP_POINTS = [-180, -90, 0, 90, 180]
SNAP_ZONE_DEG = 10

STATE_PRIORITY = ("waiting", "coding", "thinking", "done", "idle")
STATE_LABELS = {
    "idle":     "Idle",
    "thinking": "Thinking",
    "coding":   "Coding",
    "waiting":  "Waiting for input",
    "done":     "Done",
}
STATE_COLORS = {
    "waiting":  (1.00, 0.23, 0.19),
    "coding":   (0.00, 0.48, 1.00),
    "thinking": (1.00, 0.80, 0.00),
    "done":     (0.20, 0.78, 0.35),
}


class _State:
    config = dict(DEFAULT_CONFIG)
    current = "idle"
    blink_on = True
    preview_mode = False
    dot_window = None
    prefs_window = None
    blink_source = None
    tray_icon = None
    _tray_make_image = None

State = _State()


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
    track_all = bool(State.config.get("track_all", True))
    selected = set(State.config.get("tracked_sessions", []))
    states = set()
    for sess in list_sessions():
        if track_all or sess["id"] in selected:
            states.add(sess["state"])
    for s in STATE_PRIORITY:
        if s in states:
            return s
    return "idle"


# ---------- dot position ----------

def compute_dot_origin(size, angle_deg):
    """Place dot center on screen perimeter. 0°=top, 90°=right, ±180°=bottom, -90°=left."""
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geo = monitor.get_geometry()
    W, H = geo.width, geo.height

    inset = 5 + size / 2.0
    left, right = inset, W - inset
    bot, top = inset, H - inset  # Cartesian (y+ up)

    seg_tr = right - W / 2.0
    seg_r  = top - bot
    seg_b  = right - left
    seg_l  = top - bot
    seg_tl = W / 2.0 - left
    total  = seg_tr + seg_r + seg_b + seg_l + seg_tl

    p = (float(angle_deg) % 360.0) / 360.0 * total

    if p <= seg_tr:
        cx, cy = W / 2.0 + p, top
    elif p <= seg_tr + seg_r:
        cx, cy = right, top - (p - seg_tr)
    elif p <= seg_tr + seg_r + seg_b:
        cx, cy = right - (p - seg_tr - seg_r), bot
    elif p <= seg_tr + seg_r + seg_b + seg_l:
        cx, cy = left, bot + (p - seg_tr - seg_r - seg_b)
    else:
        cx, cy = left + (p - seg_tr - seg_r - seg_b - seg_l), top

    # GTK screen coords: y=0 at top, increases downward
    return int(geo.x + cx - size / 2.0), int(geo.y + (H - cy) - size / 2.0)


# ---------- floating dot window ----------

class DotWindow(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.stick()
        self.set_accept_focus(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.connect("draw", self._draw)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._click)
        self.show_all()

    def _draw(self, widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        s = State.current
        color = STATE_COLORS.get(s)
        if color is None:
            if State.preview_mode and State.blink_on:
                color = (0, 0, 0)
            else:
                return False
        elif s == "waiting" and not State.blink_on:
            return False

        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = min(w, h) / 2.0
        cr.set_source_rgba(*color, 1.0)
        cr.arc(r, r, r - 1, 0, 2 * math.pi)
        cr.fill()
        return False

    def _click(self, widget, event):
        if event.button == 3:
            _show_dot_menu(event.time)
        else:
            open_prefs()
        return True

    def apply_config(self):
        size = max(4, int(State.config.get("size", 16)))
        angle = float(State.config.get("position_angle", 0))
        x, y = compute_dot_origin(size, angle)
        self.resize(size, size)
        self.move(x, y)
        self.queue_draw()
        _reschedule_blink()


def _show_dot_menu(t):
    menu = Gtk.Menu()
    si = Gtk.MenuItem(label=f"Status: {STATE_LABELS.get(State.current, State.current)}")
    si.set_sensitive(False)
    menu.append(si)
    menu.append(Gtk.SeparatorMenuItem())
    for label, sname in [
        ("Test: Thinking (yellow)", "thinking"),
        ("Test: Coding (blue)",     "coding"),
        ("Test: Waiting (red)",     "waiting"),
        ("Test: Done (green)",      "done"),
        ("Test: Clear (idle)",      "idle"),
    ]:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _, s=sname: _write_test_session(s))
        menu.append(item)
    menu.append(Gtk.SeparatorMenuItem())
    pi = Gtk.MenuItem(label="Preferences…")
    pi.connect("activate", lambda _: open_prefs())
    menu.append(pi)
    menu.append(Gtk.SeparatorMenuItem())
    qi = Gtk.MenuItem(label="Quit ClaudeBlinker")
    qi.connect("activate", lambda _: Gtk.main_quit())
    menu.append(qi)
    menu.show_all()
    menu.popup(None, None, None, None, 3, t)


def _write_test_session(sname):
    sd = SESSIONS_DIR / TEST_SESSION
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "state").write_text(sname)
    (sd / "info.json").write_text(json.dumps({"cwd": "(menu test)", "last_seen": time.time()}))


# ---------- timers ----------

def _reschedule_blink():
    if State.blink_source is not None:
        GLib.source_remove(State.blink_source)
    interval_ms = max(50, int(State.config.get("blink_interval", 0.5) * 1000))
    State.blink_source = GLib.timeout_add(interval_ms, _blink_tick)


def _blink_tick():
    if State.current == "waiting" or (State.preview_mode and State.current == "idle"):
        State.blink_on = not State.blink_on
        if State.dot_window:
            State.dot_window.queue_draw()
    return True


def _poll_tick():
    new_state = aggregate_state()
    if new_state != State.current:
        State.current = new_state
        State.blink_on = True
        if State.dot_window:
            State.dot_window.queue_draw()
        _update_tray()

    if State.prefs_window and State.prefs_window.is_visible():
        State.prefs_window.refresh_sessions()

    if TRIGGER_FILE.exists():
        try:
            TRIGGER_FILE.unlink()
        except OSError:
            pass
        open_prefs()

    return True


# ---------- optional system tray (pystray + pillow) ----------

def _update_tray():
    if not State.tray_icon or not State._tray_make_image:
        return
    cf = STATE_COLORS.get(State.current, (0.5, 0.5, 0.5))
    try:
        State.tray_icon.icon = State._tray_make_image(tuple(int(c * 255) for c in cf))
    except Exception:
        pass


def _start_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return

    def make_img(color=(128, 128, 128)):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([4, 4, 60, 60], fill=(*color, 255))
        return img

    menu = pystray.Menu(
        pystray.MenuItem("Preferences…",    lambda i, it: GLib.idle_add(open_prefs)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Test: Thinking",  lambda i, it: GLib.idle_add(_write_test_session, "thinking")),
        pystray.MenuItem("Test: Coding",    lambda i, it: GLib.idle_add(_write_test_session, "coding")),
        pystray.MenuItem("Test: Waiting",   lambda i, it: GLib.idle_add(_write_test_session, "waiting")),
        pystray.MenuItem("Test: Done",      lambda i, it: GLib.idle_add(_write_test_session, "done")),
        pystray.MenuItem("Test: Clear",     lambda i, it: GLib.idle_add(_write_test_session, "idle")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda i, it: (i.stop(), GLib.idle_add(Gtk.main_quit))),
    )
    icon = pystray.Icon("ClaudeBlinker", make_img(), "ClaudeBlinker", menu)
    State.tray_icon = icon
    State._tray_make_image = make_img
    import threading
    threading.Thread(target=icon.run, daemon=True).start()


# ---------- prefs window ----------

class PrefsWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="ClaudeBlinker")
        self.set_border_width(12)
        self.set_default_size(500, 400)
        self.connect("delete-event", self._close)
        nb = Gtk.Notebook()
        self.add(nb)
        nb.append_page(self._blinker_tab(), Gtk.Label(label="Blinker Settings"))
        nb.append_page(self._terminals_tab(), Gtk.Label(label="Terminals"))
        self.show_all()

    def _close(self, *_):
        State.preview_mode = False
        if State.dot_window:
            State.dot_window.queue_draw()
        self.hide()
        return True  # don't destroy

    def _blinker_tab(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=14)
        grid.set_border_width(16)
        cfg = State.config
        r = 0

        grid.attach(Gtk.Label(label="Dot size:", xalign=0), 0, r, 1, 1)
        sa = Gtk.Adjustment(value=int(cfg["size"]), lower=6, upper=60, step_increment=1)
        ss = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=sa)
        ss.set_hexpand(True)
        ss.set_draw_value(True)
        def _size_changed(s):
            State.config["size"] = int(s.get_value())
            save_config(State.config)
            if State.dot_window:
                State.dot_window.apply_config()
        ss.connect("value-changed", _size_changed)
        grid.attach(ss, 1, r, 1, 1)
        r += 1

        grid.attach(Gtk.Label(label="Blink speed (s):", xalign=0), 0, r, 1, 1)
        ba = Gtk.Adjustment(value=float(cfg["blink_interval"]), lower=0.1, upper=2.0, step_increment=0.05)
        bs = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=ba)
        bs.set_hexpand(True)
        bs.set_draw_value(True)
        bs.set_digits(2)
        def _blink_changed(s):
            State.config["blink_interval"] = round(s.get_value(), 2)
            save_config(State.config)
            _reschedule_blink()
        bs.connect("value-changed", _blink_changed)
        grid.attach(bs, 1, r, 1, 1)
        r += 1

        grid.attach(Gtk.Label(label="Position (°):", xalign=0), 0, r, 1, 1)
        pa = Gtk.Adjustment(value=int(cfg.get("position_angle", 0)), lower=-180, upper=180, step_increment=1)
        ps = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=pa)
        ps.set_hexpand(True)
        ps.set_draw_value(True)
        for ang in SNAP_POINTS:
            ps.add_mark(ang, Gtk.PositionType.BOTTOM, None)
        def _pos_changed(s):
            raw = int(s.get_value())
            snapped = next((sp for sp in SNAP_POINTS if abs(raw - sp) <= SNAP_ZONE_DEG), raw)
            if snapped != raw:
                s.set_value(snapped)
            State.config["position_angle"] = snapped
            save_config(State.config)
            if State.dot_window:
                State.dot_window.apply_config()
        ps.connect("value-changed", _pos_changed)
        grid.attach(ps, 1, r, 1, 1)
        r += 1

        lbl_box = Gtk.Box()
        for txt in ["Bot", "Left", "Top", "Right", "Bot"]:
            lbl = Gtk.Label(label=txt)
            lbl.set_hexpand(True)
            lbl_box.pack_start(lbl, True, True, 0)
        grid.attach(lbl_box, 1, r, 1, 1)
        r += 2

        btns = Gtk.Box(spacing=8)
        rb = Gtk.Button(label="Reset defaults")
        rb.connect("clicked", self._reset)
        qb = Gtk.Button(label="Quit Blinker")
        qb.connect("clicked", lambda _: Gtk.main_quit())
        btns.pack_start(rb, False, False, 0)
        btns.pack_end(qb, False, False, 0)
        grid.attach(btns, 0, r, 2, 1)
        return grid

    def _terminals_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(12)

        self._track_all_cb = Gtk.CheckButton(label="Track all sessions")
        self._track_all_cb.set_active(bool(State.config.get("track_all", True)))
        self._track_all_cb.connect("toggled", self._track_all_changed)
        vbox.pack_start(self._track_all_cb, False, False, 0)

        hint = Gtk.Label(label="When off, only checked sessions trigger the blinker.")
        hint.set_xalign(0)
        vbox.pack_start(hint, False, False, 0)

        rb = Gtk.Button(label="Refresh")
        rb.set_halign(Gtk.Align.END)
        rb.connect("clicked", lambda _: self.refresh_sessions())
        vbox.pack_start(rb, False, False, 0)

        self._store = Gtk.ListStore(bool, str, str, str)
        tree = Gtk.TreeView(model=self._store)

        tr = Gtk.CellRendererToggle()
        tr.connect("toggled", self._session_toggled)
        tree.append_column(Gtk.TreeViewColumn("Track", tr, active=0))

        col_f = Gtk.TreeViewColumn("Folder", Gtk.CellRendererText(), text=2)
        col_f.set_expand(True)
        tree.append_column(col_f)
        tree.append_column(Gtk.TreeViewColumn("State", Gtk.CellRendererText(), text=3))

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(tree)
        sw.set_vexpand(True)
        vbox.pack_start(sw, True, True, 0)

        self.refresh_sessions()
        return vbox

    def refresh_sessions(self):
        sessions = list_sessions()
        track_all = bool(State.config.get("track_all", True))
        selected = set(State.config.get("tracked_sessions", []))
        self._store.clear()
        for s in sessions:
            folder = os.path.basename(s["cwd"].rstrip("/")) if s["cwd"] else s["id"][:10]
            self._store.append([track_all or s["id"] in selected, s["id"], folder, s["state"]])
        if hasattr(self, "_track_all_cb"):
            self._track_all_cb.set_active(track_all)

    def _reset(self, *_):
        State.config = dict(DEFAULT_CONFIG)
        save_config(State.config)
        if State.dot_window:
            State.dot_window.apply_config()
        self.hide()
        State.prefs_window = None
        open_prefs()

    def _track_all_changed(self, cb):
        State.config["track_all"] = cb.get_active()
        save_config(State.config)
        self.refresh_sessions()

    def _session_toggled(self, renderer, path):
        if bool(State.config.get("track_all", True)):
            return
        it = self._store.get_iter(path)
        sid = self._store[it][1]
        new_val = not self._store[it][0]
        self._store[it][0] = new_val
        sel = State.config.setdefault("tracked_sessions", [])
        if new_val:
            if sid not in sel:
                sel.append(sid)
        else:
            if sid in sel:
                sel.remove(sid)
        save_config(State.config)


def open_prefs():
    State.preview_mode = True
    if State.prefs_window is None:
        State.prefs_window = PrefsWindow()
    else:
        State.prefs_window.refresh_sessions()
        State.prefs_window.show_all()
        State.prefs_window.present()
    if State.dot_window:
        State.dot_window.queue_draw()


# ---------- main ----------

def main():
    BASE.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    State.config = load_config()

    State.dot_window = DotWindow()
    State.dot_window.apply_config()

    _reschedule_blink()
    GLib.timeout_add(POLL_INTERVAL_MS, _poll_tick)
    _start_tray()

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
