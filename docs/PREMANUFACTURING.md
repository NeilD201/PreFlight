# Manufacturing review coverage

This is a general KiCad review workflow. The smartwatch is a regression fixture;
component references, battery specifications and operating loads are not assumed
to apply to other projects.

## What the app now checks automatically

| Area | Implemented check | Limit |
|---|---|---|
| Source consistency | File hashes, component and net comparison, KiCad schematic parity | Native parity requires matching project filenames |
| Copper | Refill temporary PCB, then DRC with all track errors and all severities; rerun ignored rules with temporary settings | Original severities and saved exceptions are preserved; additional findings have separate downloadable reports |
| Outline | KiCad polygon outline validation and bounding dimensions | Does not establish enclosure fit |
| Fabrication | Existing manufacturer profiles plus draft Gerbers and PTH/NPTH drills | Partial process coverage; order options still need approval |
| Export integrity | Copper/mask/paste/legend/outline attributes, file completeness, common plot origin, hashes | Manufacturer preview remains necessary |
| Circuit | Existing sourced pin checks, topology checks and optional validated AI review | Coverage depends on readable datasheets and known operating context |
| I²C | Trace external pull-ups and calculate rise time and sink current, including resistor tolerance, against entered limits | Needs supply, total line capacitance, rise-time limit and device sink specification; multi-rail pull-ups are excluded; logic levels and addresses are separate |
| Power | Per-rail current budget from entered simultaneous loads and source capacity, with reserve margin | Supply pins are counted once per component; does not verify regulator voltage, stability, charging or temperature |
| Heating | Steady-state junction-temperature estimate from ambient, dissipation and effective assembly thermal resistance | Needs assembly-specific resistance; no thermal field, transient heating or automatic derating calculation |
| Load steps | Conservative supply-drop estimate from current deficit, response time, capacitance, ESR, loop inductance and current rise time | No control-loop simulation, ringing, startup sequence or measured response |
| Protection | Positive-pulse clamp plus connection-inductance voltage estimate versus entered pin limit | Does not establish fitted protection, negative-pulse behavior, pulse survival or ESD immunity |
| Trace loading | Voltage drop and power loss for recognized single-chain traces, using entered current and resistance multiplier | Excludes vias, arcs, pours and branches; not an ampacity or temperature rating |
| Signal geometry | Nearby parallel straight tracks and centerline coverage by adjacent named ground fills | Geometric candidates only; no coupled voltage, ground connectivity or return-current distribution verification |
| Assembly | Compare fitted BOM references and required CPL rows; reject duplicate/missing rows, invalid coordinates/sides and a mismatched source PCB hash | Automatic candidates, physical rotation and solderability need review |
| Test | Trace dedicated SWDIO/SWDCLK pins to connectors/test points and require matching PCB pads and nets | Direct breakout only; voltage, reset, physical access and successful programming remain separate |

The preparation helper runs inside KiCad's Python environment. It writes only to
a temporary staged project. The board preview and fabrication package use that
analyzed board; original project bytes are checked for changes during analysis.
A failed export can preserve the analyzed copy without offering a fabrication ZIP.

## Checks still requiring engineering work

### Product-specific selection

The top-level **What we check** page separates review scope from completed results.
It uses fitted component identities, connected pin names and current product-use
answers to choose relevant areas. Unknown use remains a question. Radio capability
does not establish that wireless is used; an unconfirmed antenna-endpoint observation
stays in notes until use is confirmed. Shorts and unrouted connections remain active.
A wired product does not automatically exclude EMC or ESD.

The page includes antenna/RF, EMC, ESD, crosstalk, thermal, return-current and
transient-current scope, with the available evidence and missing analysis shown
separately. **This selection does not implement those simulations or verify those
physical properties.** Saved scope reaches AI review, chat and repair evidence.
Changing fitted parts, connections or product description requires reconfirming
scope answers. Saving the plan is local, uses no API calls and does not rerun KiCad.

**Ask questions for this project** opens the local guided interview. Questions
branch on confirmed operating choices: wireless use adds antenna-surroundings
questions; accessible cables add powered-connection and cable-environment
questions; switching loads add simultaneous-load questions. Enclosure airflow
and moisture exposure are asked where the saved design cannot establish them.
These answers select additional review work, not completed engineering tests.
Unanswered questions come before completed answers so newly relevant follow-ups
are not hidden by the interview's page limit. The app's labels and generated
explanations are English; saved part and net names are preserved.

### Remaining analysis

- Worst-case power, startup, protection and sequencing.
- Absolute maximum and operating limits, component derating and thermal behavior.
- Bus voltage compatibility, address configuration and firmware modes; measured timing under actual loading.
- High-speed and RF impedance, return paths, EMI, antenna performance and coupling.
- Mechanical tolerances, connector mating, component heights and enclosure fit.
- Polarity, final placement rotation, paste apertures and assembly process details.
- Programming access, bring-up, functional tests and product-specific validation.
- Final manufacturer interpretation of the files, stackup, holes and assembly data.

These remain **unverified** or **partial** in Overview. A successful AI
request, a generated ZIP, an accepted-risk note or a clean enabled DRC does not
complete them. There is no universal production-approval score.

## Finding locations

The PCB viewer resolves KiCad finding UUIDs against the analyzed board copy used
to render the preview. Snapshot hashes must match before geometry is attached to
an older report. Pads, tracks and vias have source coordinates, nets and layers;
the viewer does not substitute a component centre for an exact object location.

Each location keeps the objects in a finding together. A spacing finding marks
both objects; several dimension findings for the same via share one location.
Previous/next controls, fit, zoom, front/back views and copyable KiCad coordinates
help inspect each site. Native measurements remain beside the selected objects.
Reviewed objects use blue outlines when no copper edit is proposed. An unresolved
circuit path is also blue and explicitly identified as context.

PCB pads cross-select their schematic pins when those pins exist. Pin checks
prioritize connections that need a change or context. Source pin positions drive
the schematic crosshair. Component-only locations and unavailable shapes are
labeled as approximate; rounded/custom pad outlines are labeled as envelopes.
The image does not claim a precise defect location when one is not established.

## Calculation inputs

**What we check → Engineering analysis** contains the heating, load-step,
trace-loading and positive-pulse calculators and the routing measurements. Each
calculation requires all inputs and a note identifying the operating case and the
source of the numbers. Changing product context invalidates these engineering
results as well as changing the source revision. Completed results and their
assumptions are included in subsequent AI review, chat and repair context.

New analyses measure the refilled board automatically. Older saved reports offer
**Measure saved board**, which checks the snapshot hash before measuring. This
uses no AI credits and does not edit the source project. Its result is attached to
the current session; a full Update stores measurements with the next report.

The implemented equations are:

- Junction temperature: ambient + dissipation × effective thermal resistance.
  A package's JEDEC test-board value alone is not an assembly-specific value.
- Load-step drop: I × response time / capacitance + I × ESR + L × I / rise time.
  This sums estimated maxima conservatively; it is not a transient simulation.
- Positive-pulse pin stress: clamp voltage + L × di/dt. The entered clamp and pin
  limit must apply to the stated pulse, and normal voltage must fit the standoff.
- Trace drop/loss: I × R and I² × R, with an entered worst-case resistance multiplier.

Routing screening reports straight-track pairs with at least 1 mm parallel
overlap and edge spacing no greater than three times the larger track width.
This selection is a review heuristic, not a universal spacing requirement;
intentional differential pairs may appear. Ground coverage uses line/polygon
clipping, including holes in the filled copper. A projected gap may have another
valid return path. Zero gaps does not establish a connected ground plane.
Arcs, unrecognized ground names and bounded analysis work are explicitly reported
as exclusions; no RF, EMC or crosstalk pass is inferred from this screening.

Open Connections or Power & protection in Overview, choose a signal/rail,
and enter its limits and their source. Calculation results are labeled **Within
entered limits**. The app does not assume that an entered number is a guaranteed
part rating. Limits are stored locally per project, with revision checks to prevent
two tabs overwriting each other. Changed source hashes invalidate prior results;
saved inputs remain visible for review. Clear saved limits removes a calculation.
The current review checklist includes these results; a new full Update also saves
the input snapshot with the run. Historical run files remain immutable.

The I²C calculation uses the 30–70% RC rise-time relation from
[TI's pull-up resistor application note](https://www.ti.com/lit/pdf/slva689).
It applies the high resistance tolerance to rise time and the low tolerance to
sink current. It does not model active buffers or internal pull-ups.

## References

- [KiCad 9 command-line documentation](https://docs.kicad.org/9.0/en/cli/cli.html):
  native checks, schematic parity and fabrication export commands.
- [KiCad PCB editor: design-rule checking](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html#design-rule-checking):
  board rules, exclusions and verification scope.
- [KiCad zone-filler API](https://docs.kicad.org/doxygen-python-9.0/classpcbnew_1_1ZONE__FILLER.html):
  fill operation used on the staged board.
- [JLCPCB KiCad 9 export guide](https://jlcpcb.com/help/article/how-to-generate-gerber-and-drill-files-in-kicad-9):
  fabrication layer and drill handoff.
- [TI PCB design guidelines for reduced EMI](https://www.ti.com/lit/pdf/szza009):
  signal, power and return-path concerns that a geometric DRC alone cannot establish.
- [TI semiconductor thermal metrics](https://www.ti.com/lit/an/spra953d/spra953d.pdf):
  effective thermal resistance and the limits of package test-board metrics.
- [TI ESD protection layout guide](https://www.ti.com/lit/an/slva680a/slva680a.pdf):
  clamping voltage and added voltage from connection inductance.
- [TI high-speed layout guidelines](https://www.ti.com/lit/an/scaa082a/scaa082a.pdf):
  reference-plane gaps, parallel routing and return-path context.

The implementation was exercised with KiCad 9.0.5. Other supported installations
must provide the native commands and a compatible `pcbnew` Python runtime; absent
capabilities are reported rather than assumed.
