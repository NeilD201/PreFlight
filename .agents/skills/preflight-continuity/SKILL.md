---
name: preflight-continuity
description: Keep the overnight PreFlight Builder-Debugger wire-recognition loop alive, logging progress and recovering after 30 minutes of inactivity.
---

# PreFlight Continuity

Use for the overnight PreFlight automatic-reconstruction operation only
(Builder `rural-nova` + Debugger `brisk-zenith`, deadline 2026-10-30 07:00 CET,
combined Meta budget $0.25). Purpose: record progress every cycle and recover
the loop after 30 minutes without meaningful progress. Never involve Supervisor.

## Progress log (every cycle, no exceptions)

After each development or verification cycle, append to `DEBUG_REPORT.md`:

- Cycle number, agent name, Berlin timestamp (`%Y-%m-%d %H:%M %Z`).
- What was attempted (fresh disposable project id, photo seeds/filenames).
- Evidence (API counts, spend delta, screenshot paths, test results).
- Verdict (PASS/FAIL) and the single next action with its owner.

The newest timestamp in this log is the last-progress time. Never backdate it.
Never log a cycle that did not run.

## Inactivity check

While the Debugger session is active, compare now (Europe/Berlin) against the
last-progress timestamp:

- Under 30 minutes: keep working, say nothing about continuity.
- 30 minutes or more: investigate first — read the tail of `DEBUG_REPORT.md`,
  check for Builder messages or file sections, verify the browser lock state
  and whether the live server is reachable. Then recover (below).

A skill cannot wake an idle session. For unattended stretches, run the
companion watchdog (same directory, `watchdog.py`) from an already-authorized
local scheduler or shell loop. The watchdog only writes state and recovery
tasks; it never spawns agents, sends messages, or bypasses approvals.

## Recovery (inactivity confirmed)

1. Send Builder `rural-nova` a recovery message containing exactly: last
   completed task + evidence, current blocker + how it was determined, next
   required action with owner, and the request to continue automatic
   breadboard wire recognition.
2. If direct messaging fails or is unacknowledged within one cycle, append
   the same recovery task under a `## Recovery` section in `DEBUG_REPORT.md`
   (Builder treats that file as the fallback channel).
3. Resume the next authorized development or verification cycle immediately.
   A failed reconstruction is never a stop reason on its own.

## Hard rules (never violated by this skill)

- Never fabricate wire endpoints or place/confim holes to avoid questions.
- Never answer in-app placement questions during acceptance runs.
- Never modify VECTOR or unrelated features; no commits or pushes; never
  delete original user projects.
- Respect the $0.25 combined Meta budget (stop paid calls if spend cannot be
  tracked) and the 07:00 CET deadline.
- One agent on Playwright at a time; announce taking/releasing the lock.
- Do not claim automatic recovery works until a real recovery message is
  delivered AND acknowledged.
