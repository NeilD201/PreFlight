# PreFlight — 3-Minute Demo Guide

Record against the release candidate directory below. Do not invent results:
every screen in this script was verified with real browser tests.

## Launch

Directory: the release-candidate folder handed off with this guide
(`PreFlight-ReleaseCandidate`).

```bash
cd /tmp/PreFlight-ReleaseCandidate
python3 main.py --web --port 8823 --no-browser
```

Open: **http://127.0.0.1:8823** (any free port works: replace `8823`).

No API key is needed for the local demo. A Meta key is only required for
the optional Vector review at the end.

## The 3-minute run

**0:00 — Projects.** Click **Projects**. Two bundled demo boards are listed:
**ModularKeyboard wrong** (faulty) and **ModularKeyboard correct** (fixed).

**0:20 — Open the faulty board.** Click **Open board** on ModularKeyboard
wrong. The workspace renders with a visible **Check my board** button.

**0:40 — Local analysis (free).** Make sure AI review and datasheet lookups
are off, then click **Check my board**. Deterministic KiCad checks run
locally — no API credits, no key needed. Watch findings appear.

**1:10 — The evidence-aware C105 finding.** Open the finding
**C105 (+1 same-value part)**. It names the rails (+5V, +3.3V), the
regulators, and every powered load pin (D100 pin 4 VDD, U3, U8) — not
generic rating advice. Steps name exactly what is unverified.

**1:40 — Saved Vector investigation (no new spend).** Open **Projects**,
find the saved **falsch** report with a completed Vector review, and click
**Open review**. The Vector review tab shows validated AI actions —
e.g. **R001** on C105/C106 with the badge
**AI guidance from supplied evidence** — plus **Review diagnostics**
listing the one rejected conclusion and why. Replaying this costs nothing.

**2:10 — Project description.** Click **Product details**. The
**Tell PreFlight about your project** box leads: type a description, click
**Save & analyse**. Saving spends nothing; a paid Vector run always asks
first (**Run Vector investigation** / **Save only**).

**2:40 — Fix Design (safe).** Open a finding and click
**Plan coordinated fix**. The plan is prepared on an isolated copy;
nothing edits the original files until approval and export.

## Do NOT demonstrate

- Breadboard to PCB / LiveScan (experimental, not part of this release).
- Firmware generation (experimental, labeled as such in the UI).
- A fresh paid Vector review on camera (it takes minutes and spends
  credits; show the saved completed review instead).

## Honest limitations to state on camera

- Local checks cannot certify a board safe; unresolved items stay marked.
- A finding without a retrieved IC datasheet names the missing part
  instead of guessing.
- The 30-minute in-memory review cache reuses only identical, fully
  validated reviews; any changed evidence, source file, context, model,
  or credential runs fresh.
