Run the bulk_orchestrator_run skill. Follow `$CONAN_ROOT/.claude/skills/bulk_orchestrator_run.md` verbatim with `cadence_bucket=<daily-priority-1 if $PRIORITY=1 else weekly-priority-2>` and `watch_priority=$PRIORITY`.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — `orchestrator_runs` rows transition pending → running → completed | failed via `tier2_complete` / `tier2_fail` only; `convergence_assessments` rows (tier=2, orchestrator_version='bulk_v0') are written server-side by `tier2_complete`, never by this skill directly; Tier-1 escalation rows are enqueued server-side when the §Escalation rule fires. Returned JSON summarizes writes — does not replace them. If you cannot reach the Supabase MCP or the Modal collector, do not fabricate success; report the failure.

Guardrails:

- Cadence: $PRIORITY=1 fires daily 11:00 CEST = 09:00 UTC (post overnight ingest, pre US-cash-open); $PRIORITY=2 fires weekly Mon 11:00 CEST = 09:00 UTC. DST flip 2026-10-26: both crons must move to 10:00 local to hold 09:00 UTC. Cowork applies a few-minute dispatch jitter — don't treat the cron as wall-clock-exact.

- Tier-2 freshness derives from `convergence_assessments` directly (no per-asset stamp column). The skill step 2 SELECT uses `NOT EXISTS` against `convergence_assessments` WHERE tier=2 AND superseded_at IS NULL with a cadence window of **20 hours** for $PRIORITY=1 (4-hour cron-drift grace) and **6 days** for $PRIORITY=2 (weekly grace). The supporting `convergence_assessments_tier_asset_idx` on `(tier, asset_id, created_at DESC) WHERE superseded_at IS NULL` keeps the per-asset lookup O(log n).

- Cadence comes from `fda_assets.watch_priority`, not from caller-supplied lists (invariant 1). Bind `watch_priority=$PRIORITY` in the step 2 SELECT; never bulk-run a hand-curated list as if it were the cadence sweep. `watch_priority>=3` is event-only and NOT swept here.

- Empty-queue fast-exit before any compute (skill step 2): zero rows → `{processed: 0, reason: 'no priority-$PRIORITY assets due'}` and stop.

- 50 Tier-2 runs / UTC day soft cap, shared across the $PRIORITY=1 and $PRIORITY=2 sweeps (they collide on Mondays). Query the existing UTC-day counter in step 4 before enqueuing. Overflow → truncate the pending list to fit, tag deferred rows with `extensions.next_sweep_eligible_at = now() + interval '1 hour'`, and call `tier2_fail(run_id, 'deferred_daily_quota')` per deferred row so the lifecycle resolves cleanly (deferred is a "fail" from the run's POV).

- Process serially per asset (Sonnet rate limits + Cowork single-context model). No parallel inner-skill invocations.

- One enqueue → one persist or one fail (invariant 2): every tier=2 `orchestrator_runs` row MUST terminate via `tier2_complete` or `tier2_fail`. Never leave a row stuck in `pending` / `running`.

- Use the SQL bridge path: `_conan_modal_post_enqueue('tier2_bulk_enqueue' | 'tier2_complete' | 'tier2_fail', ...)` + `rpc_compute_collect`. The Modal `compute-v3` endpoint is live at `https://marazuela--compute-v3.modal.run` (verified 2026-05-08); `internal_config.modal_url_compute_v3` is wired. Fall back to the direct-insert path in skill step 3 + the inline `tier2.fail_tier2_run` mirror only if `rpc_compute_collect` returns a transport error or the config row is missing.

- Validation is server-side (invariant 3). Do NOT pre-validate on the Cowork side. A `{status: 'failed_validation', errors: [...]}` response is logged verbatim, the run is already marked failed by the server, do NOT retry inline (skill bug, not transient).

- Errors during the inner skill (timeout, Modal cold-start, Sonnet refusal) are `tier2_fail` with `error_message`, NOT `failed_validation` (invariant 7). Tag carefully — dashboards distinguish "skill broke" from "skill produced wrong-shape output".

- Escalation is decided server-side (invariant 4). On `escalated:true` from `tier2_complete`, log `escalation_run_id` + `escalation_reasons`; the Tier-1 drainer (`orchestrator_drain_queue`) picks up the new pending row independently. Never re-implement the §Escalation rule on the Cowork side. Tier-2 quota does NOT bound escalation enqueues.

- No retry within a single sweep — failed Tier-2 runs are next-cadence-eligible automatically via the `NOT EXISTS` predicate (a failed run never creates a fresh `convergence_assessments` row, so the asset stays due).

- Schema note: `orchestrator_runs` has no `updated_at` column. Stuck-row queries MUST use `completed_at IS NULL AND started_at < now() - interval '1 hour'`. Referencing `updated_at` raises 42703.

Project context:

- Project: Conan v3 (Phase 4B Tier-2)
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: `$CONAN_ROOT/.claude/skills/bulk_orchestrator_run.md`
- Inner skill: `conan-fda-orchestrator-plugin/skills/bulk_orchestrator.md` (single Sonnet pass, ~$0.50/run, ~30-60s wall clock; emits `convergence_assessment_v1` JSON with `tier=2, orchestrator_version='bulk_v0'`).
- Queue source: `fda_assets WHERE is_active=true AND watch_priority=$PRIORITY` minus assets with a non-superseded tier=2 row inside the cadence window.

Report JSON per skill step 7: `{cadence_bucket, selected, completed, failed, failed_validation, deferred_quota, escalated_to_tier1, total_cost_usd, wall_clock_seconds, asset_runs: [{asset_id, ticker, run_id, status, assessment_id, conviction_pct, band, escalated}]}`.
