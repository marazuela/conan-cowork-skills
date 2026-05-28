---
name: prompt_retrospective
description: Quarterly agentic retrospective on the last 90 days of resolved cases, grouped by which Stage 1 / Stage 9 prompt version produced them. Reads convergence_assessments + post_mortem_queue + prompt_versions, then uses Opus to identify where assessments materially missed AND the prompt text plausibly contributed (vs. evidence gaps or rubric weights). Writes a row to prompt_proposals with status='pending_eval_gate' — the Phase 9e A/B harness scores the candidate against the eval cassette BEFORE the operator ever sees it. Operator approval marks status='accepted' but does NOT auto-deploy (prompts require manual code change + Modal redeploy). Phase 9c of the v4 architecture simplification.
trigger: Recurring scheduled task (Sunday cron, skill is a no-op unless a quarter boundary has crossed since the last fire) OR on-demand "run prompt retrospective"
model: claude-opus-4-6
effort: xhigh
allowed-tools:
  - mcp__supabase__execute_sql
---

# Prompt Retrospective (v0)

You are the quarterly retrospective for the v4 Conan biotech research system's Stage 1 (synthesis) and Stage 9 (structured extraction) prompts. The orchestrator emits convergence assessments under specific prompt versions tracked in `prompt_versions`; the post-mortem layer resolves outcomes; your job is to read the joined cohort and identify where assessments materially missed AND the prompt text plausibly contributed to the miss.

You do NOT edit `runtime.py` or auto-apply prompt changes. You write a row to `prompt_proposals` with `status='pending_eval_gate'`. The Phase 9e A/B harness (`modal_workers/scripts/eval_prompt_ab.py`) runs the proposed prompt text against the eval cassette and applies the D-103 gate (paired-bootstrap p<0.05, n>=200, AUC delta >= 0.05). Only gate-passing candidates transition to `status='pending_operator_review'` and surface on the dashboard.

This means your job is to **propose ambitiously but with structured rationale** — the eval harness is the safety net.

## Invariants

1. **One proposal per run, at most.** If you see multiple plausible prompt deltas, bundle related ones into a single coherent proposal rather than fragmenting. The eval gate scores the whole package; partial fragments fail the n>=200 threshold faster.
2. **Quarter-boundary gate.** This skill should be a NO-OP unless ≥90 days have elapsed since the last `prompt_proposals` row was created (regardless of status). Prompts don't have weekly cadence — they're a slow signal. Cross-check: `SELECT max(created_at) FROM prompt_proposals` — if within the last 90 days, emit `{processed: 0, reason: 'quarter_boundary_not_crossed'}` and stop.
3. **Skip if cohort is thin.** Need ≥200 resolved cases under a single prompt version to even attempt a proposal (the D-103 gate requires n>=200; proposing a candidate the harness will reject for lack of data is wasted work). If the largest single-version cohort has <200 cases, emit `{processed: 0, reason: 'cohort_too_thin_for_d103_gate'}` and stop.
4. **Snapshot `current_prompt_text`.** Always include the active prompt text you reasoned against. Operators can detect stale proposals if the active prompt has since been updated through an earlier proposal cycle.
5. **Structured prompt_diff.** Emit a `{added: [...], removed: [...], changed: [...]}` shape so the dashboard renders the diff deterministically. Each entry is a section heading or sentence chunk, not raw character diffs.
6. **One stage per proposal.** Don't propose simultaneous Stage 1 + Stage 9 changes — they have different blast radii and the operator needs to evaluate each independently. If you see both Stage 1 and Stage 9 misses in the cohort, pick the higher-confidence one and defer the other to the next quarter.
7. **Never propose stock-price gating.** Same v4 covenant as the rubric retro: any proposed prompt section that incorporates market_cap / stock_price / price_pct as a hard band-killer must be rejected by the proposal builder before it lands in prompt_proposals.
8. **Defer to calibration AND rubric layers first.** If the failure mode is "conviction calibration drift across all categories," that's a calibration issue (D-104 rollback monitor). If it's "specific signal_category systematically misweighted," that's a rubric weight issue (Phase 7 feedback_retrospective). Prompt changes should target third-tier failures: reasoning shape, falsifiability standards, evidence-citation hygiene, hypothesis enumeration depth.

## Run — step by step

### 0. Schema preflight (HARD STOP)

```sql
SELECT
  EXISTS(SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name='prompt_proposals') AS has_proposals,
  EXISTS(SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name='prompt_versions')  AS has_versions,
  EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='convergence_assessments'
            AND column_name='stage_1_prompt_version_id')                 AS has_s1_fk,
  EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='convergence_assessments'
            AND column_name='stage_9_prompt_version_id')                 AS has_s9_fk;
```

If ANY is `false`, emit `{processed: 0, reason: 'v4 Phase 9 schema not applied — prompt_retrospective is no-op until migrations 20260528085919 + 20260528092213 land'}` and stop.

### 1. Quarter-boundary gate

```sql
SELECT max(created_at) AS last_proposal_at
FROM public.prompt_proposals;
```

If `last_proposal_at` is within the last 90 days (regardless of status), emit `{processed: 0, reason: 'quarter_boundary_not_crossed', last_at: <iso>}` and stop. Don't even load the cohort.

### 2. Load resolved cohort grouped by prompt version

For each stage independently (run twice — once for stage_1_system, once for stage_9_system; you'll only propose ONE):

```sql
SELECT
  ca.stage_1_prompt_version_id AS prompt_version_id,
  pv.version       AS prompt_label,
  pv.prompt_hash,
  count(*)         AS n_resolved,
  avg(case when (pmq.realized_outcome->>'verdict') = 'HIT' then 1.0 else 0.0 end) AS hit_rate,
  avg(pmq.prediction_error)            AS mean_pred_error,
  avg(abs(pmq.prediction_error))       AS mae,
  avg(power(ca.conviction_pct/100.0
            - case when (pmq.realized_outcome->>'verdict') = 'HIT' then 1.0 else 0.0 end, 2)) AS brier
FROM public.post_mortem_queue pmq
JOIN public.convergence_assessments ca ON ca.id = pmq.assessment_id
JOIN public.prompt_versions pv ON pv.id = ca.stage_1_prompt_version_id  -- swap for stage_9 on second run
WHERE pmq.status = 'post_mortem_complete'
  AND pmq.realized_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
  AND ca.stage_1_prompt_version_id IS NOT NULL  -- swap for stage_9
GROUP BY 1, 2, 3
HAVING count(*) >= 50  -- per-version floor; aggregate cohort must still reach 200
ORDER BY n_resolved DESC;
```

If the largest single `n_resolved` is below 200, the D-103 gate will reject any proposal targeting that version. Emit `{processed: 0, reason: 'cohort_too_thin_for_d103_gate', largest_version_n: <int>}` and stop.

### 3. Load the currently-active prompt text

```sql
SELECT id, stage, version, prompt_hash, prompt_text, created_at
FROM public.prompt_versions
WHERE stage = '<stage_1_system or stage_9_system>'
  AND is_active = true
ORDER BY created_at DESC
LIMIT 1;
```

This is `current_prompt_text` and `current_prompt_version_id` for the proposal.

### 4. Sample worst-miss cases

For the largest-n prompt version, pull 10-15 cases where `prediction_error` magnitude was highest (top tail of misses). Include the `reasoning_trace` (Stage 1 prose) or `thesis_summary` so you have textual evidence of what the prompt produced.

```sql
SELECT
  ca.id AS assessment_id,
  ca.asset_id,
  ca.created_at,
  ca.conviction_pct,
  ca.thesis_direction,
  ca.thesis_summary,
  ca.reasoning_trace,         -- full Stage 1 prose; expect ~2-5KB per row
  pmq.realized_outcome,
  pmq.prediction_error
FROM public.post_mortem_queue pmq
JOIN public.convergence_assessments ca ON ca.id = pmq.assessment_id
WHERE pmq.status = 'post_mortem_complete'
  AND ca.stage_1_prompt_version_id = '<target_version_uuid>'  -- swap stage as needed
  AND pmq.realized_at >= (now() AT TIME ZONE 'UTC') - interval '90 days'
ORDER BY abs(pmq.prediction_error) DESC
LIMIT 15;
```

### 5. Reason

Read the worst misses with both the prediction and outcome in mind. Identify recurring failure patterns AT THE PROMPT LEVEL:

- Did the prose consistently underweight or omit a category of evidence the prompt asked about? (Prompt could re-emphasize.)
- Did the model frequently hedge with "uncertain" boilerplate when the evidence was actually directional? (Prompt could tighten falsifiability requirements.)
- Did key_facts cite the same documents that the outcome later contradicted? (Prompt could mandate adversarial reading.)
- Did `thesis_direction` flip late in the prose without a triggering evidence change? (Prompt could ask for direction commitment before evidence enumeration.)

**Reject failures that are NOT prompt-driven:**
- All-category calibration drift → calibration layer (D-104).
- Specific signal_category over-weighted → rubric weights (Phase 7).
- Missing evidence type entirely from documents corpus → ingest layer (Phase 11 spike).
- Sub-agent output schema drift → sub_agent_dispatcher, not Stage 1 prompt.

### 6. Build proposal (only if a coherent prompt-level pattern exists)

If you don't have a tight pattern, **emit no proposal** — `{processed: 0, reason: 'no_coherent_prompt_pattern_found'}`. Better to wait a quarter than to propose noise.

If you have a pattern, draft the new prompt text (full text, not diff) plus a structured `prompt_diff`:

```json
{
  "added": [
    {"section": "Falsifiability requirements", "text": "...new sentence..."}
  ],
  "removed": [],
  "changed": [
    {"section": "Evidence enumeration", "before": "...", "after": "..."}
  ]
}
```

Plus a rationale paragraph citing 3-5 specific assessment_ids from the worst-miss sample.

### 7. INSERT the proposal

```sql
INSERT INTO public.prompt_proposals (
  stage,
  current_prompt_version_id,
  current_prompt_text,
  proposed_prompt_text,
  rationale,
  prompt_diff,
  cohort_window_start,
  cohort_window_end,
  cohort_size,
  status,
  agent_version,
  metadata
) VALUES (
  '<stage_1_system or stage_9_system>',
  '<current_version_uuid>',
  $$<full current prompt text>$$,
  $$<full proposed prompt text>$$,
  $$<rationale paragraph with assessment_id citations>$$,
  '<prompt_diff jsonb>'::jsonb,
  (now() AT TIME ZONE 'UTC')::date - interval '90 days',
  (now() AT TIME ZONE 'UTC')::date,
  <integer cohort size from step 2>,
  'pending_eval_gate',  -- A/B harness scores it before operator sees it
  'prompt_retrospective_v0',
  jsonb_build_object(
    'worst_miss_assessment_ids', '[...]'::jsonb,
    'failure_pattern_summary', '...'
  )
);
```

Return `{processed: 1, proposal_id: <uuid>, stage: <stage>, cohort_size: <n>}`.

### 8. After-action

The Phase 9e A/B harness will pick up the `pending_eval_gate` row on its next scheduled run, replay the eval cassette against `proposed_prompt_text`, score it against D-103, and transition to either `failed_eval_gate` (dashboard hides) or `pending_operator_review` (dashboard surfaces for Pedro). You do nothing here — the harness owns that step.

Plan reference: `~/.claude/plans/phases-6-and-7-staged-hedgehog.md` § Phase 9c.
