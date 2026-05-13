---
name: skill_eval_replay
description: Sweep the 271-case `public.eval_harness` set (or a filtered subset) through the `assess-fda-binary-catalyst` skill, score each prediction against the realized outcome, and emit an aggregate report vs. the live orchestrator's `convergence_assessments` for the same `(asset_id, reference_assessment_date)`. Sidecar mode — does NOT touch live pipeline tables. Output is a single `eval_run_summary.json` plus per-case JSONL. Drives the answer to "does a single Opus 4.7 skill invocation beat the multi-stage chain on the 271 historical PDUFAs?"
trigger: On-demand only. Operator phrases: "run skill eval replay", "score the orchestrator skill", "skill eval [holdout|dev|all] [N]". No cron — this is a deliberate backtest, not a recurring sweep. Cost is bounded (~$136 for full 271-case sweep at $0.50/case target) but non-trivial; gate via operator confirm.
quota: 271 cases per full sweep (~$136 at target). Scope-limited subsets: `holdout` (81 cases, ~$40), `dev` (190 cases, ~$95), or explicit `N` cap. Skill self-aborts if rolling per-case mean exceeds $1.00 (declared in `assess-fda-binary-catalyst/SKILL.md` §Compliance).
host: pedro
host_enrollment: Cowork on-demand task `conan-skill-eval-replay`. NOT scheduled (no cron line). Operator runs manually via the dashboard or CLI when validating a skill change.
---

You are the eval-harness driver for the `assess-fda-binary-catalyst` skill. Your job is **batch coordination + scoring + reporting** — you don't reason about FDA outcomes yourself. The reasoning lives in the skill; you drive the loop and do the deterministic scoring math.

The 271 cases in `public.eval_harness` are resolved historical FDA binary catalysts (2023-2025). Each row has the `(asset_id, reference_assessment_date, document_set, realized_outcome)` tuple needed to (a) feed the skill an as-of input snapshot with no leakage and (b) grade the output. The grading math is already in `orchestrator_runtime/eval_harness/gold_standard.py` and `metrics.py` — call those rather than re-implementing.

## Invariants

1. **No leakage at the driver level.** When you build the input payload for the skill, `realized_outcome` and `realized_outcome_data` MUST NOT be passed in. The skill's leakage self-check (Step 0 in `assess-fda-binary-catalyst/SKILL.md`) is a backstop, but the driver is the first line of defense. If you pass `realized_outcome` to the skill, you have introduced a bug.
2. **One eval_case → one output row OR one explicit refusal.** Every case the driver picks MUST produce either a successful `<output_dir>/<case_id>.json` (with skill JSON) or an entry in `<output_dir>/_refusals.jsonl` with the refusal reason. Never skip silently.
3. **The skill's output is the contract.** Don't re-score, re-classify, or post-process the skill's `thesis_direction` / `conviction_pct` before passing them to `is_direction_correct` and `calibration_curve`. If the skill emits something the harness can't score, that's a finding worth surfacing — record it, don't paper over it.
4. **No writes to live pipeline tables.** This routine reads `eval_harness`, `documents`, `extracted_facts`, `asset_documents`, `fda_assets`, `convergence_assessments`. It writes ONLY to disk (`<output_dir>/`). It MUST NOT INSERT/UPDATE `convergence_assessments`, `orchestrator_runs`, `skill_assessments`, or any other live table. Sidecar = parallel, never overwrites.
5. **Sequential per-case execution.** The skill calls Opus 4.7; running 271 in parallel would violate the Tier-1 30K tokens/min rate limit and inflate cost via cache miss. Process one case at a time. Wall-clock target ≤ 7h for the full set; partial-progress checkpointing means a mid-run interrupt resumes cleanly (see step 6).
6. **Cost ceiling enforcement.** If rolling per-case mean cost exceeds $1.00 OR cumulative cost exceeds the per-sweep budget (default $200), abort the sweep and write the partial summary. The skill itself enforces a per-case cap; the driver enforces the sweep-level cap.

## Run — step by step

### 1. Parse trigger args

Operator phrase determines scope:

| Phrase | Scope | Filter |
|---|---|---|
| `run skill eval replay holdout` (default) | 81 cases | `is_holdout = true` |
| `run skill eval replay dev` | 190 cases | `is_holdout = false` |
| `run skill eval replay all` | 271 cases | none |
| `run skill eval replay N=20 holdout` | first 20 of holdout | `LIMIT 20` |

Default to `holdout` if ambiguous — the holdout set is the gating set for promotion decisions and is the smallest spend. Confirm scope with the operator if the phrase implies > 100 cases.

### 2. Load eval cases

Use the existing loader to ensure schema parity:

```python
from orchestrator_runtime.eval_harness.gold_standard import (
    load_holdout_set, load_dev_set, load_all,
)

cases = load_holdout_set()   # or load_dev_set() / load_all()
if scope_limit:
    cases = cases[:scope_limit]
```

Or via Supabase MCP if Python is unavailable in this Cowork session:

```sql
SELECT id, asset_id, reference_assessment_date, document_set,
       is_holdout, difficulty, notes
  FROM public.eval_harness
 WHERE ($1::bool IS NULL OR is_holdout = $1)
 ORDER BY reference_assessment_date DESC
 LIMIT $2;
```

DO NOT select `realized_outcome` or `realized_outcome_data` here — those are loaded later, *only* for scoring, in step 5. Keeping the prediction-input query free of outcome columns prevents an "oops I passed it through" bug.

### 3. Pre-create output directory

```
<repo_root>/skills_v2/assess-fda-binary-catalyst/outputs/eval_runs/<run_id>/
  ├── _index.jsonl       (one line per completed case; written by the skill)
  ├── _refusals.jsonl    (one line per refused case; written by this routine)
  ├── _progress.json     (rolling cost + completion counter; written by this routine)
  ├── <case_id>.json     (skill output; written by the skill)
  └── <case_id>.reasoning.md  (Opus prose; written by the skill)
```

`<run_id>` = `eval_<scope>_<UTC timestamp>` e.g. `eval_holdout_2026-05-12T08-45Z`. This is the report's primary key.

### 4. Per-case loop

For each case:

1. **Resume check.** If `<output_dir>/<case_id>.json` already exists and validates, skip — partial-progress safety. Increment `_progress.json.skipped`.
2. **Cost gate.** Read `_progress.json`; if `mean_cost_per_case > 1.00` or `cumulative_cost > sweep_budget`, abort the sweep, write `_progress.json.aborted_reason`, jump to step 7.
3. **Build skill input** (no realized outcome):
   ```json
   {
     "asset_id": "<case.asset_id>",
     "reference_assessment_date": "<case.reference_assessment_date>",
     "document_set": [<case.document_set uuids>],
     "eval_case_id": "<case.id>",
     "output_dir": "<output_dir>",
     "enable_subskills": true
   }
   ```
4. **Invoke `assess-fda-binary-catalyst`** with the input. Capture stdout JSON (`status`, `cost_usd`, `latency_ms`).
5. **On `status: "ok"`**: append the skill's stdout JSON to `_index.jsonl`. Update `_progress.json.completed += 1`, `_progress.json.cumulative_cost += cost_usd`.
6. **On `status: "refused"`**: append `{case_id, asset_id, reference_assessment_date, reason, detail}` to `_refusals.jsonl`. Update `_progress.json.refused += 1`. Continue to next case — refusals are signal, not failure.
7. **On exception** (skill crashed, Opus 429, etc.): append `{case_id, error_message}` to `_failures.jsonl`. If error is `credit_exhaustion` or `rate_limit_persistent`, abort the sweep — operator must reset before continuing.

### 5. Score completed cases

After the loop completes (or aborts), score every entry in `_index.jsonl`:

```python
from orchestrator_runtime.eval_harness.gold_standard import (
    load_all, is_direction_correct,
)
from orchestrator_runtime.eval_harness.metrics import aggregate

# Re-load cases keyed by id (NOW we read realized_outcome — for scoring only)
all_cases = {c.id: c for c in load_all()}

per_case = []
for line in open(index_path):
    rec = json.loads(line)
    case = all_cases[rec["eval_case_id"]]
    direction_correct = 1 if is_direction_correct(rec["thesis_direction"], case) else 0
    per_case.append({
        "eval_case_id": case.id,
        "asset_id": case.asset_id,
        "reference_assessment_date": case.reference_assessment_date.isoformat(),
        "predicted_direction": rec["thesis_direction"],
        "conviction_pct": rec["conviction_pct"],
        "realized_outcome": case.realized_outcome,
        "direction_correct": direction_correct,
        "cost_usd": rec["cost_usd"],
        "latency_ms": rec["latency_ms"],
    })

result = aggregate(
    orchestrator_version="skill-v0",
    prompt_hash="<sha of SKILL.md as of run time>",
    per_assessment_results=per_case,
    reference_brier=None,   # set on subsequent runs to compare
)
```

Save `result.as_eval_runs_row()` to `<output_dir>/eval_run_summary.json`.

### 6. Comparison vs. live orchestrator

For each scored case, JOIN to `public.convergence_assessments` to find the live orchestrator's prediction on the same `(asset_id, reference_assessment_date)`:

```sql
SELECT asset_id, conviction_pct, thesis_direction, evidence_quality, cost_usd
  FROM public.convergence_assessments
 WHERE asset_id = ANY($1::uuid[])
   AND date(created_at) <= $2          -- created on/before the reference date
   AND superseded_at IS NULL
   AND tier IN (1, 2)
 ORDER BY created_at DESC;
```

For each `(asset_id, reference_assessment_date)`, take the most recent live assessment created on or before the reference date. If none exists (asset never went through the live orchestrator on that date), the case is skill-only — record in `_meta.live_orchestrator_coverage`.

Compute side-by-side metrics:
- `skill_brier` vs `live_brier` (where both predictions exist)
- `skill_auc` vs `live_auc`
- Per-case agreement matrix (skill_direction × live_direction × realized_outcome)
- Cost delta: skill mean cost vs live mean cost on the same case set

Write to `<output_dir>/comparison_summary.json`.

### 7. Final report

Emit a Markdown summary at `<output_dir>/REPORT.md`:

```markdown
# Skill eval replay — <run_id>

**Scope:** <scope> (<n_cases> cases)
**Skill version:** assess-fda-binary-catalyst v0
**Model:** claude-opus-4-7
**Run wall-clock:** <duration>
**Cost:** $<total> (mean $<per_case>/case)

## Headline metrics
- Brier score: <skill_brier> (live: <live_brier>, delta: <±X>)
- Ranking AUC: <skill_auc> (live: <live_auc>)
- Direction-correct rate: <X>% (<n_correct>/<n_scored>)
- Calibration deviation (worst bucket): <bucket_range> over-/under-confident by <pp>

## Refusals
- <n_refused> refusals (<%>)
- Top reason: <reason>

## Coverage gaps
- <n> cases with no live-orchestrator prediction (skill-only)
- <n> cases where skill direction differed from live direction

## Per-case file
See `_index.jsonl` and `comparison_summary.json` for raw rows.

## Recommendation
- [ ] Skill outperforms live orchestrator on this slice → consider sidecar live deployment
- [ ] Skill matches → no change; iterate on prompt
- [ ] Skill underperforms → investigate <specific failure pattern>
```

Stdout JSON for the operator dashboard:

```json
{
  "run_id": "<run_id>",
  "scope": "holdout",
  "n_cases": 81,
  "n_completed": 78,
  "n_refused": 2,
  "n_failed": 1,
  "skill_brier": 0.21,
  "live_brier": 0.24,
  "skill_auc": 0.68,
  "live_auc": 0.65,
  "direction_correct_rate": 0.62,
  "total_cost_usd": 38.40,
  "wall_clock_seconds": 14820,
  "report_path": "<output_dir>/REPORT.md"
}
```

## Known dependencies (verify before scheduling)

1. **`assess-fda-binary-catalyst` skill exists** at `conan-cowork-skills/skills_v2/assess-fda-binary-catalyst/SKILL.md`. If missing, this routine is a no-op — fail fast with `skill_not_found`.
2. **Sub-skills P1 + U3** referenced by the skill exist (per STATUS.md, P1 is Tier-2 / smoke-test only, U3 is Tier-1). If P1 or U3 errors, the skill widens its probability spread and continues — the driver does not need to special-case.
3. **Eval-harness Python loader importable**: `orchestrator_runtime.eval_harness.gold_standard` + `metrics`. If running outside the conan repo, fall back to inline SQL + reimplement `is_direction_correct` from `gold_standard.py` lines 99-116 verbatim.
4. **Anthropic API credit balance positive.** A credit-exhaustion mid-sweep aborts the run and the partial outputs are preserved. Operator must restock credits before resuming. (Cf. 2026-05-11 incident — credit exhaustion took the live orchestrator down for ~14h.)
5. **Read access to `public.documents`, `public.extracted_facts`, `public.asset_documents`, `public.fda_assets`, `public.convergence_assessments`, `public.eval_harness`** — all read-only. The dashboard RLS unlock from PR #34 (commit 29d37f4) covers these.

## Reference

- Skill being driven: `conan-cowork-skills/skills_v2/assess-fda-binary-catalyst/SKILL.md`
- Eval cases: `public.eval_harness` (271 rows, schema in migration `20260506000010_v3_phase_0_1_schema.sql`)
- Loader: `orchestrator_runtime/eval_harness/gold_standard.py` — `HarnessCase`, `load_holdout_set`, `load_dev_set`, `load_all`, `is_direction_correct`
- Scoring: `orchestrator_runtime/eval_harness/metrics.py` — `calibration_curve`, `ranking_auc`, `aggregate`, `EvalRunResult.as_eval_runs_row`
- Live-orchestrator output it compares against: `public.convergence_assessments` (Stage 9 output, schema source-of-truth)
- Existing replay infrastructure (orchestrator-side, not skill-side): `orchestrator_runtime/eval_harness/replay_runner.py` — uses cassette-based replay against the live orchestrator. Complementary to this routine; not invoked here.
- Decision context: D-100 (FDA + EDGAR depth pivot, 2026-05-06), D-103 (paired-bootstrap promotion gate ≥ 200 resolved cases), 2026-05-12 conversation re. orchestrator failure modes + Opus 4.7 default cutover (PR #40).
