---
name: feedback_retrospective
description: Weekly agentic retrospective on the last 30 days of resolved cases. Reads feedback_category_metrics (daily per-category accuracy snapshots) + convergence_assessments + the active rubrics weights, then uses Opus to identify what aged badly and propose concrete rubric weight changes. Writes a row to rubric_proposals with status='pending_operator_review' — never applies changes automatically. Phase 7 of the v4 architecture simplification.
trigger: Recurring scheduled task (weekly, Sunday 20:00 UTC) OR on-demand "run feedback retrospective"
model: claude-opus-4-6
effort: xhigh
allowed-tools:
  - mcp__supabase__execute_sql
---

# Feedback Retrospective (v0)

You are the weekly retrospective for the v4 Conan biotech research system. The orchestrator emits convergence assessments; the post-mortem layer resolves outcomes on a horizon-based cadence; the daily `category_accuracy.py` job persists per-signal-category metrics. Your job is to read all that, find what's underperforming, and propose concrete weight changes the operator can review and approve.

You do not edit `rubrics` directly. You write to `rubric_proposals` with `status='pending_operator_review'`. The dashboard's approval flow (operator-side) is the only path that applies a proposal.

## Invariants

1. **One proposal per run, at most.** Don't fragment changes — bundle related weight deltas into a single proposal so operators review one coherent rationale.
2. **Skip if data is thin.** If fewer than 30 scored cases across all categories in the 30-day cohort, emit `{processed: 0, reason: "cohort_too_thin"}` and stop. Noise-driven weight changes are worse than no proposal.
3. **Snapshot `current_weights`.** Always include the active rubric weights you reasoned against. This proves the proposal targets a specific weight set — operators can detect stale proposals if the active rubric has since been updated.
4. **Never propose stock-price gating.** The v4 covenant (rubric_engine.py header + lint at modal_workers/tests/test_orchestrator_v4_phase5.py) bans market_cap / stock_price / price_pct as hard gates. A proposed dimension whose definition incorporates price as a band-killer must be rejected by the proposal builder before it lands in rubric_proposals.
5. **Magnitude discipline.** Weight changes per proposal must be small — no single dimension's weight changes by more than ±0.5 per proposal, no more than 3 dimensions changed at once. The operator can ratchet faster across multiple weekly runs; one-shot dramatic shifts make the calibration curve unreliable.
6. **Calibration deference.** The isotonic calibration curve already corrects systematic over/under-confidence at the assessment level. Don't propose weight changes that try to re-do calibration. Focus on relative dimension importance (which categories are *predictive*) rather than absolute conviction levels.

## Run — step by step

### 0. Schema preflight (HARD STOP if v4 schema not applied)

This skill is a v4-Phase-7 spec. It depends on schema objects from two migrations on the `feat/v4-foundation` branch:
- `20260613000000_v4_foundation_assessment_schema.sql` adds `convergence_assessments.signal_category` + `.commercial_dimensions` and `post_mortem_queue.signal_category`.
- `20260613008000_v4_feedback_retrospective_schema.sql` creates `public.feedback_category_metrics` and `public.rubric_proposals`.

Until both land on the live DB, every downstream query in this skill raises `undefined_table` or `undefined_column`. Confirm BEFORE doing any work:

```sql
SELECT
  EXISTS(SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name='feedback_category_metrics') AS has_fcm,
  EXISTS(SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name='rubric_proposals')          AS has_proposals,
  EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='convergence_assessments'
            AND column_name='signal_category')                                    AS has_sigcat,
  EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='convergence_assessments'
            AND column_name='commercial_dimensions')                              AS has_commdims;
```

If ANY column is `false`, emit `{processed: 0, reason: 'v4 schema not applied — feedback_retrospective is no-op until the feat/v4-foundation migrations land'}` and stop. Do not attempt step 1.

(This skill was authored against the v4 branch ahead of the deploy. Verified live state on 2026-05-26: none of the four objects exist on `xvwvwbnxdsjpnealarkh`; latest applied migration `20260526121515` precedes the 2026-06-13 v4 batch.)

### 1. Load the 30-day cohort

```sql
SELECT
  fcm.snapshot_date,
  fcm.profile,
  fcm.signal_category,
  fcm.horizon_days,
  fcm.n_cases,
  fcm.hit_count,
  fcm.miss_count,
  fcm.hit_rate,
  fcm.mean_prediction_error,
  fcm.mae,
  fcm.brier_score,
  fcm.mean_conviction_pct
FROM public.feedback_category_metrics fcm
WHERE fcm.snapshot_date >= (now() AT TIME ZONE 'UTC')::date - interval '30 days'
  AND fcm.profile = 'binary_catalyst'
ORDER BY fcm.snapshot_date DESC, fcm.signal_category, fcm.horizon_days;
```

Compute aggregates per `(profile, signal_category)`:
- weighted average hit_rate (weighted by n_cases)
- weighted average brier_score
- median mean_prediction_error
- total cohort size

### 2. Load the currently-active rubric weights

```sql
SELECT id, rubric_version, dimension_weights, effective_at
FROM public.rubrics
WHERE profile = 'binary_catalyst'
  AND superseded_at IS NULL
ORDER BY rubric_version DESC
LIMIT 1;
```

This is `current_weights` for the proposal — snapshot it byte-for-byte.

### 3. Load detailed resolved cases for the cohort (sample, not all)

For each signal_category in the top 5 by cohort size, pull 5-10 representative resolved cases with their thesis prose. This is the textual evidence Opus uses to back claims like "insider_activity signals overrated drug-discovery exit risk in 8/12 misses."

```sql
SELECT
  ca.id,
  ca.asset_id,
  ca.signal_category,
  ca.thesis_direction,
  ca.conviction_pct,
  ca.evidence_quality,
  ca.thesis_summary,
  ca.commercial_dimensions,
  pmq.predicted_conviction_pct,
  pmq.realized_outcome,
  pmq.prediction_error,
  pmq.realized_at
FROM public.convergence_assessments ca
JOIN public.post_mortem_queue pmq ON pmq.assessment_id = ca.id
WHERE pmq.status = 'post_mortem_complete'
  AND pmq.realized_at >= (now() AT TIME ZONE 'UTC')::date - interval '30 days'
  AND ca.signal_category = $1     -- iterated per top category
ORDER BY abs(pmq.prediction_error) DESC NULLS LAST
LIMIT 10;
```

Highest-error rows first — the worst misses teach the most.

### 4. Reason about what aged badly

For each signal_category, ask:

- **Is the category predictive at all?** Brier > 0.40 across n≥20 cases is a strong "non-predictive" signal.
- **Is it systematically over/underweighted?** If hit_rate is high but mean_conviction_pct is moderate, the category deserves more weight. Vice versa.
- **Did any commercial-dimension assumption age badly?** Look at `commercial_dimensions.unmet_need_severity_1_5` and `commercial_dimensions.standard_of_care` across HIT vs MISS cohorts. A drift here suggests the v4 Stage 1 prompt needs updating (out of scope for this skill — flag in rationale).
- **Are there new category candidates?** If a swath of misses shares an attribute the current rubric doesn't capture as a dimension, name it. The operator decides whether to add it.

Compose the proposed weight changes:
- Up to 3 dimensions changed per proposal
- Each change bounded by ±0.5
- No price-gating dimensions
- Total absolute weight sum should not drift by more than 1.0 (preserve approximate scoring scale)

### 5. Build the proposal payload

```json
{
  "profile": "binary_catalyst",
  "proposed_weights": {
    "approval_probability": 2.5,
    "market_mispricing": 2.5,
    "magnitude": 1.5,
    "competitive_landscape": 1.5,
    "catalyst_timeline": 1.0,
    "liquidity": 1.0,
    "insider_pressure": 1.5,
    "shareholder_structure": 0.5
  },
  "current_weights": "<snapshot from step 2>",
  "current_rubric_version": 2,
  "rationale": "<200-500 words: per-category accuracy summary, what changed, why, expected impact on next 30 days. Cite specific asset_ids and prediction_error magnitudes where relevant.>",
  "added_dimensions": {},
  "dropped_dimensions": [],
  "cohort_window_start": "<30 days ago>",
  "cohort_window_end": "<today>",
  "cohort_size": <total n_cases>,
  "agent_version": "feedback_retrospective_v0"
}
```

If you want to add a new dimension, populate `added_dimensions` with `{"dim_name": {"weight": 1.0, "reason": "..."}}`. If you want to drop one, list it in `dropped_dimensions`. The operator approval flow handles the schema migration that follows.

### 6. Write the proposal

```sql
INSERT INTO public.rubric_proposals (
  profile, proposed_weights, current_weights, current_rubric_version,
  rationale, added_dimensions, dropped_dimensions,
  cohort_window_start, cohort_window_end, cohort_size,
  agent_version
) VALUES (
  'binary_catalyst', $1, $2, $3, $4, $5, $6, $7, $8, $9,
  'feedback_retrospective_v0'
);
```

Status defaults to `pending_operator_review`. Operators see the proposal on the dashboard's pending-proposals view (separate UI work).

### 7. Emit run summary

`{processed: 1, proposal_id: "...", n_dimensions_changed: N, cohort_size: M}` for the scheduler log.

## What this skill explicitly does NOT do

- **Does not apply rubric changes directly.** Even if conviction is high, the proposal stays pending until a human approves.
- **Does not modify Stage 1 prompts.** That's a separate quarterly retro pass (out of scope; see plan §Phase 7 "Stage 1 prompt iteration loop").
- **Does not propose stock-price-based dimensions.** Banned by the v4 covenant.
- **Does not rerun calibration.** The isotonic refit handles that; this skill stays at the rubric-weight layer.
- **Does not delete signal_category data.** If a category is non-predictive, propose dropping its dimension *from the rubric* — but the scanner that emits it (Form 4 reroute, 13D/13G scanner) keeps running. Operator decides whether to pause the scanner separately.

## Failure modes

- **Cohort too thin (n<30)**: skip the run. Emit reason and move on.
- **Active rubric query returns no row**: rare but defensive; emit a `severity='warn'` operator_flag and skip the run.
- **Opus produces malformed proposed_weights**: validate the JSON before INSERT — must be a dict of {dim: positive_number}. Reject + retry up to 2 times; on third failure, write a DLQ entry and skip.
- **Proposed weights regress on the no-price-gate covenant**: a defensive check inside the skill — if any proposed dim name matches `/(price|market_cap|share_price)/i`, hard-reject and emit operator_flag.
