# AI Review & Fix Design Performance — Optimization Report

Isolated copy: `/tmp/pf-perf` (release candidate and user demo server untouched).
Board: faulty WenneBoard (`falsch.kicad_pcb`, SHA-256
`a70fbff042343d33d624b3c39c327a44cef7101414073d8556633b0fd79bda41`).
Model: `meta/muse-spark-1.3-contributor` for every AI request.

## 1. Baseline (release code, measured before changes)

| Stage | Measured cost |
|---|---|
| Deterministic local checks | ~6 min total. KiCad NETLIST/ERC fast. DRC: >90 s first attempt → timeout → ~85 s retry ≈ 3.5 min. Copper refill + Gerber ≈ 5 min (kicad-cli binary runtime; single-instance locks forbid parallel KiCad). LCSC per-component loop ≈ 44 s for ~460 refs (≈20 slow HTTP lookups × ~2 s, rest instant). |
| AI investigation phase | `elapsed_seconds` 710–869 s (≈12–14.5 min), 9–10 Meta requests, ~360–460 k tokens per wrong-board review (reports `falsch-20261102-211055/212452/214009/215719`). Reference run `…215719`: 868.9 s, 10 requests, 414 853 in / 28 033 out tokens (≈$0.05 at $0.10/$0.20 per M). |
| User-perceived total | ~27–40 min (deterministic + AI + orchestration/polling overhead). |

Bottleneck ranking: (1) sequential Meta round-trips (~75 s each — model latency + large contexts, not just count); (2) DRC timeout+retry; (3) LCSC sequential HTTP; (4) investigation tasks sequential — but only `search_parts` is I/O-bound (~8.4 s each) while the rest are fast local tools, so parallelizing the task loop was rejected as low-value/high-risk (shared ledger/packet/documents mutation, ordering guarantees).

## 2. Changes (perf copy only, nothing merged to release)

1. **`native.py` — DRC gets a 180 s first attempt** (`_CHECK_TIMEOUTS`). The
   90 s limit was below the board's genuine ~85–90 s cold-cache DRC time, so
   every run paid timeout + retry (≈3.5 min). Same command, same checks; only
   the kill timer changed. Retry retained for genuine transients.
2. **`ai/investigation.py` — consult ∥ gauge.** FAULT still runs first
   (consult reads its questions). Consult and gauge then run on two worker
   threads, saving one full model round-trip. Safe because: fault's mutations
   are complete before both start; consult writes `consultations` + events
   while gauge writes `gauge_sureness` + `open_questions` + events (disjoint
   fields); gauge's eligible set is identical before/after consult (consult
   changes no claim status). Full fault ∥ consult ∥ gauge was rejected:
   consult would see zero fault questions (coverage loss) and gauge would race
   fault's status mutations.
3. **`ai/ledger.py` — RLock around `add_claim`/`record_event`.** Claim IDs
   (`H{len+1}`) and event seq numbers would collide under threads.
4. **`ai/usage.py` — locked `RequestBudget.consume` + context propagation.**
   Two real bugs found during implementation: worker threads don't inherit
   contextvars (budget read `None` → both specialists silently skip as
   "allowance reserved"), and one `Context` object can't be entered twice
   (the CLI suite caught the resulting `RuntimeError`). Each worker now gets
   its own `copy_context()` copy; the shared budget object enforces one
   limit across threads.
5. **`datasheets.py` — phased parallel LCSC fetch.** Same decisions, same
   order, same 80-key cap, same deferral/timeout messages; only the wait is
   shared: rounds of 4-worker fetches (catalog searches → footprint-hint
   retries → datasheets) with local passes reusing the exact original
   attach/verify logic. Errors replay at their original sites. One fetch per
   distinct key, as before.
6. **`webapp.py` — undo-specific guard message.** The KiCad-close guard now
   says "restoring the backup" for undo instead of "applying the edit".
7. **Attempt-patience fix (post-benchmark-1).** Investigation-trio Meta calls
   (`fault`/`consult`/`gauge`) 60→120 s; circuit-audit per-part scope
   80→150 s (still bounded by the 480 s audit deadline). A single healthy
   response slower than the old cap was killed and counted as "not
   reviewed" with a dead retry (the retry needs deadline headroom the
   timeout itself consumes). No extra requests, no prompt changes.

Deliberately NOT changed: KiCad parallelization (single-instance locks, plus
an explicit `_KICAD_LOCK`), investigation task loop (low gain, shared-state
risk), semantic question dedup (`unanswered_followups` exact-match is
intentional — semantic merging needs model reasoning; Fix Design itself asks
zero questions and makes zero AI calls, verified in `file_fixes.prepare`).

## 3. Before/after benchmark (bounded live runs)

Two full headless CLI reviews, same board (`a70fbff…`), same model
(`muse-spark-1.3-contributor`), cold LCSC cache each (separate output dirs).

| | Baseline (release, report `…215719`) | Run 1 (perf, `bench-reports`) | Run 2 (perf + patience, `bench-reports2`) |
|---|---|---|---|
| Total wall | ~19–21 min (6 det. + 14.5 AI) | **9 m 22 s** | **10 m 17 s** |
| AI phase (`elapsed_seconds`) | 868.9 s | **527.0 s** | **585.6 s** |
| Meta requests | 10 / 12 | 11 / 12 | 12 / 12 |
| Tokens in / out | 414 853 / 28 033 | 235 289 / 43 595 | 314 749 / 70 002 |
| Est. cost | ~$0.047 | ~$0.032 | ~$0.046 |
| Actions produced | 14 | 7 (all high-value kept) | 6 (all high-value kept, SRV05-4 present) |

Run-2 detail: zero Meta timeout failures (patience fix verified — the 4
per-part audit timeouts and the fault timeout are gone; remaining
"not reviewed" notes are audit scoping + budget-reserve skips, same classes
as baseline). Budget hit 12/12: patience converts kills into completions,
which spend requests — still hard-bounded by the limit. Action-count
variance across runs (14/7/6) with identical evidence is model variance in
the evidence-review stage, not an optimization effect: datasheets,
documents, tasks, and specialist completion were equivalent in all runs.

Run-1 detail (all verified from `bench-reports/…/ai-review.json`):
- DRC: single pass, no retry (no retry message in full output; previously
  guaranteed timeout + ~3.5 min on this board).
- LCSC: cold-cache fetch window ≈ 13 s for 65 files (cache mtimes
  07:15:20→07:15:33) vs 44 s sequential — same 17 document codes, same
  250-read/195-unavailable split as the release sequential run.
- Investigation: 316 s, 8/8 tasks executed, consult+gauge both ran
  (`audit_completed: 4 of 18 findings`); fault hit a 60 s Meta read timeout
  → `consult_skipped` knock-on (correct given no fault questions).
- Datasheets byte-identical to baseline (same 17 LCSC codes); live
  search_parts retrieved the SRV05-4 TVS excerpts (`D:C2836319`) again.
- Finding quality: all high-value findings present, several MORE specific
  than baseline (SRV05-4 with SOT-23-6 package + 0.6 pF + D: citation; LED
  finding names nets D168–170). 5 low-value baseline items absent (edge
  clearance, U3/U7 tab pads, GND stub, Q_SEL dot, corner/length notes) —
  traced to 4 circuit-audit per-part 80 s Meta timeouts + 3 budget-reserve
  skips + model variance, NOT to missing evidence.
- Patience fix (§2.7): trio attempt cap 60→120 s, audit per-part 80→150 s
  (bounded by the 480 s audit deadline). Run 2 tests whether slow-but-healthy
  responses recover the remaining findings.

## 4. Fix Design profile

- Prepare (`file_fixes.prepare`): fully deterministic —
  `value_candidate`/`connection_candidate` plus KiCad `validate_connection`
  on disposable staged copies. Zero Meta calls, zero questions.
- No redundant research by construction: the fix path reuses the validated
  review packet and never re-runs datasheet searches or regenerates plans.
- No repeated questions by construction either: approved design context is
  carried into retries by `prior_design_answers`, keyed to the same finding
  AND exact source revision (stale answers are never silently reused), and
  review questions are filtered by `unanswered_followups`. The residual gap
  is semantic duplicates (same intent, different wording), which exact-match
  filtering deliberately leaves to model reasoning — merging those would
  need embeddings or an extra model call per question.
- Undo/rollback: backup + SHA-256 verification intact; the KiCad-close guard
  said "applying the edit" even for undo — now says "restoring the backup"
  (`webapp.py`), pinned by an extended assertion in
  `tests/test_file_fixes.py::…state_requires_separate_confirmation…`.
- Measured e2e fix timings on the disposable WenneBoard copy
  (`tests/test_fix_design_e2e.py`, 13 passed): analysis 3.9 s, value-repair
  prepare 18.6 s (dominated by KiCad before/after validation on staged
  copies — the safety cost, not waste), grouping/dedupe 0.0 s,
  oversized-response retry 1.3 s, split retry 0.2 s, failed-Meta path 1.6 s,
  reload/re-verify ~33 s. Prepare itself is sub-second logic; the 18.6 s is
  almost entirely KiCad staging + `validate_connection`, unchanged by this
  work (no AI calls exist to remove).

## 5. Tests

- New `tests/test_perf_optimizations.py` (6 tests): one-fetch-per-key +
  order, per-reference error isolation, ledger ID uniqueness under 8-thread
  × 60-claim contention, budget limit enforcement across threads, shared
  budget via copied contexts, and the threaded consult/gauge orchestration
  (mutation-checked: fails on shared-Context code, passes on the fix).
- Full related suites in perf copy: 227 passed (perf, assembly/datasheets,
  investigation, AI review, webapp, fix candidate/API/e2e/plan/workspace,
  file fixes) + 13 Fix Design e2e passed with timings above.
- `test_cli_prompt_and_json` caught the shared-`Context` bug during the work;
  fixed and mutation-pinned by the new threading test. No outstanding
  failures in any suite run for this task.

## 6. Limits and recommendation

- Measured saving: 9–10 min total vs ~20 min baseline (~50%); AI phase
  527–586 s vs 869 s (~35–40% faster). Larger than the estimated 3–4 min
  because the baseline also carried a DRC retry and slower Meta responses;
  expect ~15–20% on like-for-like latency, more when DRC/LCSC were the
  binding constraints. Sub-15-min headless totals were achieved in both runs
  (9:22, 10:17); the 40-min user figure includes web overhead on top.
- Action-count variance (14/7/6) is model variance under identical evidence,
  not an optimization effect. Do NOT chase the count: all high-value
  findings (LED dimness, QSPI swap, LD1117 stability, USB ESD + SRV05-4 with
  D: citation, USB fuse, CC 5.1 kΩ) were present in every run.
- Do NOT merge automatically. The LCSC restructure is the largest diff and
  deserves review; the threading change is small and fully tested.
- If a further round is wanted, the honest options are: fewer/smaller
  investigation prompts (coverage trade-off — declined here), or caching
  stable evidence across re-reviews of unchanged boards.

## 7. Candidate acceptance run (web, 2026-11-03)

First full web-path review on the integrated candidate
(`ModularKeyboard-wrong-20261103-162539-by0xihwd`, port 8813):
AI completed, 698 s, 12/12 requests, ~904k tokens (~$0.10),
**14 actions — the complete finding set** (LED dimness, QSPI swap, U3/U7
tab pads, USB ESD + SRV05-4 with D:C2836319, USB fuse, CC 5.1 kΩ, bulk
caps, ceramics, edge clearance, stubs, D+/D- length, bends).
`board-front.svg` (2.8 MB) served 200 — history-card preview renders.
Demo cards show the honest "Preview available after a board check" slot
until a check runs. Total Meta spend for all benchmark/acceptance runs
this cycle ≈ $0.23, within the $0.5 testing budget.
