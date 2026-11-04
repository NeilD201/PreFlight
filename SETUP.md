# PreFlight setup

Get PreFlight running from a clean checkout. The release was verified on
macOS (Python 3.15); Python 3.10+ is required on all platforms.

> 🎬 **Hackathon demo video:** _link coming soon._

## 1. Get the code

```sh
git clone https://github.com/NeilD201/PreFlight.git
cd PreFlight   # whatever the folder is named when cloned
```

Any folder name works — imports resolve through the bundled `_pcb_bootstrap`
helper, not the directory name. Paths with spaces are supported.

## 2. Install Python 3.10+

- **macOS:** `brew install python` (or python.org installer).
- **Windows:** python.org installer — tick **"Add python.exe to PATH"**,
  or use `py -3` in place of `python` below.
- **Linux (Ubuntu/Debian):** `sudo apt install python3 python3-venv`.

Verify: `python3 --version` (Windows: `py -3 --version`).

## 3. Create the project environment

The environment lives inside the project folder (`.venv`):

Easiest option per OS (no manual venv needed):

- **Windows:** double-click **Start PCB Preflight.bat** — it creates
  `.venv`, installs dependencies, and starts the app.
- **Windows PowerShell alternative:** right-click **Start-Preflight.ps1** →
  Run with PowerShell (or `.\Start-Preflight.ps1` from the folder).
- **macOS:** double-click **Start PCB Preflight.command**.
- **macOS / Linux terminal:** `./start-preflight.sh`.

Manual setup (any OS, when you prefer to control the environment):

```sh
# macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate
```

```powershell
# Windows PowerShell:
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run once:
# Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

```bat
:: Windows Command Prompt:
py -3 -m venv .venv
.venv\Scripts\activate.bat
```

## 4. Install dependencies

```sh
pip install -r requirements.txt
pip install --target .deps -r requirements.txt
```

The second install vendors the PDF libraries into `.deps/` so the app can
read datasheet PDFs in a bounded subprocess. The app itself is otherwise
standard library only (no numpy, no web framework to install).

## 5. Configure the Meta API key (optional but recommended)

PreFlight's AI uses **Meta Muse Spark 1.3 Contributor** exclusively, via
`https://api.meta.ai/v1/chat/completions`. There is no free tier and no
provider fallback: every AI review, answer, and repair assessment is billed
to your key (currently $0.10 in / $0.002 cached / $0.20 out per 1M tokens).
A full board review typically costs a few cents; the top bar shows live
spend and token usage.

Without a key, everything local still works: parsing, deterministic checks,
questions, reports, exports, deterministic fixes. AI review, AI chat, and
AI-planned repairs are unavailable (reported as such, never faked).

Set the key with the exact variable name the app reads — `LLAMA_API_KEY`:

```sh
# macOS / Linux, session only:
export LLAMA_API_KEY=your-key-here

# Windows PowerShell, session only:
$env:LLAMA_API_KEY="your-key-here"

# Windows cmd.exe, session only:
set LLAMA_API_KEY=your-key-here

# Any OS, remembered for this project (untracked file, never commit it):
cp .env.example .env        # macOS / Linux / Windows PowerShell
copy .env.example .env      # Windows Command Prompt
# then edit .env and set LLAMA_API_KEY=your-key-here
```

The app also accepts `.env.local`, and on macOS offers Keychain storage
under the `preflightinapp` service via **Vector settings → Remember**.

Verify without spending on a board analysis:

```sh
python main.py --test-ai            # macOS / Linux
py -3 main.py --test-ai            # Windows (if py launcher)
```

This sends one small structured request and reports success or the exact
auth/connectivity error. No key is printed in logs or stored in reports.

## 6. Install KiCad 9+ (optional, recommended)

KiCad supplies the netlist/ERC/DRC engine, fabrication export, and the
Python bindings used for board edits. Without it, those checks report
**NOT PERFORMED** — never passed.

- **macOS:** install KiCad.app (tested with 9.0.5). Auto-detected at
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
- **Windows:** install KiCad 9 (64-bit). Auto-detected via `PATH` and
  `C:\Program Files\KiCad\<7.0|8.0|9.0>\bin\kicad-cli.exe`.
- **Linux:** install via package manager (`kicad`), detected via `PATH`.
- **Override anywhere:** `--kicad-cli /path/to/kicad-cli`
  (or `KICAD_PYTHON=/path/to/python` for the edit runtime).

Verify detection: start the app, link any project, and check the review —
native-check rows show versions or a clear unavailable notice.

Board edits additionally need KiCad's Python (`pcbnew`): the macOS bundle
Python, `C:\Program Files\KiCad\<ver>\bin\python.exe` on Windows, or
`KICAD_PYTHON`. Without it, Fix Design reports the missing runtime instead
of editing blindly.

## 7. Launch

```sh
python main.py --web
```

Then open **http://127.0.0.1:8765** in a browser (the exact URL is printed;
add `--port 8766` if the default port is busy). The version pill in the top
bar (currently `v1.04`) confirms which code is serving.

Useful variants:

```sh
python main.py --web --no-browser     # start without opening a browser
python main.py --web --no-ai          # start with AI review unchecked
python main.py --web /path/to/project # prefill another project folder
```

Stop with Ctrl+C in the terminal.

## 8. First run (judge walkthrough, ~20 minutes with AI)

1. Open the **Projects** page — both bundled demo boards
   (**ModularKeyboard wrong**, **ModularKeyboard correct**) are listed with
   an **Open board** button. No upload needed.
2. Open **ModularKeyboard wrong** and run the Vector review with
   **Include Vector review** ticked (needs the Meta key from step 5 of the
   setup above; without a key leave it unticked for a free local analysis).
3. Answer the product questions, or use **Enter all details** to answer
   everything on one page. “Not sure yet” is always acceptable.
4. Open the finished report: findings cite components, nets, pins, and
   evidence. With the AI review enabled, the USB finding names an exact
   orderable part (**SRV05-4**) with quoted datasheet excerpts
   (`D:C2836319`); without a key you still get the deterministic findings
   (e.g. R001 capacitor rails, R005 LED resistors).
5. Try **Ask follow-up** on any finding (Vector review tab), and
   **Fix this issue** on a supported finding (deterministic value fixes
   apply in seconds with no key; research-backed repairs need the key).
   Repairs apply to disposable copies and are re-verified.

## 9. Reading a verdict

- **Supported / fixed** — a check ran and the evidence meets the
  requirement, or a repair was applied to a disposable copy and re-verified.
- **FAIL / finding** — a check ran and the evidence contradicts it.
- **NOT VERIFIED** — evidence is incomplete or needs a measurement, a
  simulation, or your answer. Never read as safe.
- **NOT PERFORMED / unavailable** — the check could not run (no KiCad, no
  PDFs, no key). Reported, never passed silently.

## 10. Firmware (experimental)

Supported: nRF52 bare-metal C and RP2040 Pico SDK C (GPIO bring-up, I2C
HAL, UF2 output). Not supported: QMK, auto-flashing, hardware-in-the-loop
testing. Building requires external toolchains (`arm-none-eabi-gcc`,
`cmake`, Pico SDK / nRF tools) — install those per their own docs; the app
reports missing tools instead of pretending to compile.

## 11. Troubleshooting

- **Port in use:** add `--port 8766` (or any free port).
- **`kicad-cli` not found:** install KiCad 9+, or pass `--kicad-cli`.
  Checks that need it stay unavailable.
- **PDF datasheets unavailable:** rerun
  `pip install --target .deps -r requirements.txt` with the venv active.
- **AI fails or times out:** local results are preserved; use **Retry AI
  review**. Check spend in the top bar — limits are per-action request
  counts, not a spending cap.
- **Python too old:** `python3 --version` must be 3.10+.
- **Windows `python` not found:** use `py -3`, or tick "Add python.exe to
  PATH" during install.
