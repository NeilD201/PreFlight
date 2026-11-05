# PCB Preflight — circuit review and manufacturing handoff

_Preflight was completely built during the hackathon — none of it was started before the hackathon kickoff._

> 🎬 **Hackathon demo video:** _link coming soon — placeholder for the recorded
> walkthrough (wrong board → findings → SRV05-4 prescription → disposable fix)._

Read a KiCad project folder or ZIP, compare schematic and PCB components and
connections, run KiCad's electrical and board rule checks, explain their practical
significance, and optionally ask AI to review the structured evidence. Requires
Python 3.10 or newer. The local checks use the standard library; PDF reading uses pypdf.
KiCad 9+ supplies the optional netlist/ERC/DRC engine (tested with KiCad 9.0.5).
See [project structure and data flow](docs/PROJECT_STRUCTURE.md) for a source
map, outbound service list, and local-data notes before publishing or sharing.
Two bundled demo boards (`demo-projects/ModularKeyboard wrong` and
`demo-projects/ModularKeyboard correct`) open with one click from the Projects
page — no upload needed. Both boards are the developer's WenneBoard project:
[wrong-board context](demo-projects/ModularKeyboard%20wrong/preset-context.md)
and [correct-board context](demo-projects/ModularKeyboard%20correct/preset-context.md)
(full text in [DEMO_GUIDE.md](DEMO_GUIDE.md)). After opening a demo and
running an analysis, load it via **Project context → Load preset → Save
context** so Vector can use the detailed WenneBoard information in
future reviews.

## Quick Start for Judges (under two minutes)

1. **Install:** Python 3.10+ (python.org, or `py -3` on Windows), then in the
   cloned folder create and activate a venv (exact PowerShell vs Command
   Prompt commands in SETUP.md §3), then `pip install -r requirements.txt`.
   Optional but recommended: KiCad 9+ (kicad.org) for native ERC/DRC and
   manufacturing checks — without it those checks report NOT PERFORMED
   instead of guessing.
2. **Launch:** `python main.py --web` and open the printed URL.
3. **API key (only needed for AI answers):** get a Meta API key with Muse
   Spark 1.3 Contributor access, then paste it in **Vector settings → Save &
   test**, or put `LLAMA_API_KEY=your-key` in an untracked `.env` file
   (see `.env.example`). Without a key, all local checks, findings, and
   previews still work; AI review and Vector answers report unavailable.
4. **Demo:** open **ModularKeyboard Wrong**, press **Check my board**, inspect
   the C105 capacitor and LED-resistor findings (USB-C CC detail and Vector
   follow-ups need the key), then open **ModularKeyboard Correct** to
   compare. See the full walkthrough below.

## What PreFlight is (and is not)

PreFlight reviews an **existing PCB design before manufacturing**: it reads your
KiCad schematic and PCB, checks that they agree, runs the electrical and
manufacturing rules that matter for a working board, and explains each finding
with evidence you can inspect. It was built because raw DRC output tells you
*what* violates a rule but not *whether it matters* — and because generic AI
chat answers printed-circuit questions without ever reading the design files.

PreFlight is **not an AI PCB generator** and does not lay out boards. Deterministic
checks (parsing, connectivity, ERC/DRC, manufacturing rules) always run locally
and are free. Meta Muse Spark is used only as a reasoning assistant over that
evidence — citations required, nothing accepted on a model's word alone.

## Judge walkthrough (about 10 minutes, no API key needed for steps 1–5)

1. Install and launch: `python3 -m venv .venv && source .venv/bin/activate`
   (Windows: `py -3 -m venv .venv`), `pip install -r requirements.txt`,
   then `python main.py --web` and open the printed URL.
2. On the Projects page, open **ModularKeyboard Wrong** (display name for the
   bundled `ModularKeyboard wrong` demo). It opens as a disposable working
   copy — the bundled originals are never modified.
3. Press **Check my board**. The local analysis runs on your machine (tens of
   seconds) with no API key.
4. Open **R001 — 100 µF capacitor (C105)**: rails (+5V, +3.3V), regulators,
   and every powered load pin, with steps naming exactly what is unverified.
   Open **R005 — LED series resistance (R12–R14)** for the screening
   estimate. (The USB-C CC pull-down detail appears with a Meta key's
   Vector review, not in the free local pass.)
5. Open a finding: schematic/PCB evidence, the pins and nets involved, and
   what would need to change. With a Meta key configured, ask Vector a
   follow-up (e.g. why the value matters); without a key this step reports
   itself as unavailable instead of guessing.
6. Open **ModularKeyboard Correct** and compare: the same checks pass there.
7. Explore the other findings (regulator pads, copper clearance, ESD
   protection) and the board preview, schematic view, and manufacturing
   checks.

A full AI review of a 400+-part board takes several minutes and a few cents of
Meta credit; nothing in the walkthrough above requires it.

## Reading a verdict: PASS, FAIL, NOT VERIFIED, NOT PERFORMED

- **Supported / fixed:** a deterministic check ran and the evidence meets the
  requirement, or a repair was applied to a disposable copy and re-verified.
- **FAIL / finding:** a check ran and the evidence contradicts the requirement.
  Each finding cites components, nets, pins, and source evidence.
- **NOT VERIFIED:** the evidence is incomplete, contradictory, or needs a
  measurement, simulation, or answer only you can give. Nothing is declared
  safe on missing evidence.
- **NOT PERFORMED / unavailable:** a check could not run (for example KiCad or
  datasheet PDFs are missing, or no Meta key is configured). The report says
  so instead of passing silently.

PreFlight never invents a specification, never treats an AI opinion as proof
on its own, and never edits your original KiCad files — repairs apply to
disposable copies only.

## Keep AI usage low

For a model-directed engineering investigation, choose **Vector settings → Vector
usage → Vector investigation · detailed review**. Vector selects evidence tools,
reviews their results, retrieves additional passages for open questions, and
challenges its circuit conclusions. The saved review includes an expandable
execution record. It shares the existing 12-request allowance. See [Vector
pipeline](docs/VECTOR_PIPELINE.md) for capabilities,
repair validation, and limits.

If a saved review has an AI failure, use **Retry AI review** on Overview or in
**More tools → Review log**. This reviews the saved board with the latest saved
product context. It reuses local checks, datasheets, assembly results and previews;
it does not rerun KiCad or look up the same parts again. Changed linked KiCad files
require **Update review** first. Repeated clicks do not start duplicate requests.
The result is saved as a new review, preserving the previous report.

**Save & test** sends one small structured request, with no PCB data. A successful
connection test establishes that the selected key and model answered that test;
the board review still needs to complete. Request limits do not replace the
provider's actual error, and incomplete responses never become accepted edits.

After reading the project, **Tell PreFlight about your project** invites one free-text
description of what you are building (up to 6,000 characters, saved with **Save &
analyse**). The description enters the engineering evidence and the optional Vector
review; it can be edited later. **Questions from your files** then asks only about
missing external facts identified from components and pin/net connections. For example, a charger
prompts for its attached battery, and a radio prompts for its final enclosure.
Each question shows what the files already establish. Questions are generated
locally for recognized circuit patterns, with no AI requests; they do not cover
every possible missing requirement. The AI review waits for these answers.
Choose **Not sure yet** when needed. **Product details** lets you revise answers.
Every **Continue** saves the answer immediately to `reports/.preflight.sqlite3`,
including **Not sure yet**. Closing the questionnaire or failing an AI request
does not discard submitted answers. Only unanswered questions are queued; completed
interviews open their saved-answer summary. Product-use answers survive board edits;
component-specific answers require reconfirmation when their electrical basis changes.
Reviews, chat and repair planning receive the question text alongside
these assumptions and related edge cases. Unknowns do not become passed checks.
Closing the questions preserves local results and leaves the AI review pending.
**Questions & saved answers** also lets you save AI follow-up answers without
starting a paid review. Repair answers are saved before the next investigation.
Context revisions are journaled in SQLite, and stale writes from another tab are
rejected rather than overwriting newer answers.

PreFlight uses one complete-investigation tier: there is no economy mode.
A Vector review uses up to 12 provider requests, including evidence-tool
investigations, per-finding verification, and a bounded repair loop. Repair
actions have their own per-action request cap; chat answers have a 2-request
cap. Each click that starts a new action gets a new allowance. These are
request limits, **not a euro spending cap**; token usage depends on the
project and response length. Local validation still runs, and unresolved
checks or unsafe edits are not accepted to fit any allowance.

Identical, fully validated reviews are reused in memory for up to 30 minutes
(eight entries maximum). Changed evidence, source hashes, context, model or
credentials require a fresh review. Partial or failed reviews are not reused.
Opening the app and saving settings do not test the API. **Save & test** sends
one small paid request. The AI review panel shows requests sent for new reviews.

## Start the browser app

From the project folder (whatever it is named), run:

```sh
python3 launch_app.py
```

This opens **http://127.0.0.1:8765** and keeps the app running in the background.
You can close the launch window. Double-click **Start PCB Preflight.command** in
the project folder to start it again; if it is already running, this reopens it.
Startup details are saved in `reports/.webserver.log`. For a foreground server
that stops with Ctrl+C, use `python3 main.py --web`.
Connect the `.kicad_pro`, `.kicad_sch` and `.kicad_pcb` files you edit in KiCad.
Save in KiCad, then click **Update** to read the saved files again. You can also
use **Upload ZIP** to check a KiCad archive (up to 100 MiB).

**Overview** lists active issues first, with **View issue**, **Fix this issue**, and
**Ask about it**. **Chat** beside **Review** is a general conversation: ask anything,
with no required issue or step. It uses a compact snapshot of the open project and
relevant saved evidence automatically, and also works without a project. Opening it
sends no AI request. Each message uses the existing two-request chat allowance, without
rerunning the review. Project conversations are stored in SQLite and restored across
updates and restarts. **Clear chat view** clears the visible conversation while
keeping saved history. The latest 100 exchanges are visible; the full archive remains
in the database. Up to 20 recent user messages with their preceding prompts, bounded
to 12 KB, are supplied as conversational context. Explicit product and follow-up
answers are supplied separately. Assistant suggestions are not saved as verified facts.
Chats without an open project remain session-only. Issue-specific
help remains available inside each issue. The repair
workflow prepares and checks proposed changes before approval; the button does not
mean every finding has an automatic edit. The manufacturing checklist follows. It separates
completed checks, findings to review and unverified engineering requirements.
It covers connections, power, PCB geometry, signals, mechanical fit, parts,
test access and production files. Expand a section for its evidence and next action.

New full runs refill copper on a temporary copy using KiCad Python, run DRC and
schematic parity, then export Gerber and Excellon drill drafts from that same copy.
The app validates layer attributes (including renamed copper layers), includes
source and output SHA-256 hashes, and offers the checked PCB copy for inspection.
Analysis leaves the original project unchanged. Missing KiCad Python, invalid outlines and
export failures remain visible; they never produce a fabricated success result.

### Optional file fixes

Each finding has **Fix this issue** above **Observation**, beside the design preview.
The details follow **Observation → Action → Consequence**, with supporting analysis below.
The button opens the available fix options for that finding.
For supported edits, save and close that board’s PCB Editor window,
tick the confirmation, then click **Apply fix**. The project manager and schematic can stay open. The app
prepares a copy, validates the edit, keeps a backup, and applies it automatically
if the checks pass. It currently supports copying one mismatched PCB Value field
from its matching schematic symbol, and short ground connections (up to 1.5 mm)
between an identified pad and a straight track on the same copper layer/net.
For other findings, **Fix this issue** starts an investigation in that same window when Meta is connected.
Progress, the repair assessment, retries and follow-up questions stay there. Closing and reopening
the window during the same page session keeps the investigation; it does not send it again.
Investigation requests retry transient server/connection failures up to three attempts, with
a 45-second socket timeout per attempt and a shared 120-second retry budget. Authentication,
quota, certificate and permanent DNS failures are reported without automatic retries.
The investigation asks Meta Muse Spark to assess whether a change
is needed using the selected path, connected parts, stored design context and available
datasheet excerpts. KiCad can stay open during this assessment. Chat proposes changes;
it cannot write files. Other edits remain manual.

Copper proposals must remove the exact unconnected-items finding after a KiCad
refill and DRC comparison without introducing new non-excluded DRC findings.
These checks use the saved rules and do not validate return-current behavior or EMI.
Keep the PCB Editor closed while the fix runs. The app checks source hashes again, keeps an original
backup, and replaces only the selected PCB file. Reopen the PCB in KiCad and
update the review using the button in the completion message. **Undo this fix** restores the original only if no subsequent
PCB edits would be overwritten. Previews and backups are in `reports/.file-fixes/`.
Edits require a connected local folder; uploaded files and ZIPs are copies.

The checklist also exposes ignored rules and saved exceptions. Symbol pin names
can identify I²C lines even when nets have arbitrary names; fitted pull-ups are
traced to recognized supplies and parallel resistance is calculated. Supply
voltages, bus timing, addresses and alternate pin functions still require evidence.
See [the manufacturing check coverage](docs/PREMANUFACTURING.md) for scope.

The top menu has three review sections:

- **Circuit**: supported circuit concerns from pin requirements, datasheet passages,
  functional paths and operating context. Raw ERC does not become a connection mistake
  on its own. Pin analysis can raise a wiring correction or a specific missing-mode question.
- **PCB layout**: spacing, holes, mask, footprints and assembly checks. These use
  saved KiCad rules; they are not a complete datasheet placement or manufacturing review.
- **Signals**: shorts/unconnected items, circuit paths, radio/sensor routing
  observations, saved stackup, and limited DC trace estimates.

**KiCad & library notes** retains raw ERC, naming, library and unavailable-check notices.
Each section shows a marked preview beside the selected finding, its possible impact
and the next step. Additional steps, analysis and source evidence expand on demand.
The question box stays open for the selected finding; **Ask a question** at the top
jumps to it. You can ask about a footprint note as well as an active issue.
Default-clearance warnings between known pads in the same footprint, and shared mask
openings inside that footprint, are kept in **KiCad & library notes**. They do not
generate pad-moving instructions. This classification does not approve a land pattern
or manufacturing process: shorts, zero gaps, unknown pad ownership, routed tracks and
explicit clearance rules are not suppressed. Old saved AI steps are also corrected
when viewed, without marking these findings fixed or rewriting historical reports.
**Project → Edit intended use & operating details** provides interfaces, power, antenna
and manufacturing fields used together by the AI. Connected files and check settings
also live under **Project**. Empty sections do not certify the design. **More** retains
datasheets, original findings, AI status and the net browser; **BOM & parts** holds exports.

The DC model uses the saved copper thickness and TI's nominal 25 °C resistivity formula.
Only a connected, unbranched, single-layer chain of straight tracks is estimated. Nets
with vias, pours, arcs, branches, loops or intersections remain uncalculated. The estimate
is between drawn track endpoints, not component pins: pads, contacts, parts and heating
are excluded. It is not RF impedance. No return-plane, noise, crosstalk, antenna or thermal
simulation has run, and no acceptable voltage-drop/current limit is inferred.

**Projects** has searchable,
paginated history, including reports previously generated from Terminal. Give each
board a project name and reuse it for later revisions. The last opened run is restored
when the app restarts. Checks use temporary copies of the KiCad files.

### Follow the circuit

**Circuit / Signals → Explore circuit paths** traces the saved schematic connections through
supported charger and regulator stages, fitted series parts, and receiving pins.
Choose a circuit path and, where available, its source route. Click a pin to mark its
saved schematic position, or show the complete path and switch to **PCB** to highlight
its drawn copper. Signal paths also show the shared bus endpoints and side branches.

The smartwatch example includes J1 → U8 → U4, J4 → U4, U4 → U12,
the SCL/SDA sensor buses, and U13 → L1 with the antenna capacitor branches.
The AI review and step helper receive these traced paths alongside pins and datasheet
passages. Unknown internal IC connections and switch states are left unresolved.
A path through a regulator describes its intended function; it does not mean its input
and output share copper. Shunt capacitors are kept separate from DC power paths.
The highlights show saved routing, without calculating noise or impedance.

### Describe the device, then revise the change plan

**Project → What the app understands** describes the device inferred from the selected components.
Without AI this is labelled as an initial interpretation from component names. With
Meta Muse Spark it is reconsidered against the circuit evidence and your stated intent.

1. Correct the description or add constraints: power source, operating modes, external
   circuitry, interfaces, intended measurements and manufacturing requirements.
2. Select **Save & review**. This saves context and creates a new report using
   the same circuit evidence, without rerunning KiCad. A configured Meta key enables
   the AI review; without one, local steps remain available and are labelled accordingly.
3. Each item has ordered change/investigation steps. Use **Is this intentional?** to
   explain an exception, then **Use this context & rethink**. The AI must address the
   explanation and can revise the steps or retain the issue with a reason.
4. Context persists for the named project and root schematic. Notes on findings and
   selected part identities must be reconfirmed when their underlying evidence changes.
   Old reports retain the context snapshot used for their review.

Intent is not proof of a fix. Original findings are preserved. Failed AI requests keep
the saved context and local steps, and report that reconsideration did not complete.

### LCSC datasheets

Install the optional PDF reader in this project:

```sh
python3 -m pip install --target .deps -r requirements.txt
```

Each browser run automatically searches LCSC for fitted components and reads the PDF
linked to each chosen product. Explicit codes and exact MPN matches are assigned directly.
Family names and passive values/packages receive **automatic candidate** selections,
already included in the draft BOM. Candidate choices remain provisional until reviewed.
Generic labels such as `LED`, or crystals without a frequency, remain unresolved.

The matcher compares passive values and imperial package sizes, filters recognized IC
package and pin-count conflicts, and excludes development boards from chip searches.
It does not establish pinout, rating, dielectric, substitute or assembler compatibility.
Open **Parts & datasheets** to inspect the selection and its PDF, confirm a group of
identical parts or replace it with an exact LCSC code. This regenerates the BOM and plan.

Downloads use an HTTPS host allowlist, size/time limits and a seven-day local PDF cache
in `reports/.datasheets`; search results are cached for one day. Lookups share repeated
value/package queries and prioritize ICs, with an 80-query / four-minute budget. A run
can finish an in-progress bounded request after that budget. Deferred items remain visible;
rerunning uses the cached results. Stock is a search-time snapshot, not an order reservation.

PDF text is extracted in a bounded subprocess (up to 80 pages / 500,000 characters).
The initial warning review receives three passages per part, or six for flagged pins.
An independent functional review then examines active parts even when ERC reports no issue.
It retrieves up to twelve passages per part across pin functions, operating limits,
interfaces, startup, supporting components and signal requirements. Each small request
includes neighboring pins, component values and traced circuit paths. A second request
audits both proposed corrections and claims that a connection is consistent.

Open **Circuit → Circuit analysis** to see these comparisons and their missing facts.
Supported concerns appear in the normal issue list with exact pin markers. Unknown
requirements remain open questions rather than suggested wire edits. The review uses
the configured model with high reasoning effort for these focused requests. It makes
more API requests than the warning review and can use more of your provider quota.
Limits: eighteen active parts per run, eighty seconds per request, and six minutes for
the functional stage. Unreviewed parts and failed requests remain listed explicitly;
completed comparisons are preserved. A short rate-limit delay is retried only when
the provider supplies a reset within sixty seconds, with at most two pauses per run.
The initial warning stage has its own time limit.
Automatic candidates are labelled provisional and may only support investigation steps.
Page IDs and short quotations are validated against the supplied text. Scans, inaccessible
files and incomplete text are reported; a readable PDF is not required to keep a retrieved
part identity in the BOM. Quotation validation does not establish that the conclusion
follows from the quote, or that omitted diagrams/tables contain no further requirements.

Lookup sends part names/codes to LCSC. Meta Muse Spark receives structured circuit evidence,
your context and selected PDF excerpts. API keys stay in server memory during use. With **Remember key on this Mac**, the app also stores the key in macOS Keychain and restores it after restart. Downloads
and the change plan work independently of KiCad analysis; board files are not edited.

### Automatic BOM and CPL downloads

**BOM & placement files** appears below the device description after each run:

- **Download BOM + CPL**: ZIP containing the files below and a checksum manifest.
- **bom-draft.csv**: grouped `Comment,Designator,Footprint,LCSC Part #` rows for fitted
  PCB components. Equivalent passive values group together. Unresolved codes stay blank.
- **cpl-draft.csv**: `Designator,Mid X,Mid Y,Layer,Rotation`, generated by KiCad CLI
  from the exact PCB bytes used for the report. Coordinates are in mm, using KiCad's
  drill/place origin, with both sides and native rotations. No assembler-specific rotation
  correction is guessed. Check the assembler's preview before ordering.
- **sourcing-review.csv**: every part's selected MPN/code, match basis, review notes,
  listed specifications, stock snapshot, source links and placement status.

DNP, excluded-from-BOM and mechanical components are omitted. A footprint excluded
from position files stays in the BOM and is labelled accordingly in the worksheet.
Duplicate or case-colliding references block BOM/CPL output. Without KiCad, the BOM
still downloads and the missing CPL is explained. Saved placement snapshots support
context/part updates after an uploaded ZIP has been removed or the app restarted.
Changed source bytes require a new check before a fresh placement snapshot can be made.

The command-line checker does the same sourcing and exports. Use `--no-sourcing` to
skip online lookups. In the browser, uncheck automatic LCSC lookup for an offline run.
CPL files contain placements; the BOM contains their LCSC part numbers.

### Other review tools

- **Before you order** shows available evidence, missing checks and next actions.
- **Check evidence** lets you record Open, Reviewed or Accepted risk with a reason.
  Notes persist across restarts, include a change history, and apply only to that run.
  These decisions preserve the original findings. A decision is not a verified fix.
- **BOM & placement** compares BOM/placement CSVs with the PCB and shows declared
  part numbers, missing identities and conflicting MPNs. It does not verify live stock,
  pricing, substitutes, electrical ratings or the supplier package pinout. Generated
  LCSC assignments are shown alongside the imported evidence.
- **Compare runs** compares finding groups, component values/footprints/positions and
  schematic net membership against a baseline from the same project (latest 100 runs).
  A drop in check coverage is called out before interpreting absent findings.
- **Export review package** downloads a ZIP with the report, decision journal, parts
  and findings review worksheets, generated assembly drafts, and SHA-256 checksums.

### BOM and placement checks

Include UTF-8 CSV files in the project folder or ZIP. Supported separators are comma,
semicolon and tab. Recognized headers include KiCad's `Ref,PosX,PosY,Rot,Side` and
`Designator,Mid X,Mid Y,Layer,Rotation`, plus common BOM field aliases.
References like `C1, C2, C3-C5` are expanded; each placement row must name one part.
Use **Assembly files & coordinate units** when multiple candidate CSVs exist.

Checks cover duplicate/invalid references, quantities, missing/invalid coordinates,
rotation and layer, reference mismatches, DNP conflicts, side mismatches and clear
passive-package mismatches. Descriptive BOM value differences remain review items.
Mechanical/DNP exclusions and through-hole or placement-excluded footprints receive
different treatment from expected automated placements.

Bare coordinates have **unknown units** until you confirm mm or inches. Unit-bearing
headers/cells are recognized; contradictory units are rejected. Position origin,
mirroring, rotation conventions, physical centroid accuracy and assembly variants
remain unverified. CSV validation does not imply placement geometry is correct.

Format references: [JLCPCB BOM](https://jlcpcb.com/help/article/bill-of-materials-for-pcb-assembly),
[JLCPCB placement](https://jlcpcb.com/help/article/pick-place-file-for-pcb-assembly),
and [KiCad 9 position export](https://docs.kicad.org/9.0/en/cli/cli.html#pcb-position-file-export).

```sh
python3 main.py /path/to/project --bom exports/bom.csv --cpl exports/positions.csv --cpl-units mm
```

### Storage and operating limits

`reports/.preflight.sqlite3` stores the indexed project history, review decisions and
preferences. Each completed report remains an independent folder. Back up the entire
`reports/` directory while the app is stopped, including the database. Keep notes with
the reports they refer to. New reports are published only after every file is written;
failed saves do not leave a partial report in the library. Inputs receive SHA-256
fingerprints, and a source edit during a run makes that run fail instead of mixing revisions.

Listings use indexed metadata and pagination. Large component, raw-finding and parts
lists render 100 rows at a time. Refresh the library to discover CLI reports created
while the server is open. KiCad jobs remain sequential because this local engine uses
application-level resources. This release is one trusted local workspace, with no
customer accounts or hosted multi-user isolation. See [PRODUCT.md](PRODUCT.md) for
the remaining product work.

In **Vector settings**, paste a Meta API key and choose **Save & test**.
PreFlight uses Meta Muse Spark 1.3 Contributor exclusively — there is no
provider selector and no automatic fallback to another model. The existing
evidence validation and staged KiCad checks still apply.

**Remember the key on this Mac** stores it in macOS Keychain. The key never
enters reports or browser storage. The server also accepts `LLAMA_API_KEY`
as an environment variable, or from an untracked `.env` / `.env.local` file
(see `.env.example`). **Include AI review** sends structured evidence to the
Meta API; leave it unchecked for local-only checks. Selecting Save & test
sends one small request; selecting a model alone does not establish access.

The app listens only on this computer. Uploaded ZIPs are held in a temporary folder
until the server stops; completed reports remain in `reports/`. One check runs at a
time. Stop with Ctrl+C (an active check finishes before cleanup).

Useful launch options:

```sh
python3 main.py --web --port 8766       # If the default port is occupied
python3 main.py --web --no-browser     # Start without opening a browser
python3 main.py --web --no-ai          # Start with AI review unchecked
python3 main.py --web /path/to/project # Prefill another project
```

## Start the command-line checker

Open Terminal and run from the project folder:

```sh
python3 main.py
```

When it asks for the unzipped folder name, enter a folder inside the checker
directory, or a full path. If a project was previously connected, the prompt
offers it as the default; press Enter to use it. To change the default
without editing the code, set `PCB_CHECKER_PROJECT` to another folder or ZIP.

To skip the prompt, pass a folder or ZIP directly:

```sh
python3 main.py /path/to/your-board
python3 main.py /path/to/project.zip
python3 main.py /path/to/project.zip --json
```

`--json` includes every parsed component, both source records, original
properties, file paths, and source line numbers. The prompt goes to stderr,
so stdout remains valid JSON. Imports and checks do not modify the KiCad files.

If the checkout folder itself is named `pcb_checker`, `python3 -m pcb_checker` also works from its parent directory.

## Reports and component inspection

The default report leads with **Check evidence**: concrete discrepancies,
fabrication constraints, assembly concerns, and schematic/library setup. Cosmetic
findings are available under the Show menu. A via flagged by three rules appears
as one physical item in a grouped explanation. Original severities and every
underlying finding remain accessible under **Raw findings** and in JSON.

Each group explains the evidence, what could go wrong, what remains uncertain,
and a next step. These explanations are local rules; they are not labelled as AI.

The **Circuit context** tab lists supply-to-ground capacitor connections and
external pull-up resistors on SDA/SCL candidates. It does not assume every IC
needs a generic capacitor or infer voltage ratings from rail names. Component
details also show existing MPN, manufacturer and LCSC fields when present.

Two narrow manufacturer rules run when connectivity is available:

- MCP73831 DFN: compare exposed pad 9 with VSS pad 6; report nominal charge
  current when one supported programming resistor can be identified.
- MAX30102: distinguish its six documented NC pads from missing electrical
  connections; flag assigned nets or missing package pads for review.

Rules match declared part identities, not `U8`/`U11` reference names. Sources and
page/section information are included. The browser's separate LCSC lookup can retrieve
other matched datasheets; the fixed local rules do not expand automatically from PDF text.

A normal run automatically detects KiCad on PATH or in its standard macOS
installation, performs the checks, and prints the saved report path. Open
`report.html` in your browser. The report has severity and text filters,
expandable findings with evidence, component pin tables, and searchable net members.

Every run gets a new folder under `reports/` in the project folder. Earlier reports are
preserved. Each contains `report.html`, `report.json`, and the successful native
`kicad-erc.json` / `kicad-drc.json` reports. Local checks upload nothing. Optional
AI behavior is described below.

```sh
# Inspect a component's schematic and PCB connections in the terminal:
python3 main.py /path/to/project --component U13

# Ask deterministic connectivity questions without AI:
python3 main.py /path/to/project --pin U8.8 --no-ai
python3 main.py /path/to/project --net SDA --no-ai

# Show the old detailed terminal output:
python3 main.py /path/to/project --raw

# Select a KiCad executable or a report destination:
python3 main.py /path/to/project --kicad-cli /path/to/kicad-cli --output-dir /path/to/reports

# Use only the built-in component checker:
python3 main.py /path/to/project --skip-kicad

# Print machine-readable results without saving files:
python3 main.py /path/to/project --json --no-save
```

If KiCad is unavailable or a command fails, the report explicitly says which
checks could not run. A missing result is never treated as a passed check.

## AI review and questions

### Meta Muse Spark setup (paid)

PreFlight's AI review uses Meta Muse Spark 1.3 Contributor and nothing else.
There is no free tier: every AI review, question answer, and repair assessment
is billed to your Meta key at Contributor rates (currently $0.10 in / $0.002
cached / $0.20 out per 1M tokens — check Meta's current pricing). A full board
review typically costs a few cents; the top bar shows live spend and tokens.

1. Get a Meta API key with access to Muse Spark 1.3 Contributor.
2. Open **Vector settings** in the app, paste the key, and choose **Save & test**
   (one small structured request, no PCB data). Or, for Terminal runs, set
   `LLAMA_API_KEY` in your environment — or copy `.env.example` to an
   untracked `.env` file — and run `python3 main.py --test-ai`.
3. Tick **Include AI review** when starting a check. `--no-ai` keeps a run
   local. The local checks always finish even if AI is unavailable.

Avoid typing real keys into shell history or committing them. On macOS zsh,
this prompts without echoing:

```sh
read -rs 'LLAMA_API_KEY?Meta API key: '
export LLAMA_API_KEY
python3 main.py --ai-provider meta
```

Ask a focused question:

```sh
python3 main.py /path/to/project --ask "What could actually prevent this board from working?"
python3 main.py /path/to/project --ask "Explain the charger connections and the remaining uncertainties."
```

Meta uses structured JSON output with low thinking effort and bounded output
limits. Each API attempt can take up to three minutes, with at most three
attempts for transient server errors; quota and auth errors are reported
immediately with no silent provider switch.

### Diagnose an API failure

To test the key, model and structured response format without loading a project
or running KiCad:

```sh
python3 main.py --test-ai
```

This uses the same request builder and response validation as the PCB review,
with empty circuit data and a short test question. It makes one request, waits
up to 60 seconds, and does not retry or change models. It reuses the exported
key or prompts privately. The command saves no files. A successful small test
does not establish that a full board review will succeed.

Use `--ai-model MODEL` to test a specific model and `--json` for structured
diagnostics. On HTTP failure, Meta's error code and message are shown when
available. API keys and terminal controls are removed; only bounded JSON error
fields are read. The full review also saves these diagnostics in `ai-review.json`.
HTTP 503 alone does not establish that the key was accepted or identify why the
service failed. Repeated errors can represent one original request plus its
retry attempts.

The request contains normalized component identities, pin/net assignments,
grouped findings, circuit context and cited part rules. The full archive, arbitrary
custom properties and original absolute project directory are not sent. Requests
use `store: false`. Meta's API terms still apply. This setup is for your own
prototype testing; check Meta's terms before offering the app to other users.

PreFlight supports only Meta Muse Spark 1.3 Contributor. There is no
Gemini, OpenAI, or other-model option in the review path.

### Evidence and reliability

Each saved report includes:

- `ai-input.json`: the inspectable evidence packet.
- `ai-prompt.txt`: the review instructions and facts, usable manually in another AI tool.
- `ai-review.json`: model, completion status, evidence fingerprint, token usage and accepted response.

No API keys are included. Oversized evidence is rejected, rather than silently
omitted. Each AI observation must cite real evidence IDs; invalid citations,
malformed output, refusals and partial responses are rejected. AI interpretations
do not change deterministic findings. The browser review includes matched LCSC PDF
excerpts and saved intent. The CLI's original review uses the local circuit evidence.
Neither mode simulates the circuit, verifies live stock or automatically edits the design.

The HTML report shows **AI review has not run** when disabled, unconfigured,
cancelled or failed. It never substitutes a canned explanation for a model result.
Explicitly requested AI failures return exit code 3 after saving local results;
import/CLI errors use 2. Rule violations themselves do not change the exit code.

## Feature status: verified, partial, experimental

**Verified working** (test suites + real-board runs green):
- KiCad project parsing (schematic hierarchy, PCB footprints, nets, pads),
  schematic↔PCB comparison, deterministic electrical and manufacturing checks.
- Native KiCad ERC/DRC execution with timeouts and honest unavailable states.
- Meta Muse Spark investigation: catalog component discovery, datasheet
  excerpt retrieval with `D:` citations, per-finding verification,
  fix assessment, chat, and usage tracking.
- Exact-part prescriptions with quoted datasheet evidence (for example the
  USB-protection recommendation), conditional on unresolved user questions.
- Deterministic Fix Design for supported edits (value changes, verified
  ground connections): preparation stages disposable copies and re-verifies
  them; applying writes your linked project files with an automatic backup
  and undo.
- BOM/CPL drafts, fabrication exports, assembly manifests.
- Bundled demo boards, product-context wizard with bulk entry, report
  history, project persistence.

**Partially working / conditional:**
- Repair planning for non-deterministic edits needs a Meta key and may stay
  conditional when evidence is missing.
- The VBUS fuse recommendation stays conditional without supporting catalog
  evidence; the USB TVS recommendation is evidence-backed.
- Antenna/RF findings identify geometry and layout concerns but cannot prove
  RF performance without simulation or measurement.

**Experimental:**
- Firmware generation: nRF52 bare-metal C and RP2040 Pico SDK C (GPIO
  bring-up, I2C HAL, UF2 output) with 89 passing tests. No QMK, no
  auto-flashing, no hardware-in-the-loop verification. Requires external
  toolchains (arm-none-eabi-gcc, cmake, Pico SDK / nRF tools).
- Breadboard-to-PCB LiveScan: camera scanning and 3D reconstruction exist
  but are not release-verified in this build.

AI findings are engineering assistance with cited evidence, never proof of
manufacturing correctness. Nothing is declared safe on missing evidence.

## What this version checks

- References present in the schematic and PCB, and components missing from either.
- Duplicate PCB references and repeated schematic units.
- Schematic versus PCB values and footprint names.
- Missing values, missing schematic footprint assignments, and unsupported or
  unannotated references such as `R?`.
- Schematic pin numbers versus numbered PCB pads, preserving legitimate repeated
  pad numbers and ignoring unnumbered mechanical holes.
- Per-pin net assignments, nets split across different PCB net IDs/names, and
  distinct schematic nets merged into one PCB net.
- Explicit no-connect pins connected to other PCB pads.
- KiCad ERC: unconnected pins, power-pin drive rules, pin-type conflicts, and
  other enabled schematic rules.
- KiCad DRC: unrouted connections, clearances, via/drill limits, solder mask,
  silkscreen, and other enabled board rules.

The schematic graph comes from KiCad's XML netlist export, so the prototype
uses KiCad's resolved labels and pin functions. The PCB parser independently
reads pad/net assignments. Generated names for isolated pins and KiCad's
`{slash}` escaping are handled in comparisons. Extra numbered PCB pads are
review warnings: exposed or unused pads may legitimately be absent from symbols.

PCB pad/net agreement checks intended connectivity. Physical copper routing is
assessed by KiCad DRC. These are different checks, and the report lists both.
PCB placement coordinates, rotation, and layer are also captured for inspection.

Power and virtual symbols with `#` references are excluded from physical
component counts. Distinct units of the same symbol share one component when
their fields agree. Symbols marked `on_board no` are not required on the PCB;
footprints marked `board_only` are not required in the schematic. DNP and
BOM-exclusion flags are retained; neither implies that a footprint is optional.

Common R/C/L value spellings are normalized for comparison: `100nF` equals
`0.1uF`, `4k7` equals `4700`, and `µ`/`μ` are accepted as micro. Milli (`m`)
and mega (`M`) remain different. Original strings are retained in the report.
Other value strings and footprint identifiers are compared after trimming
surrounding whitespace. Different footprint names are a warning to review;
this does not compare their pad geometry or pinout.

## File selection and limits

The importer inventories nested folders. Autosave files and files under
`backup`, `backups`, or `*-backups` directories are excluded from automatic
schematic/PCB selection. Nested ZIPs remain inventory entries.

If several schematics or PCBs are present, choose their paths within the input
folder or ZIP. These options also confirm a pair with different names or folders:

```sh
python3 main.py /path/to/project.zip --pcb board/main.kicad_pcb --schematic schematic/main.kicad_sch
```

Automatic comparison requires one schematic and one PCB with the same stem
in the same folder. The matching `.kicad_pro` and `.kicad_dru`, when present,
are copied alongside the inputs so KiCad can use the saved project rules.
A missing project settings file is reported because KiCad may use defaults.
`.kicad_prl` and fabrication toolkit settings are inventoried. BOM/CPL CSVs
are still only candidates based on their headers.

This stage supports modern KiCad files with a single schematic sheet. Child
sheets or unresolved project instances are explicitly reported; cross-file
comparison is skipped in those cases. Failed or missing sources never become
blank component lists for missing-component checks. Component parsing reads
the placed symbols and footprints, including older `fp_text` PCB fields.

ZIP limits are 100 MiB compressed, 250 MiB total uncompressed, 50 MiB per file,
and 5,000 entries. Folder imports use the same entry and uncompressed size
limits. Links, unsafe archive paths, encryption, duplicate archive paths, and
damaged ZIP entries are rejected. The syntax parser also limits nesting and
token count. For native checks, only required and validated KiCad files are
materialized in a temporary working directory, preserving relative paths.
External or missing child sheets are rejected. Commands run sequentially with
timeouts to avoid KiCad instance-lock conflicts. Temporary copies are removed
afterward; the input project is unchanged.

KiCad findings use the project's saved rules and, when preparation succeeds,
copper zones refilled on a temporary copy. Older runs and failed preparation use
saved fills and are explicitly marked incomplete. Checks also use
the installed KiCad libraries. The checker does not rewrite the original board.
Library mismatch findings depend on the installed library versions. Board limits
can be compared with a selected manufacturer profile. Datasheet reading, sourcing
candidates and BOM/CPL checks each report their own coverage. Passing these checks does
not establish that a board is safe, compliant, or production-ready.

## Architecture

- `main.py` / `__main__.py`: startup prompt and command-line options.
- `archive.py` / `discovery.py`: bounded reads and file inventory.
- `parsers/sexpr.py`: nested KiCad syntax with source line numbers.
- `parsers/kicad_pcb.py` / `parsers/kicad_schematic.py`: source component records.
- `parsers/netlist.py`: resolved schematic pins, nets, and no-connect flags.
- `native.py`: KiCad discovery, isolated staging, bounded commands, and native findings.
- `models.py`: project and component models; schematic and PCB records remain
  separate within each reference so conflicts and duplicates are preserved.
- `analysis.py`: source selection, parsing, merging, and check orchestration.
- `checks/components.py`: deterministic validation and comparisons.
- `checks/connectivity.py`: pin/pad and net-group comparisons.
- `checks/circuit.py`: circuit context, pin/net queries and the sourced part rules.
- `review.py`: practical interpretation, grouping and links to original findings.
- `ai/reviewer.py`: bounded evidence packets, Meta transport and response validation.
- `report.py` / `templates/`: terminal, JSON, and standalone HTML reports.

Python callers can opt in to native checks with
`analyze_project(path, native=True)`. The command-line interface enables them
by default. Native findings retain their engine source, original severity,
descriptions, UUIDs, and reported positions. Raw engine JSON is retained alongside
the normalized model for traceability.

The parsers follow KiCad's [schematic format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/),
[PCB format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/), and
[common syntax and footprint attributes](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/).
Native commands follow the [KiCad CLI documentation](https://docs.kicad.org/9.0/en/cli/cli.html).

Run the tests from this folder:

```sh
python3 -m unittest discover -s tests -v
```

Tests use synthetic fixtures; private KiCad designs are not bundled with the code.
Optional integration tests exercise the current smartwatch fixture with the real
KiCad engine. They compare ZIP and folder results, inject a wrong pad/net assignment
into a temporary copy, and verify that all original input files remain unchanged:

```sh
PCB_CHECKER_TEST_PROJECT=/path/to/your-test-board python3 -m unittest discover -s tests -v
```

AI transport and failure handling have automated mocked tests; a live model test requires an
API key configured by the user.

## Current limitations

- RF/antenna findings describe geometry only; performance needs simulation or measurement.
- Some fixes cannot be safely automated and are reported as unsupported.
- KiCad 9+ is required for native ERC/DRC, netlist export, and fabrication files;
  without it those checks report NOT PERFORMED.
- Meta AI features (review, chat, AI-planned repairs) need your own Meta API key
  and internet access; everything else works offline.
- Firmware generation is experimental (nRF52 bare-metal C and RP2040 Pico SDK C
  only; no QMK, no auto-flashing, external toolchains required).

## License

PolyForm Noncommercial License 1.0.0 — see [LICENSE](LICENSE). Required notice:
Copyright (c) 2026 Neil Dalal.

### Schematic requirements and intended use

With KiCad checks enabled, the checker reads actual schematic pin/net connections.
The **Schematic wiring** section lists the checks that ran, and **See wiring checks**
opens their plain-language steps in the fix plan.

Local manufacturer rules currently cover selected MCP73831 DFN and MAX30102 supply,
capacitor, charge-setting resistor, and SDA pull-up connections. They use the declared
part/package and fitted parts. Capacitor values are nominal; voltage derating, position,
external circuitry, supply voltages and firmware behavior still need review.

Enable **Meta review** and automatic datasheet retrieval for a broader schematic review.
Describe the battery, supply voltages, interfaces and operating modes in **What this device
is meant to do**. Meta Muse Spark can now add new schematic concerns, beyond existing KiCad warnings.
Each new concern must include actual exported pin/net observations, a matching datasheet
quotation and steps. Invalid pin connections or quotations cause the response to be rejected.
AI concerns remain suggestions for review. The excerpts do not cover every page or figure,
and a matching quotation alone does not prove an interpretation is correct.

The revision checklist does not mark an AI concern fixed just because a later AI response
omits it. Local schematic concerns require their specific check to run successfully again.

### Review workspace

Start with **Circuit**, **PCB layout** or **Signals**. Pick a
finding on the left, then read **Finding**, **If unchanged** and **Next step**. The viewer switches between the
schematic and PCB; use **Focus issue** to return to the selected component.
The schematic preview currently covers the root sheet only.

Evidence includes original file findings and actual PDF images when available.
An exact, uniquely matched quotation is highlighted in a crop of the original
page. Otherwise the app shows the cited page or explains that an image is
unavailable. A matching passage establishes what the datasheet says; it does not
by itself confirm the selected part or prove a hardware fault.
PDF rendering requires the dependencies in `requirements.txt`.

Use **BOM & parts** for assembly exports, **Project** for device context and revision
history, and **Update** after saving changes in KiCad. Detailed checks remain under
**More**. Each finding has an expandable context field and **Explain** step help.

### Opening the app and step help

The app restores the last opened board. With a local source folder available, it
asks for a Meta key and offers **Connect & run AI review**. The key stays only
in server memory, with optional macOS Keychain storage for automatic reconnection. Closing the prompt leaves saved results available. An existing
key starts the review once per browser tab session; refreshing does not repeatedly
run the same automatic review.

**Explain this step** immediately retrieves a walkthrough. With Meta connected,
you can ask follow-up questions. Saved local guidance is refreshed consistently
for both the displayed steps and the help endpoint. Declared part-family matches
appear in Evidence instead of being assigned as manual lookup steps.

### Save in KiCad, then Update

For a local folder, **Update** immediately rereads the saved project and compares
it with the currently open report. No ZIP or file selection is needed each time.
You can also choose **Project input → Choose KiCad files** and select the
`.kicad_pro`, `.kicad_sch`, and `.kicad_pcb` together (optional `.kicad_dru` and
additional schematic sheets are accepted). Maximum combined upload size: 30 MB.
Where the browser supports file handles, Update rereads those files while the tab
remains open. Otherwise it asks you to select fresh files again; it never silently
rechecks an old uploaded snapshot. For custom libraries, use the complete folder
or ZIP.

### Connection checks and routing advice

Supported charger/regulator paths are checked for joined input/output, input/ground
and output/ground nets. Those saved connection contradictions appear in the normal
issue list. Unknown IC internals and separate protected battery returns are not joined
by inference. ECG via-count differences remain measurements without requiring a routing
change. AI advice that delegates a generic routing/noise inspection is rejected; a
correction must state a supported edit or the exact missing operating/layout context.
Circuit path and source details are optional disclosures in the interface.

### Manufacturer check

Open a project → **Manufacturer** → choose JLCPCB or PCBWay, copper weight and surface finish → **Check manufacturer**. The choice is saved for this project. It runs locally without any AI key and carries forward when you click **Update** after saving in KiCad.

The dated profiles cover rigid FR4 with through vias. The table compares measured layers, thickness, track widths, via dimensions and the pad gaps already reported by KiCad. Results distinguish published limits, paid ordering options, unsupported dimensions and missing information. Download the order checklist or expand the suggested KiCad settings. Original KiCad files are never edited by this check.

Profiles compare measured values against published [JLCPCB capabilities](https://jlcpcb.com/capabilities/pcb-capabilities) and [PCBWay capabilities](https://www.pcbway.com/capabilities.html). This is a partial fabrication comparison, not a complete manufacturer DRC or production acceptance. Outline/edge checks, component holes, mask webs, assembly and controlled impedance are not covered. Old runs are measured only when their source hash matches; otherwise the app asks for Update.
# Editing from an issue

Open **Fix this issue** → **Investigate and design a fix**. An optional request can add
constraints. The app reads the affected parts' available documents, circuit paths,
stackup and placements, then checks its proposed approach against those sources.
Questions are for missing operating context. Answer them (or enter “unknown”), review
the updated approach, then choose **Approve approach and prepare edits**. This approval
only starts preparation; saving the exact file changes needs a separate approval.

The planner can move or rotate existing footprints, draw and remove tracks on existing
nets, and replace an installed library footprint with the same unique pad numbers.
Replacement also updates the matching schematic Footprint field. It does not remap pins.
For a Nordic nRF52840 AQFN73 design, the investigation retrieves Nordic's official
QIAA reference-layout package and compares the saved RF matching topology and values
with its configuration 1 schematic. That reference ends at an RF output; it does not
specify a suitable antenna geometry for every board. The app must not substitute an
unrelated vendor's antenna pattern or treat an unrecognized endpoint as a proven fault.
It can still prepare a sourced antenna edit when an exact, package-appropriate antenna
footprint and its layout requirements are supplied and approved. Placement, ground
clearance, enclosure effects and RF tuning still need verification. Existing matching
component values are preserved. A keepout does not remove existing tracks, pads or vias;
necessary moves must be listed separately.

Every batch is prepared on copies with KiCad, copper refill and before/after DRC.
New DRC errors or unrouted connections block saving. New warnings are listed for review.
When AI plans an edit, KiCad rejection or new errors are fed back to a fresh planning
attempt up to twice. If they remain, preparation stops and no project file is changed.
The preview includes both board sides, every proposed operation and an exact file diff.
Save and close both KiCad editors, then approve the listed changes to apply them.
After saving, the app lists what changed. Reopen the same files and update the review.
Backups and Undo cover all changed files; edits made after the preview block application.

New schematic components/wires, outline changes, pin remapping, via edits and arbitrary
antenna synthesis are not implemented. The staged workflow supports other issue types
using the same supported operations. AI source review can still miss errors. DRC does not verify
package suitability, electrical intent or RF performance. Multi-file writes roll back on
ordinary write failures; keep the saved backups for recovery after a process or power failure.

### Chat context and token use

Issue chat and general chat compact oversized requests locally. Current questions,
recent messages, product declarations, source revisions and relevant evidence take
priority over repeated answers and board-wide records. Complete quotes and pin
records are kept or explicitly omitted; they are not truncated into new claims.
The AI receives a coverage notice when records are omitted. Full saved conversations
and project context remain in the project database. This selection makes no extra
AI call and targets a 48 KB JSON evidence payload, not an exact token or cost limit.

### Two chat scopes

- **Issue chat** opens beside a selected finding. It uses that issue’s steps,
  physical pins, datasheet evidence, connections and separate saved conversation.
  Steps within the same issue share a conversation. Explicit product answers
  are still shared across the project. Opening the chat does not call the API.
- **Project chat** is in the top navigation. Each question searches saved
  components, nets, findings and supplied datasheet excerpts using reference IDs
  and keywords. This is local project retrieval, not internet browsing, and uses
  no separate model request. Its conversation is independent of issue chats.


### Vector with Meta’s own API

Open **Vector settings**, select **Meta · Spark 1.3 Contributor**, and paste
your Meta API key. The Meta provider uses Muse Spark 1.3 Contributor
exclusively at Contributor billing ($0.10 in / $0.002 cached / $0.20 out per
1M tokens); it never switches to another model. **Save & test** sends one
small structured request without PCB files. **Save settings** sends no request.

The adapter uses `https://api.meta.ai/v1/chat/completions`. It supports
review, project and issue chat, and existing repair planners. All existing
local evidence and edit checks still apply. Meta mode does not switch to
another provider. Web search is not currently available in Vector chat; use
the in-app findings, datasheet excerpts, and cited evidence instead.

Remembered Meta keys are stored in macOS Keychain under the
`preflightinapp` service for your username, never in reports or browser
storage. For Terminal use, set `LLAMA_API_KEY` (or copy `.env.example` to
untracked `.env`) and run `python3 main.py --ai-provider meta --test-ai`
to check the connection.
Request limits apply to all providers; they are not a monetary spending cap.
Provider integration tests use simulated responses and do not establish live
account access or guarantee the correctness of a model’s engineering advice.

The top bar shows Meta spend and tokens. Select it for per-request totals and
a local budget with warnings; the budget is local only and is not your Meta
account balance.

### Decisions made in Vector chat

Issue chat and project chat can offer a part choice or an answer to a product question.
Accept with the choice button or a clear reply such as “ok then use that” when there is
one current proposal. Preflight saves the choice in the project database, including
its source and component identity, and rechecks affected findings within the existing
per-chat request budget. A confirmed part choice does not also select firmware behavior.

Only a validated “no change needed” conclusion moves a finding out of active issues.
Changed files, unsupported conclusions and exhausted request limits leave findings open
with a specific next action. The choice remains saved. Historical findings and conversations
remain available; chat does not bypass the existing preview/approval flow for file edits.
Chat choices use the same structured fields as the review; every accepted
choice is rechecked against the affected findings.
