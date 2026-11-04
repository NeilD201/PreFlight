# PreFlight — 5-Minute Demo Guide (fresh clone)

Runs against a clean checkout of this repository. No saved reports, databases,
or API keys are assumed — every step below starts from the bundled files.

## Launch

```sh
git clone https://github.com/NeilD201/PreFlight.git
cd PreFlight
python3 -m venv .venv && source .venv/bin/activate   # Windows: py -3 -m venv .venv
pip install -r requirements.txt
python main.py --web
```

(Windows: `py -3 main.py --web`, or double-click **Start PCB Preflight.bat**;
macOS: double-click **Start PCB Preflight.command**, or `./start-preflight.sh`.
See SETUP.md for PowerShell vs Command Prompt details.)

Open the printed URL (default **http://127.0.0.1:8765**).

## The 5-minute run (no API key needed)

**Projects.** Open the **Projects** page. Two bundled demo boards are listed:
**ModularKeyboard wrong** (faulty) and **ModularKeyboard correct** (fixed).

**Open the faulty board.** Click **Open board** on ModularKeyboard wrong.
It opens as a disposable working copy — the bundled originals are never
modified. A visible **Check my board** button renders in the workspace.

**Local analysis (free).** Leave **Include Vector review** unticked (no key
needed) and click **Check my board**. Deterministic checks run locally;
findings appear with components, nets, pins, and evidence.

**Read verified findings.** All of these come from the free local analysis:

- **R001 — 100 µF capacitor (C105) (+1 same-value part):** names the supply
  rails (+5V, +3.3V), the regulators, and every powered load pin
  (D100 pin 4 VDD, U3, U8) — not generic rating advice. Steps name exactly
  what is unverified.
- **R005 — LED series resistance (R12, R13, R14):** the screening estimate
  (~0.13 mA) shows the 10 kΩ values starve the LEDs.

**Reset and isolation.** **Reset this project** restores an unreviewed,
pristine copy. **Open in KiCad** opens the disposable working copy, never
the bundled originals.

## With a Meta API key (optional)

Add the key in **Vector settings → Save & test** (or `LLAMA_API_KEY` in an
untracked `.env` file), then tick **Include Vector review** before
**Check my board**. Answer the product questions (**Enter all details** puts
them on one page; **Not sure yet** is always acceptable). The **Vector
review** tab then adds AI-verified guidance — e.g. the USB-C CC finding with
an exact orderable part and quoted datasheet excerpts — plus
**Review diagnostics** for anything the AI could not establish. Follow-up
questions (**Ask about this run** → **Send question**) reuse the saved
review evidence without rerunning KiCad. **Plan coordinated fix** prepares
repairs on an isolated copy; nothing edits originals without approval.

## Do NOT demonstrate

- Breadboard to PCB / LiveScan (experimental, not part of this release).
- Firmware generation (experimental, labeled as such in the UI).
- A fresh paid Vector review on camera (it takes minutes and spends
  credits; replay a saved completed review instead).

## Honest limitations to state on camera

- Local checks cannot certify a board safe; unresolved items stay marked.
- A finding without a retrieved IC datasheet names the missing part
  instead of guessing.
- Saved reviews are reused from memory for up to 30 minutes only when the
  evidence, sources, context, model, and credentials are identical;
  anything changed runs fresh.
- KiCad 9+ is required for native ERC/DRC and manufacturing checks;
  without it those checks report NOT PERFORMED.
