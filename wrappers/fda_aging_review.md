Run the fda_aging_review skill. Follow $CONAN_ROOT/.claude/skills/fda_aging_review.md steps 1–7 verbatim, including the direct Supabase `execute_sql` work-queue pull (step 1), the dual gate per asset (step 3 mechanical + step 4 Claude semantic challenger), the verdict-then-state-update ordering (step 5), and the consecutive_failures bookkeeping (step 6).

Outputs wired into the Conan v3 app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — `fda_aging_verdicts` INSERT (stage='b_claude_review' with recommendation, challenger_verdict, evidence arrays, consecutive_failures, trigger_rule); `fda_assets` UPDATE (is_active, aging_state, aging_state_since, last_aging_evaluated_at, aging_extensions per the recommendation matrix); `convergence_assessments` UPDATE (superseded_at on terminal verdicts kill/deliver); `fda_agent_reviews` INSERT on extractor-gap fallback (agent_kind='aging_review'); `operator_flags` UPSERT (source='aging_review', kind='aging_stuck') on consecutive_failures >= 3. Returned summary describes writes — does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- One run per UTC day. The step 1 work-queue SQL enforces the 10/UTC-day cap via `LIMIT (10 - today_count)`, where `today_count = count(*) from fda_aging_verdicts WHERE stage='b_claude_review' AND created_at >= today_utc`. If you receive an empty `assets` list, stop — do not pull from a fallback selection.

- Stage A is upstream truth (invariant 1). Only evaluate assets where `fda_assets.aging_state='kill_pending'` AND `last_aging_evaluated_at < today_utc`. Stage A is the only writer that sets `kill_pending`; the SQL function `v3_fda_aging_stage_a()` runs daily at 05:55 UTC via pg_cron. If you see an asset in `kill_pending` whose Stage A trigger_rule looks wrong, file an `operator_flags` row with kind='aging_stage_a_misclassification' and skip — do not silently override.

- Dual gate is non-negotiable (invariant 2). Every triggered claim (recommendation ∈ {kill, deliver}) passes BOTH (a) Gate 1 mechanical match (extracted_facts first, raw_text regex fallback) AND (b) Gate 2 Claude semantic challenger. Either gate fails → recommendation downgrades to maintain (or `flag_for_review` if Gate 1 hit via fallback only), AND consecutive_failures increments on the new verdict row.

- Gate 1 fallback to raw_text regex preserves v2 parity but flags an extractor gap. When the mechanical match comes from `documents.raw_text` and not `extracted_facts`, ALSO insert `fda_agent_reviews (agent_kind='aging_review', output={extractor_gap_detected: true, ...})` so the fact_extractor backlog has a paper trail. This does NOT block Gate 2.

- routine_declined is sticky (invariant 5). When BOTH `recommendation='kill'` AND `challenger_verdict='kill'`: set `fda_assets.aging_extensions->>'routine_declined'='true'`. This causes `v3_prior_failure_guard()` to block orchestrator dispatch for 24h. The flag clears on the next passing Stage B verdict (recommendation ∈ {promote_to_active, deliver, maintain} with challenger_verdict ∈ {confirm, challenge}).

- consecutive_failures = COALESCE(prior_latest, 0) + 1 when `recommendation='maintain'` AND `challenger_verdict IN ('challenge','kill')`. Reset to 0 on any clean run. At >=3, UPSERT operator_flags(source='aging_review', kind='aging_stuck', severity='warn', target_id=asset_id). This mirrors v2 candidate_aging_failures.consecutive_failures.

- Empty open hypotheses → recommendation='maintain', trigger_rule='no_open_hypotheses', challenger_verdict=NULL. Do NOT invoke Claude for an asset with no hypotheses to evaluate against; there's nothing to decide.

- No Modal calls at all. This is a Cowork-resident skill (~zero marginal API spend); the work-queue pull is a direct Supabase MCP `execute_sql` SELECT. All Claude work happens in your local context. Do NOT spawn Tier-1 runs from here — that's the orchestrator's job via separate triggers.

- Verdict-first ordering (invariant 3). `fda_aging_verdicts` INSERT must precede the `fda_assets` UPDATE and the `convergence_assessments` UPDATE. If a partial failure happens, the verdict row is the audit anchor — operator-recoverable from there.

- Stage 2 prompt-extension lag is graceful. Assessments produced before migration `20260524000010_v3_hypothesis_deliver_conditions` will have empty `deliver_conditions` arrays. Treat them as "kill-only evaluable" — Gate 1 walks kill_conditions only. Don't backfill deliver_conditions on old rows; let supersession refresh them.

- Output write order per asset (step 5): (1) INSERT fda_aging_verdicts, (2) UPDATE fda_assets state, (3) UPDATE convergence_assessments superseded_at on terminal verdicts. Wrap in a single SQL transaction batch via Supabase MCP.

Project context:

- Project: Conan v3
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/fda_aging_review.md
- Source rows: pulled by the step 1 `execute_sql` CTE — `fda_assets WHERE is_active=true AND aging_state='kill_pending' AND watch_priority IN (1,2) AND (last_aging_evaluated_at IS NULL OR last_aging_evaluated_at < today_utc)`, ordered by next_catalyst_date ASC NULLS FIRST, with `LIMIT GREATEST(0, 10 - today_count)`. No Modal bridge — the sister Modal action was deleted alongside the v4 Phase 6b tier2 actions; `aging_bulk_enqueue` was never built.
- Deterministic SQL companion: `v3_fda_aging_stage_a()` runs at 05:55 UTC via pg_cron job `v3-fda-aging-stage-a`. The 5-minute gap before this 06:00 UTC skill ensures Stage A writes are visible.
- Drain guard companion: `v3_prior_failure_guard(asset_id)` reads `fda_aging_verdicts` to block orchestrator dispatch when routine_declined is sticky-set on the asset.

Report JSON: {processed_total, killed, delivered, demoted_to_watch, maintained, gate1_fallbacks, gate2_challenge_downgrades, gate2_kill_downgrades, consecutive_failure_flags_raised, consecutive_failure_flags_resolved, extractor_gaps_flagged, no_hypotheses_skipped, quota_remaining}.
