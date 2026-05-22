#!/usr/bin/env python3
"""Claude Code hook helper — writes per-session state for the blinker.

Usage:
    setstate.py waiting   # Notification hook (Claude wants permission)
    setstate.py done      # Stop hook (Claude finished a turn)
    setstate.py idle      # UserPromptSubmit hook (new turn started)

Reads JSON from stdin to identify the session (session_id, cwd) so that
the blinker can distinguish state across multiple Claude Code terminals.
"""
import json
import os
import subprocess
import sys
import time


def find_terminal_tty():
    """Walk up the process tree until we find an ancestor attached to a tty.
    Claude Code spawns hooks with stdin as a pipe, so /dev/tty isn't open
    on us — but our parent (or its parent) is the shell inside Terminal.app
    and does have one."""
    pid = os.getpid()
    for _ in range(20):  # safety bound on tree walk
        if pid <= 1:
            return ""
        try:
            r = subprocess.run(
                ["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                capture_output=True, text=True, check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        parts = r.stdout.strip().split(None, 1)
        if not parts:
            return ""
        ppid_s = parts[0]
        tty_s = parts[1].strip() if len(parts) > 1 else ""
        if tty_s and tty_s not in ("?", "??"):
            return tty_s if tty_s.startswith("/dev/") else f"/dev/{tty_s}"
        try:
            pid = int(ppid_s)
        except ValueError:
            return ""
    return ""


def main():
    if len(sys.argv) < 2:
        return 0
    new_state = sys.argv[1]

    data = {}
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
    except Exception:
        pass

    sid = data.get("session_id") or "unknown"
    cwd = data.get("cwd") or os.getcwd()

    # Smart upgrade: tools that always block on user input shouldn't show as
    # "coding" — flip them straight to "waiting" so the dot turns red instantly
    # without waiting for Claude Code's idle-Notification timer.
    INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
    if new_state == "coding":
        tool_name = data.get("tool_name") or ""
        if tool_name in INTERACTIVE_TOOLS:
            new_state = "waiting"

    base = os.path.expanduser("~/.claude-blinker/sessions")
    session_dir = os.path.join(base, sid)
    os.makedirs(session_dir, exist_ok=True)
    state_file = os.path.join(session_dir, "state")

    # Suppress idle-ping Notifications: Claude Code fires "Notification" both
    # for permission requests AND for "I'm waiting for input" pings after a
    # turn has finished. If state is already "done" when a "waiting" arrives,
    # treat it as the idle ping and keep "done".
    if new_state == "waiting" and os.path.exists(state_file):
        try:
            if open(state_file).read().strip() == "done":
                return 0
        except OSError:
            pass

    with open(state_file, "w") as f:
        f.write(new_state)

    info = {"cwd": cwd, "last_seen": time.time(), "tty": find_terminal_tty()}
    with open(os.path.join(session_dir, "info.json"), "w") as f:
        json.dump(info, f)

    return 0


if __name__ == "__main__":
    sys.exit(main())
