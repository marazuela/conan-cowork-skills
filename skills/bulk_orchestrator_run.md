---
name: bulk_orchestrator_run
description: Phase 4B Tier-2 (Cowork bulk) coordinator. Daily/weekly sweep of FDA assets per `fda_assets.watch_priority` cadence. For each due asset: pull the Tier-2 input blob from Modal, run the inner `bulk_orchestrator` skill (single Sonnet pass producing a convergence_assessment_v1 JSON), post the result back via `tier2_complete` so the runtime persists a tier=2 row, applies the §Escalation rule (high conviction / direction change / new primary doc), and enqueues a Tier-1 escalation when triggered. Runs under Pedro's Cowork-scheduled account, not Anthropic API. Runtime contract = D-128.
trigger: Recurring scheduled task — daily 09:00 UTC for `watch_priority=1` (post overnight ingest, pre US-cash-open), weekly Mon 09:00 UTC for `watch_priority=2`. `watch_priority>=3` are event-only and NOT swept here (Tier-1 picks them up via `new_doc` / `cross_source` triggers). Also on-demand "run bulk orchestrator sweep" / "run tier-2 sweep [asset_id]".
quota: 50 Tier-2 runs per UTC day (~$25 daily ceiling at $0.50/run target). Daily watch_priority=1 sweep should typically consume 10–20 of this; weekly priority=2 sweep folds in on Mondays and may push toward the cap. Skill defers lowest-priority assets to the next run when the cap is hit; Tier-1 escalations enqueued by this skill are NOT counted against the Tier-2 cap (they consume the Tier-1 hard-kill ceiling instead — D-125).
host: pedro
host_enrollment: Pedro's Cowork — two scheduled tasks (Cowork takes one cron per task). `conan-bulk-orchestrator-daily` (cron `0 11 * * *` = 11:00 CEST = 09:00 UTC) for the priority=1 sweep, `conan-bulk-orchestrator-weekly` (cron `0 11 * * 1`) for the Monday priority=2 sweep. Both fire ~simultaneously on Mondays and contend for the same 50/UTC day quota; deferral handles overflow. DST flip 2026-10-26: both crons must change to `0 10 * * *` / `0 10 * * 1` to hold 09:00 UTC after CEST→CET. Cowork applies a deterministic jitter of several minutes to the cron string at dispatch time — actual fire was observed at 11:01 / 11:09 local, so don't take the cron as wall-clock-exact.
---

You are the Tier-2 (Cowork bulk) coordinator for the Conan v3 investment research system. The strategic pivot (D-100, 2026-05-06) made FDA + EDGAR depth the focus; Tier-2 is the **breadth lever** that lets us cover the full FDA asset watch list daily without paying Tier-1's full-pipeline ($10–15/run) cost. Every asset on the watch list deserves a fresh assessment within its cadence window; this skill is what makes that economical.

Your job is **scheduling + coordination**, not synthesis. The actual probabilistic reasoning lives in the inner `bulk_orchestrator` skill (single Sonnet pass, ~$0.50/run, ~30–60s wall clock). You drive the loop: select due assets → for each, dispatch to the inner skill via the runtime → record the result → escalate to Tier-1 when warranted.

## Invariants

1. **Cadence comes from `fda_assets.watch_priority`, not from caller-supplied lists.** The trigger frontmatter dictates which priority bucket fires; the SELECT in step 1 is the only authoritative source of "due" assets. Never bulk-run a hand-curated list as if it were the cadence sweep — that bypasses the priority-based prioritization the dashboard relies on.
2. **One enqueue → one persist or one fail.** Every `orchestrator_runs` row this skill creates (status='pending', tier=2) MUST terminate via either `tier2_complete` (status='completed' with `assessment_id`) or `tier2_fail` (status='failed' with `error_message`). Never leave a tier=2 row stuck in `pending` or `running` — that strands the queue and is a known SLA-sweep trigger (observability.thesis_jobs_sla_sweeper has a sibling for orchestrator_runs to flag this).
3. **The inner skill's output is the contract.** `tier2_complete` calls `validate_tier2_output` server-side; if validation fails, the call returns `{status: 'failed_validation', errors: [...]}` and the orchestrator_runs row is marked failed. Do NOT pre-validate on the Cowork side — duplicating the validator drifts. Surface validator errors verbatim into the run report.
4. **Escalation is decided server-side.** `tier2_complete` runs the §Escalation rule (`check_tier1_escalation` per `orchestrator_runtime/tier2.py`); never re-implement it on the Cowork side. If the response carries `escalated: true`, just log the escalation_run_id; the Tier-1 drainer (`orchestrator_drain_queue`) will pick it up.
5. **Tier-2 quota is a sweep-level ceiling, not per-asset gating.** Don't refuse to enqueue a single asset because the sweep is at, say, `40 / 50`. Continue until `50 / 50`, then defer the remainder to the next run with `extensions.next_sweep_eligible_at = now() + interval '1 hour'` on the deferred `orchestrator_runs` rows so a manual re-run doesn't immediately re-pick them. Daily and weekly cadences share the same quota counter (UTC day).
6. **No partial DB writes from this skill.** All persistence flows through `tier2_complete`; you never write to `convergence_assessments` directly. The same goes for `orchestrator_runs` updates — only `tier2_complete` and `tier2_fail` touch the lifecycle columns. You can READ freely (e.g. for the asset list select).
7. **Errors during the inner skill are NOT validation errors.** A timeout, a Modal cold-start failure, or a Sonnet-side refusal is a `tier2_fail` event with `error_message`. A Sonnet-produced JSON that fails the schema is `tier2_complete` with a payload that fails validation. Tag carefully — operator dashboards distinguish "skill broke" (fail) from "skill produced wrong-shape output" (failed_validation).
8. **Tier-1 escalations are cheap to enqueue, not cheap to run.** The §Escalation rule fires on high conviction / direction change / new primary doc. Enqueueing is one INSERT; the actual Tier-1 run is ~$15. The Tier-2 quota does NOT bound escalation enqueues — if every asset triggers escalation, every asset gets queued. That's the design. Tier-1's own daily ceiling (D-125 hard-kill) is what bounds wasted spend; this skill is not the right place to second-guess.

## Run — step by step

### 1. Pick today's cadence bucket

The trigger fires this skill with one of:
- daily-priority-1 (09:00 UTC, every day)
- weekly-priority-2 (09:00 UTC, Mondays only)
- on-demand-single (operator phrase: "run tier-2 sweep <asset_id>")
- on-demand-bucket (operator phrase: "run bulk orchestrator sweep")

For the on-demand-single path, skip step 2 entirely — go straight to step 3 with that single asset_id (still subject to the quota check in step 4).

For the on-demand-bucket path, default to priority=1 (matching the daily cadence). Operators can override via "run bulk orchestrator sweep priority=2".

### 2. Find work

Read live state via the Supabase MCP (`project_id=xvwvwbnxdsjpnealarkh`):

```sql
SELECT a.id AS asset_id, a.ticker, a.drug_name, a.indication,
       a.watch_priority,
       (SELECT max(created_at) FROM convergence_assessments
         WHERE asset_id = a.id AND tier = 2 AND superseded_at IS NULL
       ) AS latest_tier2_at
  FROM public.fda_assets a
 WHERE a.is_active = true
   AND a.watch_priority = $1            -- bound from cadence (1 or 2)
   AND NOT EXISTS (
         SELECT 1 FROM public.convergence_assessments ca
          WHERE ca.asset_id   = a.id
            AND ca.tier       = 2
            AND ca.superseded_at IS NULL
            AND ca.created_at >= now() - $2::interval
       )                                 -- '20 hours' (priority=1) | '6 days' (priority=2)
 ORDER BY a.watch_priority ASC,
          latest_tier2_at ASC NULLS FIRST,
          a.ticker ASC
 LIMIT 50;                              -- hard cap = quota; don't over-select
```

The `NOT EXISTS` clause prevents the daily sweep from re-running an asset that already got a fresh non-superseded tier=2 assessment within the cadence window — e.g. from a manual on-demand run earlier in the same UTC day. Use **20 hours** for priority=1 (gives 4-hour grace if the cron drifts) and **6 days** for priority=2. Freshness derives from `convergence_assessments` directly (no per-asset bookkeeping column); the supporting `convergence_assessments_tier_asset_idx` on `(tier, asset_id, created_at DESC) WHERE superseded_at IS NULL` makes the per-asset lookup O(log n).

If zero rows: emit `{processed: 0, reason: 'no priority-N assets due'}` and stop. Do not invoke any compute.

### 3. Enqueue + fetch input blobs

For the selected asset_ids, call the SQL wrapper `rpc_tier2_bulk_enqueue` — a `SECURITY DEFINER` thin shim that POSTs `{action: 'tier2_bulk_enqueue', args: {asset_ids: [...]}}` to the multiplexed `modal_url_compute_v3` endpoint and returns the pg_net `request_id`. Pair with `rpc_compute_collect` (D-122 split-call pattern):

```sql
-- Enqueue (separate execute_sql, single statement; pg_net needs the txn to commit)
SELECT public.rpc_tier2_bulk_enqueue(ARRAY['<id1>','<id2>',...]::text[]) AS request_id;

-- Collect (separate execute_sql)
SELECT public.rpc_compute_collect($1, 60000) AS result;  -- 60s; blob assembly is N×(asset+facts+docs+prior) reads
```

If `modal_url_compute_v3` itself is missing (would require a Modal redeploy regression — none observed since 2026-04-23) the wrapper raises with a precise message identifying the absent config key. On that error, fall back to the **direct-insert path**:

```sql
INSERT INTO public.orchestrator_runs (asset_id, trigger_type, tier, status, notes)
SELECT id, 'scheduled', 2, 'pending',
       jsonb_build_object('source', 'bulk_orchestrator_run.cowork')
  FROM unnest($1::uuid[]) AS u(id)
RETURNING id, asset_id;
```

Then build each input blob inline by reading `fda_assets`, `extracted_facts` (limit 200 by `extracted_at desc`), `asset_documents` (limit 50 by `created_at desc`), and the latest non-superseded `convergence_assessments` row per asset. Mirror `orchestrator_runtime.tier2.build_tier2_input_blob` exactly — same SELECT shapes, same limits.

The blob payload for the inner skill MUST match the schema in `conan-fda-orchestrator-plugin/skills/bulk_orchestrator.md` §Inputs verbatim.

### 4. Quota check

Count today's tier=2 attempts (any terminal status) via:

```sql
SELECT count(*) AS used_today
  FROM public.orchestrator_runs
 WHERE tier = 2
   AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
   AND status IN ('completed','failed');
```

> Schema note: `orchestrator_runs` has **no `updated_at` column**. Its
> lifecycle timestamps are `created_at`, `scheduled_at`, `started_at`,
> `completed_at`. Any ad-hoc freshness / "stuck run" query MUST use those
> (e.g. `completed_at IS NULL AND started_at < now() - interval '1 hour'`);
> referencing `updated_at` raises `42703` and aborts the query.

If `used_today + len(pending_assets) > 50`, truncate the pending list to fit, and tag the deferred assets via:

```sql
UPDATE public.orchestrator_runs
   SET notes = coalesce(notes,'{}'::jsonb)
       || jsonb_build_object('deferred_quota', true,
                             'next_sweep_eligible_at',
                             (now() + interval '1 hour')::text)
 WHERE id = ANY($1);
```

Then call `tier2_fail(run_id, 'deferred_daily_quota')` per deferred row so the lifecycle resolves cleanly. (A deferral is a "fail" from the run's POV — the operator sees an explicit reason instead of a stuck pending row.)

### 5. Run the inner `bulk_orchestrator` skill per asset

Process serially (NOT in parallel — Sonnet rate limits and Cowork's single-context model). For each `(run_id, asset_id, blob)`:

1. Invoke `bulk_orchestrator` (the inner skill at `conan-fda-orchestrator-plugin/skills/bulk_orchestrator.md`) with the blob as input.
2. The inner skill returns a `convergence_assessment_v1.json` payload with `tier=2, orchestrator_version='bulk_v0'`.
3. Capture wall-clock latency (start→end of step 1) and accumulated cost (Sonnet input/output token cost; the inner skill emits these).

**On Sonnet error / inner-skill exception** (timeout, refusal, infrastructure failure): call `rpc_tier2_fail` and skip to the next asset:

```sql
SELECT public.rpc_tier2_fail($run_id::uuid, $error::text) AS request_id;
SELECT public.rpc_compute_collect($1, 15000);
```

(Fall back to a direct UPDATE on `orchestrator_runs` if `modal_url_compute_v3` is unreachable; mirror `orchestrator_runtime.tier2.fail_tier2_run` — INCLUDE `tier=2` in the WHERE so a Tier-1 row can never be accidentally marked failed.)

**On inner-skill success**: continue to step 6.

### 6. Post the completed payload

```sql
SELECT public.rpc_tier2_complete(
  run_id     => $run_id::uuid,
  payload    => $payload::jsonb,
  cost_usd   => $cost_usd::numeric,
  latency_ms => $latency_ms::int
) AS request_id;
SELECT public.rpc_compute_collect($1, 60000);  -- includes prior fetch + persist + escalation enqueue
```

The collect returns one of:

| Response shape | Meaning | Action |
|---|---|---|
| `{status: 'completed', assessment_id, escalated: false, ...}` | Happy path. Tier-2 row persisted; no Tier-1 escalation. | Log `assessment_id` and the conviction band. Move on. |
| `{status: 'completed', assessment_id, escalated: true, escalation_run_id, escalation_reasons: [...]}` | Tier-2 row persisted AND a Tier-1 escalation enqueued. | Log all three; the Tier-1 drainer picks up the new pending row independently. |
| `{status: 'failed_validation', errors: [...]}` | The inner skill produced a malformed payload. The orchestrator_runs row is already marked failed by the server. | Log the errors, cite the asset_id + run_id in the run report, do NOT retry inline (this is a skill bug, not a transient fault). Move on. |
| pg_net transport error / non-200 from collector | Modal endpoint blew up. | Call `tier2_fail(run_id, '<error>')` to reconcile lifecycle, then move on. |

Do NOT retry within a single sweep — failed Tier-2 runs are next-cadence-eligible per the `NOT EXISTS` clause in step 2 (a failed run never creates a fresh `convergence_assessments` row, so the asset stays due automatically).

### 7. Bookkeeping

After the per-asset loop, emit the sweep summary. No per-asset stamp column needed — "last assessed" derives from `convergence_assessments` directly via the `NOT EXISTS` predicate in step 2. Completed runs leave a fresh non-superseded tier=2 row (so the asset is no longer due); failed / failed_validation runs leave no fresh row (so the asset stays due and re-attempts next cadence).

```json
{
  "cadence_bucket": "daily-priority-1",
  "selected": <int>,
  "completed": <int>,
  "failed": <int>,
  "failed_validation": <int>,
  "deferred_quota": <int>,
  "escalated_to_tier1": <int>,
  "total_cost_usd": <float>,
  "wall_clock_seconds": <int>,
  "asset_runs": [
    {"asset_id":"...","ticker":"...","run_id":"...","status":"completed",
     "assessment_id":"...","conviction_pct":52.0,"band":"watchlist",
     "escalated":false},
    ...
  ]
}
```

The summary is what Cowork reports back to the operator dashboard; structure it identically across runs so dashboards can rely on the field set.

## Known dependencies

Path 2 from the original plan shipped (single multiplexed `compute` FastAPI endpoint). The skill's primary RPC path uses these wrappers:

- `public.rpc_tier2_bulk_enqueue(asset_ids text[]) → bigint` — POSTs `{action: 'tier2_bulk_enqueue', args: {asset_ids}}` to `modal_url_compute_v3`.
- `public.rpc_tier2_complete(run_id, payload, cost_usd, latency_ms) → bigint` — same multiplex, `action='tier2_complete'`.
- `public.rpc_tier2_fail(run_id, error_message) → bigint` — same multiplex, `action='tier2_fail'`.

All three are `SECURITY DEFINER` SQL shims defined alongside the Modal `compute_v3` endpoint; they share the `modal_url_compute_v3` config key (no per-action URL rows in `internal_config`). The direct-insert fallback in §3 + the inline-mirror UPDATE in §5 remain as belt-and-suspenders if the Modal multiplex goes down.

## Reference

- Tier-2 contract + orchestration helpers: `orchestrator_runtime/tier2.py` (Python source of truth — every SQL fallback in this skill must mirror the Python function of the same name verbatim).
- Tier-2 schema: `convergence_assessments.tier int` column added by migration `20260512000000_v3_phase_4b_convergence_assessments_tier.sql`.
- Tier-2 LLM methodology: `conan-fda-orchestrator-plugin/skills/bulk_orchestrator.md` (the inner skill — what step 5 invokes per asset).
- Spec: DECISIONS.md D-128 (Phase 4B foundation).
- Tier-1 escalation drainer: `modal_workers/orchestrator_app.py::orchestrator_drain_queue` (filters `tier=eq.1`, picks up rows enqueued by `tier2_complete`'s escalation branch).
- Hard-kill cost ceiling per Tier-1 run: D-125 (`PER_RUN_HARD_KILL_USD` in `modal_workers/shared/cost_budget.py`).
