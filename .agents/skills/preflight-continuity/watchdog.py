#!/usr/bin/env python3
"""PreFlight continuity watchdog (companion to the preflight-continuity skill).

Reads the last-progress timestamp from state.json (Europe/Berlin), compares
against now, and — after --threshold-minutes (default 30) of inactivity —
appends a structured recovery task to DEBUG_REPORT.md under a ## Recovery
section and prints the recovery message for the on-duty agent to send to
Builder rural-nova.

It never spawns agents, sends messages, modifies production code, spends API
budget, or bypasses approvals. Safe to run from cron or a shell loop:

    */5 * * * * /usr/bin/python3 .agents/skills/preflight-continuity/watchdog.py --check
    python3 .agents/skills/preflight-continuity/watchdog.py --progress --note "R2 done: FAIL, wire ends None"
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get(
    "PREFLIGHT_CONTINUITY_STATE", "/tmp/preflight-continuity-state.json"
)
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
REPORT = os.path.join(WORKSPACE, "DEBUG_REPORT.md")


def now_berlin():
    return datetime.now(BERLIN)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(note):
    state = {"last_progress": now_berlin().isoformat(timespec="seconds"), "note": note}
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    print("progress recorded:", state["last_progress"], "-", note)


def check(threshold_min, builder="rural-nova"):
    state = load_state()
    last = state.get("last_progress")
    if not last:
        print("NO-STATE: no progress recorded yet; recording now as baseline.")
        save_state("watchdog baseline (no prior progress)")
        return 0
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=BERLIN)
    except ValueError:
        print("BAD-STATE: unparseable timestamp; treating as stale.")
        last_dt = now_berlin() - timedelta(minutes=threshold_min + 1)
    idle = (now_berlin() - last_dt).total_seconds() / 60
    print("last progress: %s (%.1f min ago, threshold %d min)" % (last, idle, threshold_min))
    if idle < threshold_min:
        print("OK: loop active, nothing to do.")
        return 0
    msg = (
        "RECOVERY-TRIGGERED after %.1f min without progress.\n"
        "To: Builder %s — last completed task: %s (at %s). "
        "Current blocker: inactivity, cause under investigation — read DEBUG_REPORT.md tail, "
        "check Builder messages/file sections, verify browser lock + server reachability. "
        "Next required action: resume automatic breadboard wire recognition loop. "
        "Send this via session messaging; on failure append it under ## Recovery in DEBUG_REPORT.md."
        % (idle, builder, state.get("note", "unknown"), last)
    )
    entry = "\n## Recovery (%s)\n- %s\n" % (now_berlin().strftime("%Y-%m-%d %H:%M %Z"), msg.replace("\n", " "))
    try:
        with open(REPORT, "a") as f:
            f.write(entry)
        print("recovery task appended to DEBUG_REPORT.md")
    except OSError as e:
        print("FILE-FALLBACK-FAILED:", e)
        return 2
    print(msg)
    print("NOTE: a skill/watchdog cannot wake an idle session or send messages itself; "
          "an active agent must deliver the message above and confirm acknowledgement.")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--threshold-minutes", type=int, default=30)
    args = ap.parse_args()
    if args.progress:
        save_state(args.note or "manual progress mark")
        return 0
    if args.check:
        return check(args.threshold_minutes)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
