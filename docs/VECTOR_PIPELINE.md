# Vector investigation

In **Vector settings → Vector usage**, choose **Vector investigation · detailed review**,
save settings, and run the review. Select Meta and configure its key to run the same
workflow through the Meta adapter. The implementation also supports the other configured
providers. Choosing the detailed mode does not switch providers.

## What runs

1. Preflight collects the saved revision, product context, component inventory, nets,
   local checks, and matched datasheet text.
2. Vector proposes up to eight targeted evidence tasks. Local validation rejects
   nonexistent components, unsupported tools, repeated tasks, and excessive plans.
3. Python executes the selected tools: connection tracing, matched PDF retrieval,
   calculations with saved operating limits, layout evidence, and manufacturer checks.
4. Vector interprets the gathered evidence. Existing validators check component/pin/net
   identities, quoted passages, disposition constraints, and concrete actions. A focused
   correction can revise rejected explanations.
5. The circuit audit prioritizes the selected parts while retaining its baseline targets.
   It compares power, interfaces, configuration, external parts, and signal paths.
6. Unresolved or concerning comparisons trigger another local search of matched PDFs.
   A second model request challenges the conclusions with the added evidence.
7. The report saves the investigation questions, executed tools, limitations, source
   revision hashes, and evidence fingerprints. Expand **How Vector investigated this
   design** in the review details to inspect them.

Planning and additional PDF searches do not write project files. The planner returns
structured tool selections; it cannot run shell commands, access arbitrary URLs, or
declare a board passed. A gathered passage is not proof that its interpretation is correct.

## Evidence Ledger

Every investigation records an append-only ledger (`ai/ledger.py`) inside the saved
pipeline record: the planner's product hypothesis as a claim, one event per executed
tool task, revision fingerprints, and coverage counts derived only from recorded
entries. The Engineering Failure Atlas (`ai/atlas.py`) adds taxonomy-driven
hypotheses from observed components, nets and coverage before the tools run;
quiet modes record why they stayed out. Design-driven discovery
(`ai/design_scan.py`) then adds structural hypotheses — lonely and high-fanout
nets, unpaired signals, local findings promoted to tracked claims, and the
connected-subsystem partition — without reading the taxonomy claims, so both
streams stay independent until they merge in the ledger. Scenario-driven discovery
(`ai/scenario_scan.py`) completes the three streams with startup, operation,
misuse, environment and assembly walkthroughs from the same packet facts.
Gate A finding verification (`ai/verdict.py`) then confirms every scoped
object and evidence link, re-runs each observation's originating detector,
and advances claims to supported, contradicted, unresolved or not_applicable
with recorded reasons; judgment-needing claims wait for specialist review.
ORIGIN (`ai/origin.py`) then rebuilds the product architecture from packet
facts alone — subsystems, supply domains, interfaces, part roles — separates
observed facts from low-confidence guesses and confirmed intentions, asks
intent questions for missing purpose and environment, and flags claims that
touch confirmed intentions without changing any status.
DOUBT (`ai/doubt.py`) then re-examines coverage without reading prior
conclusions: unexamined active parts and connectors become new claims that
reenter Gate A verification and ORIGIN review, while remaining gaps stay in
the event record with explicit overflow counts. FAULT (`ai/fault.py`) sends
one batched challenge over up to four material findings when budget allows;
validated critiques add counterevidence and open questions, and proposed
fixes enter Gate B structural verification (`verdict.verify_fix`), which
confirms references, pins and nets without ever approving values. A refuted-
with-cited-evidence critique closes an unresolved claim as contradicted via
reopening; deterministic supported verdicts never move on model opinion.
VECTOR consultation (`ai/consult.py`) then answers up to four FAULT missing
checks in one batched, evidence-cited request when budget allows; answers are
recorded with provenance and never change a status. GAUGE
(`ai/gauge.py`) then audits the same claims with independent sureness
scores, escalating weak support; scores are recorded as AI estimates, never
averaged into probabilities. Skipped and failed adversarial passes are
recorded with their reason and never block the review.
`finish()` then stores a
Coverage Proof and Investigation Replay projection (`ai/replay.py`) in the
record; Vector review → How Vector investigated this design renders them as
expandable sections, reusing the existing panel components. Older reports
without these keys render exactly as before.
FAULT sends one batched challenge over up to four material findings and
GAUGE audits the same claims when request budget allows; validated critiques
add counterevidence and open questions, proposed fixes enter Gate B
structural verification, and audit scores escalate weak support. Skipped or
failed passes are recorded with their reason. A CLI deep review now sends up
to five Gemini requests (plan, challenge, consult, audit, interpret) within the same
shared 12-request allowance.
Skipped and failed runs keep their
ledger with the limiting reason, so partial work and coverage gaps stay visible
instead of silently passing.

## Repairs

The existing repair workflow creates edits in a copy, refills copper, compares KiCad
DRC before and after, and feeds new violations and draft geometry into a bounded
correction loop. It now saves an execution history outside the disposable draft folder
and detects repeated operations that already failed on the same source revision.
The correction receives the previous DRC feedback instead of blindly validating the
same geometry again. Passing drafts still require approval before application.

The history appears under **Repair validation history** for prepared edits and is
stored beside the draft as `*-workflow.json`. Approval and rollback behavior is unchanged.

## Interview

`review_inputs` appends two ledger question streams to the existing interview
queue: ORIGIN `product_model.intent_questions` and ledger `open_questions`
starting with `RECONCILE_PREFIX`. Both pass through the unchanged filters
(datasheet-lookup skip, beginner pin-level skip, text-based answered
suppression), so technical open questions stay in the replay and only
designer-directed questions reach the interview. Answered suppression is
text-based, matching AI questions; revision-aware re-asking is deferred.

## Limits and cost

- Detailed mode retains the shared limit of 12 actual provider attempts per review or
  repair action. Planning, retries, corrections, and fallback requests share that limit.
- Low usage keeps its existing two-request review allowance and skips the added planner.
- The planner gets one attempt with a 90-second timeout and an 8000-token output
  budget. A failed plan skips only the planned tool tasks: deterministic
  discovery, both verification gates and the FAULT/consult/GAUGE loops still
  run, and the pipeline is recorded as limited.
- Meta transport retries transient HTTP 5xx and network failures only (up to
  three attempts sharing one deadline and request budget). Rejections,
  refusals, truncation and validation failures never retry.
- A crashed investigation or audit stage is recorded as unavailable with its
  cause; the completed review and pipeline still attach. Saved-key restore
  loads each provider independently, so one denied prompt keeps the rest.
- Native KiCad failures are detected by exit code and output files, never by
  stderr noise: fontconfig cache complaints under sandboxes or read-only
  homes no longer void healthy netlists. An evidence packet with parts but
  zero pin-to-net assignments records an explicit degraded-evidence
  limitation instead of silently starving discovery.
- Specialist output budgets fit their schemas (plan 8000, fault 6000,
  consult and gauge 4000 tokens); truncation surfaces as an unavailable
  stage with the provider finish reason, never as a silent pass.
- Circuit batches start only when there is capacity for both analysis and challenge.
- Identical validated reviews may be reused. Source evidence, model, product context,
  calculation inputs, and pipeline instructions participate in cache identity.
- These are request limits, not currency spending caps.
- The audit retains its existing 18-active-part limit and explicitly lists unreviewed
  parts. It does not establish complete coverage for arbitrarily large boards.
- Missing/stale operating limits remain unresolved. No RF field solver, physical thermal
  simulation, measured EMC/ESD qualification, or guaranteed autonomous repair is added.

## Offline checks

`PYTHONPATH=tests python3 -m unittest test_investigation test_circuit_audit test_ai_usage test_meta test_ledger test_atlas test_design_scan test_scenario_scan test_verdict test_origin test_doubt test_fault test_gauge test_consult test_eval test_review_inputs -q`

## Reliability evaluation

`python3 tests/eval_benchmark.py [--json]` runs the versioned labeled suite
(`tests/eval_cases.py`, currently v1): synthetic defective and correct boards
through the full deterministic flow, reporting recall, forbidden-mode hits,
unsupported-supported claims, unresolved links and fix validity. It makes no
provider calls, exits 1 on any contract failure, and measures the pipeline as
shipped — not model output quality, which needs labeled real-board review.

Tests exercise the Meta request shape with fake responses, shared budgets, source-specific
retrieval, the review/audit handoff, stale input rejection, and repeated repair detection.
They do not call a paid provider or prove real-world engineering accuracy.
