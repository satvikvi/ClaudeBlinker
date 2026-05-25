#!/usr/bin/env python3
"""Claude Blinker (Windows) — floating dot overlay for Claude Code state.

State is per-Claude-Code-session. Hooks write to
  %USERPROFILE%\\.claude-blinker\\sessions\\<session_id>\\{state, info.json}
and the app aggregates the state of all tracked sessions.
"""
import json
import os
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

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
    "waiting":  "#FF3B30",
    "coding":   "#007AFF",
    "thinking": "#FFCC00",
    "done":     "#34C759",
}

# This exact color is made transparent so the dot floats on a see-through window.
# Avoid using this color as a state color.
_TRANSPARENT = "#010101"


class _State:
    config = dict(DEFAULT_CONFIG)
    current = "idle"
    blink_on = True
    preview_mode = False
    root = None
    dot = None
    canvas = None
    prefs = None
    blink_after_id = None
    tray_icon = None
    _tray_make_image = None
    _cb_queue = queue.Queue()

State = _State()

# Treeview reference kept here so refresh_sessions() can reach it without
# passing it through every call chain.
_prefs_tree = None


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
    """Returns (x, y) top-left screen coords for the dot window."""
    W = State.root.winfo_screenwidth()
    H = State.root.winfo_screenheight()
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

    # Windows screen coords: y=0 at top, increases downward
    return int(cx - size / 2.0), int((H - cy) - size / 2.0)


# ---------- dot window ----------

def make_dot_window():
    dot = tk.Toplevel(State.root)
    dot.overrideredirect(True)
    dot.attributes("-topmost", True)
    dot.attributes("-transparentcolor", _TRANSPARENT)
    dot.config(bg=_TRANSPARENT)
    try:
        dot.wm_attributes("-toolwindow", True)  # hide from taskbar
    except tk.TclError:
        pass

    canvas = tk.Canvas(dot, bg=_TRANSPARENT, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.bind("<Button-1>", lambda e: open_prefs())
    canvas.bind("<Button-3>", _show_dot_menu)

    State.dot = dot
    State.canvas = canvas


def apply_config():
    size = max(4, int(State.config.get("size", 16)))
    angle = float(State.config.get("position_angle", 0))
    x, y = compute_dot_origin(size, angle)
    State.dot.geometry(f"{size}x{size}+{x}+{y}")
    _redraw_dot()
    _reschedule_blink()


def _redraw_dot():
    canvas = State.canvas
    if canvas is None:
        return
    canvas.delete("all")
    s = State.current
    color = STATE_COLORS.get(s)
    if color is None:
        if State.preview_mode and State.blink_on:
            color = "#000000"
        else:
            return
    elif s == "waiting" and not State.blink_on:
        return

    size = max(4, int(State.config.get("size", 16)))
    canvas.create_oval(1, 1, size - 1, size - 1, fill=color, outline="")


def _show_dot_menu(event):
    menu = tk.Menu(State.root, tearoff=0)
    menu.add_command(
        label=f"Status: {STATE_LABELS.get(State.current, State.current)}",
        state="disabled",
    )
    menu.add_separator()
    for label, sname in [
        ("Test: Thinking (yellow)", "thinking"),
        ("Test: Coding (blue)",     "coding"),
        ("Test: Waiting (red)",     "waiting"),
        ("Test: Done (green)",      "done"),
        ("Test: Clear (idle)",      "idle"),
    ]:
        menu.add_command(label=label, command=lambda s=sname: _write_test_session(s))
    menu.add_separator()
    menu.add_command(label="Preferences…", command=open_prefs)
    menu.add_separator()
    menu.add_command(label="Quit ClaudeBlinker", command=_quit)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _write_test_session(sname):
    sd = SESSIONS_DIR / TEST_SESSION
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "state").write_text(sname)
    (sd / "info.json").write_text(json.dumps({"cwd": "(menu test)", "last_seen": time.time()}))


# ---------- timers ----------

def _reschedule_blink():
    if State.blink_after_id is not None:
        State.root.after_cancel(State.blink_after_id)
    interval_ms = max(50, int(State.config.get("blink_interval", 0.5) * 1000))
    State.blink_after_id = State.root.after(interval_ms, _blink_tick)


def _blink_tick():
    if State.current == "waiting" or (State.preview_mode and State.current == "idle"):
        State.blink_on = not State.blink_on
        _redraw_dot()
    _reschedule_blink()


def _poll_tick():
    # Drain cross-thread callbacks (from tray icon running in a background thread)
    while not State._cb_queue.empty():
        try:
            State._cb_queue.get_nowait()()
        except Exception:
            pass

    new_state = aggregate_state()
    if new_state != State.current:
        State.current = new_state
        State.blink_on = True
        _redraw_dot()
        _update_tray()

    if State.prefs is not None and _prefs_exists():
        refresh_sessions()

    if TRIGGER_FILE.exists():
        try:
            TRIGGER_FILE.unlink()
        except OSError:
            pass
        open_prefs()

    State.root.after(POLL_INTERVAL_MS, _poll_tick)


def _prefs_exists():
    try:
        return State.prefs.winfo_exists() and State.prefs.winfo_viewable()
    except Exception:
        return False


def _quit():
    if State.tray_icon:
        try:
            State.tray_icon.stop()
        except Exception:
            pass
    State.root.destroy()


# ---------- optional tray icon (pystray + pillow) ----------

def _update_tray():
    if not State.tray_icon or not State._tray_make_image:
        return
    hex_color = STATE_COLORS.get(State.current)
    if hex_color:
        color = (int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16))
    else:
        color = (128, 128, 128)
    try:
        State.tray_icon.icon = State._tray_make_image(color)
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

    def enqueue(fn):
        State._cb_queue.put(fn)

    menu = pystray.Menu(
        pystray.MenuItem("Preferences…",    lambda i, it: enqueue(open_prefs)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Test: Thinking",  lambda i, it: enqueue(lambda: _write_test_session("thinking"))),
        pystray.MenuItem("Test: Coding",    lambda i, it: enqueue(lambda: _write_test_session("coding"))),
        pystray.MenuItem("Test: Waiting",   lambda i, it: enqueue(lambda: _write_test_session("waiting"))),
        pystray.MenuItem("Test: Done",      lambda i, it: enqueue(lambda: _write_test_session("done"))),
        pystray.MenuItem("Test: Clear",     lambda i, it: enqueue(lambda: _write_test_session("idle"))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda i, it: enqueue(_quit)),
    )
    icon = pystray.Icon("ClaudeBlinker", make_img(), "ClaudeBlinker", menu)
    State.tray_icon = icon
    State._tray_make_image = make_img
    threading.Thread(target=icon.run, daemon=True).start()


# ---------- prefs window ----------

def open_prefs():
    State.preview_mode = True
    _redraw_dot()
    if State.prefs is not None and _prefs_exists():
        State.prefs.lift()
        State.prefs.focus_force()
        return
    _build_prefs()


def _build_prefs():
    global _prefs_tree
    w = tk.Toplevel(State.root)
    w.title("ClaudeBlinker")
    w.geometry("520x420")
    w.resizable(True, True)
    w.protocol("WM_DELETE_WINDOW", lambda: _close_prefs(w))
    State.prefs = w

    nb = ttk.Notebook(w)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    # --- Blinker tab ---
    bf = ttk.Frame(nb, padding=16)
    nb.add(bf, text="Blinker Settings")
    bf.columnconfigure(1, weight=1)

    ttk.Label(bf, text="Dot size:").grid(row=0, column=0, sticky="w", pady=6)
    size_var = tk.IntVar(value=int(State.config["size"]))
    size_lbl = ttk.Label(bf, text=f"{size_var.get()} px")
    ttk.Scale(bf, from_=6, to=60, variable=size_var, orient="horizontal").grid(
        row=0, column=1, sticky="ew", pady=6
    )
    size_lbl.grid(row=0, column=2, padx=8)
    def _size_changed(*_):
        State.config["size"] = int(size_var.get())
        size_lbl.config(text=f"{State.config['size']} px")
        save_config(State.config)
        apply_config()
    size_var.trace_add("write", _size_changed)

    ttk.Label(bf, text="Blink speed (s):").grid(row=1, column=0, sticky="w", pady=6)
    blink_var = tk.DoubleVar(value=float(State.config["blink_interval"]))
    blink_lbl = ttk.Label(bf, text=f"{blink_var.get():.2f} s")
    ttk.Scale(bf, from_=0.1, to=2.0, variable=blink_var, orient="horizontal").grid(
        row=1, column=1, sticky="ew", pady=6
    )
    blink_lbl.grid(row=1, column=2, padx=8)
    def _blink_changed(*_):
        State.config["blink_interval"] = round(blink_var.get(), 2)
        blink_lbl.config(text=f"{State.config['blink_interval']:.2f} s")
        save_config(State.config)
        _reschedule_blink()
    blink_var.trace_add("write", _blink_changed)

    ttk.Label(bf, text="Position (°):").grid(row=2, column=0, sticky="w", pady=6)
    pos_var = tk.IntVar(value=int(State.config.get("position_angle", 0)))
    pos_lbl = ttk.Label(bf, text=f"{pos_var.get()}°")
    ttk.Scale(bf, from_=-180, to=180, variable=pos_var, orient="horizontal").grid(
        row=2, column=1, sticky="ew", pady=6
    )
    pos_lbl.grid(row=2, column=2, padx=8)
    def _pos_changed(*_):
        raw = int(pos_var.get())
        snapped = next((sp for sp in SNAP_POINTS if abs(raw - sp) <= SNAP_ZONE_DEG), raw)
        if snapped != raw:
            pos_var.set(snapped)
        pos_lbl.config(text=f"{snapped}°")
        State.config["position_angle"] = snapped
        save_config(State.config)
        apply_config()
    pos_var.trace_add("write", _pos_changed)

    ttk.Label(bf, text="Bot   Left   Top   Right   Bot", foreground="gray").grid(
        row=3, column=1, sticky="ew"
    )

    btn_f = ttk.Frame(bf)
    btn_f.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(20, 0))
    ttk.Button(btn_f, text="Reset defaults", command=_reset_defaults).pack(side="left")
    ttk.Button(btn_f, text="Quit Blinker", command=_quit).pack(side="right")

    # --- Terminals tab ---
    tf = ttk.Frame(nb, padding=12)
    nb.add(tf, text="Terminals")

    track_var = tk.BooleanVar(value=bool(State.config.get("track_all", True)))
    ttk.Checkbutton(
        tf, text="Track all sessions", variable=track_var,
        command=lambda: _track_all_changed(track_var.get()),
    ).pack(anchor="w")
    ttk.Label(
        tf, text="When off, only checked sessions trigger the blinker.", foreground="gray"
    ).pack(anchor="w")
    ttk.Button(tf, text="Refresh", command=refresh_sessions).pack(anchor="e", pady=4)

    tree = ttk.Treeview(tf, columns=("track", "folder", "state"), show="headings", height=10)
    tree.heading("track",  text="Track");  tree.column("track",  width=60,  anchor="center")
    tree.heading("folder", text="Folder"); tree.column("folder", width=260)
    tree.heading("state",  text="State");  tree.column("state",  width=100)
    tree.pack(fill="both", expand=True)
    tree.bind("<ButtonRelease-1>", _tree_click)
    _prefs_tree = tree

    refresh_sessions()
    w.lift()
    w.focus_force()


def _close_prefs(w):
    State.preview_mode = False
    _redraw_dot()
    w.withdraw()


def _reset_defaults():
    State.config = dict(DEFAULT_CONFIG)
    save_config(State.config)
    apply_config()
    if State.prefs and _prefs_exists():
        State.prefs.destroy()
        State.prefs = None
    open_prefs()


def _track_all_changed(val):
    State.config["track_all"] = val
    save_config(State.config)
    refresh_sessions()


def refresh_sessions():
    global _prefs_tree
    tree = _prefs_tree
    if tree is None:
        return
    try:
        if not tree.winfo_exists():
            return
    except Exception:
        return
    sessions = list_sessions()
    track_all = bool(State.config.get("track_all", True))
    selected = set(State.config.get("tracked_sessions", []))
    for item in tree.get_children():
        tree.delete(item)
    for s in sessions:
        tracked = track_all or s["id"] in selected
        folder = os.path.basename(s["cwd"].rstrip("/\\")) if s["cwd"] else s["id"][:10]
        check = "☑" if tracked else "☐"
        tree.insert("", "end", iid=s["id"], values=(check, folder, s["state"]))


def _tree_click(event):
    tree = _prefs_tree
    if tree is None:
        return
    row = tree.identify_row(event.y)
    col = tree.identify_column(event.x)
    if not row or col != "#1":
        return
    if bool(State.config.get("track_all", True)):
        return
    sel = State.config.setdefault("tracked_sessions", [])
    if row in sel:
        sel.remove(row)
    else:
        sel.append(row)
    save_config(State.config)
    refresh_sessions()


# ---------- main ----------

def main():
    BASE.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    State.config = load_config()

    root = tk.Tk()
    root.withdraw()  # hidden root; dot and prefs use Toplevel
    State.root = root

    make_dot_window()
    apply_config()

    root.after(POLL_INTERVAL_MS, _poll_tick)
    _start_tray()

    root.mainloop()


if __name__ == "__main__":
    main()
