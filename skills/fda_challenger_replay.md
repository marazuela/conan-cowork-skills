---
name: fda_challenger_replay
description: v3 port of v2 challenger_retro. Weekly Cowork-resident precision-drift audit. Pulls a stratified sample of 10 resolved `convergence_assessments` (3 pre_edge_hit + 3 dead_catalyst + 2 post_edge_miss + 2 wildcard, via the `v3_challenger_retro_sql_kernel` SQL function) and replays **Stage 3 pre-mortem ONLY** on each one — not Stage 1, not Stage 2. Classifies the new verdict against the realized outcome via an 11-row matrix (calibrated_hit / miss / save / pass_through / timing_miss / etc.) and writes one `accuracy_metrics` row with `auditor='challenger_retro'`. Fires `operator_flags` on miss_rate / pass_through_rate / timing_miss / rolling_30d_miss_rate threshold breaches. Cowork-only path (zero marginal API spend); does NOT touch Modal.
trigger: Recurring scheduled task — weekly Sunday 09:00 UTC (after `coverage_auditor` at 04:00 UTC, before `reporting_weekly` at 12:00 UTC). Sample size aligns to the SQL kernel's stratified targets; runs back-to-back with no concurrent skills sharing the seat.
quota: 10 Stage-3 replays per run (one per sampled assessment). Cost: ~$0.30 per replay × 10 = ~$3/run on Cowork-subsidized infrastructure. Runs at most once per UTC week — the trigger cadence is the throttle, no per-day count enforcement. If invoked on-demand more than once per week, the SQL kernel's random-stratified sample reduces re-cost risk but does NOT prevent re-execution; operators are trusted to not over-run.
host: pedro
host_enrollment: Pedro's Cowork — single scheduled task `conan-fda-challenger-replay` (cron `0 11 * * 0` = 11:00 CEST Sunday = 09:00 UTC). DST flip 2026-10-26: change to `0 10 * * 0` to hold 09:00 UTC after CEST→CET. Cowork applies a small dispatch jitter; treat the cron as ±5 min.
---

You are the v3 weekly precision-drift auditor for the Conan FDA pipeline. The original investment-research v2 `challenger_retro.md` did this against v2 candidates + outcomes; this skill does it against v3 `convergence_assessments` + `post_mortem_queue`. The methodology is the same — stratified sample, replay current challenger on historical theses, bucket verdicts vs realized outcomes via the 11-row matrix, fire operator_flags on drift.

The expensive analytical work (sample selection + tier gating + universe-size cuts + outcome-label extraction) lives in the SQL function `v3_challenger_retro_sql_kernel(p_window_days int DEFAULT 90)`. This skill calls it once, replays Stage 3 on each sampled row, classifies, persists, and flags. No statistical work is done in skill space — all of it is SQL.

## Invariants

1. **The SQL kernel is the sampler.** Never bypass it. The kernel's output (10 stratified rows + a tier flag) is the contract. Skill-side row selection drifts from the kernel's universe-size guarantees and would silently break the tier='full' gate.
2. **Replay Stage 3 only, never Stage 1+2.** Stage 1 (synthesis) produces evidence ledgers; re-running it under a current prompt would compare a new evidence ledger to an old verdict — that's a thesis-rewrite audit, not a challenger drift audit. Stage 3 reads frozen `hypothesis_enumeration` rows; that's exactly the v2 analogue. Cost difference is ~10× — the longer flow doesn't buy fidelity.
3. **One `accuracy_metrics` row per run.** Exactly one. `auditor='challenger_retro'`, `measured_at=now()`. If you wrote two rows in a run, the rolling 30d aggregator double-counts. If the sample tier='insufficient', still write a row — with the n-counts at 0 and `insufficient_sample=true`.
4. **`tier='full'` gates per-run rate flags.** Only when the SQL kernel returns `tier='full'` are you allowed to raise miss_rate / pass_through_rate / timing_miss operator_flags. `tier='preview'` and `tier='insufficient'` write the row but skip per-run flags. The rolling 30d aggregator is independent — it can fire across preview runs once it accumulates `rolling_n >= 8`.
5. **Verdict matrix is canonical.** Use the 11-row table verbatim. Anything outside the matrix → log as `unclassified` in the evidence jsonb and don't count toward miss/save/pass_through. Drift in the matrix breaks v2/v3 parity diffs.
6. **No Tier-1 escalations from this skill.** Even if a replay produces a striking new verdict, do not enqueue an `orchestrator_runs` row. The skill is observational. Operator can dispatch via dashboard "refresh" if a finding warrants action.
7. **Costs are Cowork-subsidized.** Don't worry about per-run API cost ceilings — the orchestrator's PER_RUN_HARD_KILL_USD doesn't apply here. Cost reporting is best-effort.

## Run — step by step

### 1. Sample via the SQL kernel

```sql
SELECT * FROM public.v3_challenger_retro_sql_kernel(90);
```

Returns up to 10 rows with columns:
- `assessment_id` (uuid)
- `asset_id` (uuid)
- `stratum` (text: pre_edge_hit | dead_catalyst | post_edge_miss | wildcard)
- `predicted_outcome` (text)
- `predicted_conviction_pct` (numeric)
- `predicted_direction` (text)
- `realized_outcome` (jsonb — has `label` key plus shape-specific fields)
- `pre_mortem_verdict` (text: all_survive | partial | all_falsified | skipped)
- `hypothesis_count` (int — sanity; >0 expected)
- `tier` (text: full | preview | insufficient — same value on every row)

If `tier='insufficient'` AND no rows returned: write the zero-counts `accuracy_metrics` row (see step 5 below) and exit. Skip the Claude work entirely.

If `tier='insufficient'` BUT rows returned (universe scrapes together a partial sample): still skip Claude, write the zero-counts row with `insufficient_sample=true`, and note `tier='insufficient'` in the evidence jsonb.

### 2. Per-row: fetch frozen Stage 2 + Stage 3 context

For each sampled `assessment_id`, read:

```sql
-- 2a. Hypothesis enumeration rows (Stage 2 frozen output).
SELECT hypothesis_id, label, claim, mechanism, direction,
       supporting_fact_ids, contradicting_fact_ids,
       kill_conditions, deliver_conditions, prior_estimate_pct
  FROM public.hypothesis_enumeration
 WHERE assessment_id = $1
 ORDER BY hypothesis_id;

-- 2b. Existing premortem verdicts (original Stage 3 output for diff).
SELECT hypothesis_id, verdict, failure_modes,
       disconfirming_searches, update_triggers, is_declined
  FROM public.premortem_assessments
 WHERE assessment_id = $1
 ORDER BY hypothesis_id;

-- 2c. Original convergence_assessment context.
SELECT thesis_summary, thesis_direction, conviction_pct,
       evidence_quality, band, reasoning_trace
  FROM public.convergence_assessments
 WHERE id = $1;
```

If `hypothesis_count=0` (no Stage 2 output — old or failed assessment), skip the row with `unclassified` reason `'no_hypotheses'`. Don't count against miss/save.

### 3. Replay Stage 3 (pre-mortem only)

Build the Stage 3 prompt locally using the SAME `STAGE_3_SYSTEM` from [orchestrator_runtime/premortem.py:79](orchestrator_runtime/premortem.py). The user content is the serialized Stage 2 hypotheses (see `_serialize_hypothesis` in the same file). Single Sonnet call per assessment, no caching needed (this is a replay; cache prefix wouldn't match the freshness gate anyway).

Parse the response per `_validate_and_parse_verdicts` semantics: extract `verdicts[]` with per-hypothesis `verdict` AND `challenger_verdict` AND `failure_modes`, plus overall_verdict (rollup).

### 4. Classify via the 11-row matrix

Per sampled row, derive `new_verdict_rollup` from the replayed `verdicts[]`. Pick the most-extreme verdict across hypotheses (precedence: falsified > weakened > survives). Map to v2 challenger verbs:
- `survives` → `confirm`
- `weakened` → `challenge`
- `falsified` → `kill`
- any hypothesis with `is_declined=true` → `decline` (sparingly used)

Then classify against `stratum` (which encodes the realized outcome label):

| Realized stratum | New verdict | Classification     |
|------------------|-------------|--------------------|
| pre_edge_hit     | confirm     | calibrated_hit     |
| pre_edge_hit     | challenge   | ambiguous_hit      |
| pre_edge_hit     | kill        | **miss**           |
| pre_edge_hit     | decline     | over_decline       |
| dead_catalyst    | kill        | save               |
| dead_catalyst    | challenge   | partial_save       |
| dead_catalyst    | confirm     | **pass_through**   |
| dead_catalyst    | decline     | early_save         |
| post_edge_miss   | kill        | timing_catch       |
| post_edge_miss   | challenge   | timing_catch       |
| post_edge_miss   | confirm     | **timing_miss**    |
| post_edge_miss   | decline     | timing_save        |

Wildcard stratum rows: classify as `unclassified` with the new_verdict logged. Don't count toward miss/save/timing rates; they still appear in evidence for trend analysis.

### 5. Persist one `accuracy_metrics` row

```sql
INSERT INTO public.accuracy_metrics (
  measured_at, window_days, auditor,
  sample_n, labeled_n, insufficient_sample, sampled_total,
  calibrated_hit_n, ambiguous_hit_n, miss_n,
  save_n, partial_save_n, pass_through_n,
  timing_catch_n, timing_miss_n,
  decline_n, over_decline_n, early_save_n, timing_save_n,
  miss_rate, pass_through_rate, save_rate, calibrated_hit_rate,
  evidence
)
VALUES (
  now(), 90, 'challenger_retro',
  $sample_total, $labeled_n, $insufficient, $sampled_total,
  $calibrated_hit_n, $ambiguous_hit_n, $miss_n,
  $save_n, $partial_save_n, $pass_through_n,
  $timing_catch_n, $timing_miss_n,
  $decline_n, $over_decline_n, $early_save_n, $timing_save_n,
  $miss_rate, $pass_through_rate, $save_rate, $calibrated_hit_rate,
  $evidence_jsonb
);
```

Rates (nullable on insufficient_sample=true):
- `miss_rate          = miss_n            / pre_edge_hit_sampled` (NULL if denominator=0)
- `pass_through_rate  = pass_through_n    / dead_catalyst_sampled`
- `save_rate          = save_n            / dead_catalyst_sampled`
- `calibrated_hit_rate= calibrated_hit_n  / pre_edge_hit_sampled`

The `evidence` jsonb holds the full audit trail per sample:

```json
{
  "tier": "full",
  "samples": [
    {
      "assessment_id": "<uuid>",
      "asset_id": "<uuid>",
      "stratum": "pre_edge_hit",
      "new_verdict": "confirm",
      "new_challenger_verdict": "confirm",
      "original_verdict": "all_survive",
      "classification": "calibrated_hit",
      "predicted_conviction_pct": 72.5,
      "realized_outcome_label": "pre_edge_hit",
      "replay_cost_usd": 0.31,
      "replay_latency_ms": 12000
    },
    ...
  ]
}
```

### 6. Per-run rate flags (tier='full' only)

If the SQL kernel returned `tier='full'` AND the per-rate denominators are non-zero:

```sql
-- Miss-rate flag (warn @ 0.10, critical @ 0.25)
INSERT INTO public.operator_flags
  (source, kind, severity, target_type, target_id, payload, created_at)
VALUES (
  'challenger_retro',
  'challenger_retro_miss',
  CASE WHEN $miss_rate >= 0.25 THEN 'critical' ELSE 'warn' END,
  'accuracy_metrics', $accuracy_metrics_id,
  jsonb_build_object('miss_rate', $miss_rate, 'miss_n', $miss_n,
                     'pre_edge_hit_sampled', $pre_edge_hit_sampled),
  now()
)
WHERE $miss_rate >= 0.10
ON CONFLICT (source, kind, target_type, target_id) DO UPDATE SET
  payload  = EXCLUDED.payload,
  severity = EXCLUDED.severity;
```

Same shape for:
- `challenger_retro_pass_through` (warn @ pass_through_rate >= 0.25)
- `challenger_retro_timing_blindspot` (warn @ timing_miss_n >= 2 AND post_edge_miss_sampled >= 3)

`source='challenger_retro'` will need to be added to the operator_flags.source CHECK once this skill goes live — the canonical list (post-migration 20260524000050) does NOT yet include it. Until then, raise the warning via stdout instead of failing the insert. The CHECK extension is a one-line additive migration; pair it with the M6 pg_cron schedule.

### 7. Rolling 30-day aggregator

Independent of the per-run flags. Pull the four prior `accuracy_metrics` rows for `auditor='challenger_retro'` within 30 days:

```sql
SELECT
  sum(miss_n)            AS rolling_miss_n,
  sum(calibrated_hit_n + ambiguous_hit_n + miss_n + over_decline_n) AS rolling_pre_edge_hit_n
FROM public.accuracy_metrics
WHERE auditor='challenger_retro'
  AND measured_at >= now() - interval '30 days';
```

`rolling_miss_rate = rolling_miss_n / rolling_pre_edge_hit_n` (NULL on zero). If `rolling_miss_rate >= 0.10` AND `rolling_pre_edge_hit_n >= 8`, raise:
```
operator_flags(source='challenger_retro',
               kind='challenger_retro_rolling_miss',
               severity='warn',
               payload={rolling_miss_rate, rolling_pre_edge_hit_n})
```

### 8. Run report

```
fda_challenger_replay @ 2026-05-17 09:04 UTC
  Tier: full
  Sampled: 10 (3 pre_edge_hit + 3 dead_catalyst + 2 post_edge_miss + 2 wildcard)
  Universe: 12 pre_edge_hit / 8 dead_catalyst / 5 post_edge_miss
  Classifications:
    calibrated_hit: 2   ambiguous_hit: 0   miss: 1
    save: 2             partial_save: 1    pass_through: 0
    timing_catch: 1     timing_miss: 1
    unclassified (wildcard): 2
  Rates: miss=33.3% pass_through=0.0% calibrated_hit=66.7% save=66.7%
  Cost: $3.12 (mean $0.31/replay over 10 replays)
  Flags raised:
    - challenger_retro_miss (warn) @ miss_rate=33.3%
  Rolling 30d: rolling_miss_rate=18.2% over rolling_n=11 — flag fires (warn)
```

Run report goes to stdout AND markdown at `tasks/fda_challenger_replay_runs/YYYY-WW.md`.

## Edge cases

- **Zero samples returned (universe empty)**: write a zero-counts row, `tier='insufficient'`, `insufficient_sample=true`. Skip all Claude work.
- **Sample but no hypothesis_enumeration rows** (very old assessment, pre-Stage-2): log as `unclassified` reason='no_hypotheses', don't replay.
- **Replay JSON parse failure**: treat as `unclassified` reason='parse_failure'. Don't crash the run; the matrix bucket just gets 0 on that row.
- **operator_flags source CHECK doesn't yet include `challenger_retro`**: surface as stdout warning until the CHECK extension lands.
- **Cost overrun**: there is no hard ceiling on Cowork; if the 10 replays cost $50 instead of $3, log it but don't fail.

## Cross-references

- v2 origin: `challenger_retro.md` (same author, same matrix, different inputs — v2 candidates → v3 convergence_assessments).
- SQL kernel: `public.v3_challenger_retro_sql_kernel(int)` defined in migration 20260524000040. Returns the 10-row sample + tier flag in one call.
- Stage 3 source: replays the prompt at [orchestrator_runtime/premortem.py](orchestrator_runtime/premortem.py) (STAGE_3_SYSTEM constant). DO NOT fork — reuse the canonical prompt so drift is detectable.
- Writer target: `accuracy_metrics` (migration 20260425000000) — auditor='challenger_retro' column carries this skill's rows.
- Peer skill: `fda_aging_review` (daily 06:00 UTC) shares Pedro's Cowork seat. Both fit within Cowork's effective daily Claude budget; the weekly cadence here is the headroom for the daily peer.
