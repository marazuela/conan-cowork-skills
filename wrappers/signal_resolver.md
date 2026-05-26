Run the signal_resolver skill. Follow $CONAN_ROOT/.claude/skills/signal_resolver.md steps 1–12 verbatim, including the step 3.5 no-profile pre-empt, the step 9.5 pre-filter, the step 11 challenger pass, and the thesis_writer §8f dispatch table on the inline-draft branch.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — signals UPDATE (dims + score + band + auto_caps + extensions) fires the reactor webhook; thesis_jobs status transitions; candidates upsert and candidate_events append on the inline-draft path; thesis_drafting_failures on DLQ; dossier markdown PUT to Storage bucket 'candidates/<YYYY>/<MM>/<ticker>_<signal_id>.md'. The returned JSON summarizes those writes — it does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'scoring' rows >30 min back to 'needs_scoring' first (skill step 1), appending gate_reasons += ['stuck_scoring_skill_reset'] so the sweeper is auditable. Then claim up to 5 queued rows ordered by created_at ASC.

- FDA-only scope (v2-teardown A.3). Filter the SELECT to signals whose scoring_profile IN ('binary_catalyst','fda_event'). The reactor hard-blocks non-FDA signals upstream, but this defends against pre-halt backlog burning the shared daily cap. Do NOT widen to the historical six profiles.

- Pre-empt no-rubric scanners at step 3.5. congressional_trading PTRs have no fitting profile pending D-014 reopen — persist sentinel score=0/band='archive' on the signal FIRST (statement 1), THEN terminal-transition the job with gate_reasons=['deferred_no_profile:<scanner>'] (statement 2). Order matters: the thesis_jobs_block_zombie_below_immediate trigger rejects the job UPDATE if signals.score is still NULL.

- Process one row at a time, serially — both gates on the inline-draft branch are stateful via drafted_thesis + all_drafts updates.

- Empty queue → report {processed: 0, empty_queue_exit: true} and exit fast. Fires 144×/day — DO NOT open WebSearch, the compute RPCs, the challenger, the syntactic gate, or Storage on an empty queue.

- 15 promotions / UTC day soft cap SHARED with the hourly thesis_writer run. Coordinate via the same counter on thesis_jobs.status='promoted' AND completed_at >= today, EXCLUDING rows whose gate_reasons contains 'routine_declined_flagged' (flagged-pass promotions are free). Dim resolution + below-immediate transitions are unmetered; only the inline-draft promotion consumes quota.

- Quota exhausted mid-run on an immediate-band rescore → transition the row to 'scoring_complete_below_immediate' with gate_reasons=['daily_quota_reached'] and stop drafting. Reactor will re-inspect tomorrow.

- Dim estimation discipline (skill step 5): default to 3 with honest citation-backed reasoning when research doesn't support a confident value. The gate accepts conservative-3; it rejects guessed high/low. _provenance='ai_resolved'. Mandatory ≥1 primary-source visit per signal — never tag 'insufficient_evidence' without one (the "no_research_in_resolver_run" anti-pattern produced 50+ zombies in the 2026-04-27 wave).

- Primary-source routing. SEC / EDGAR URLs (*.sec.gov) → rpc_edgar_fetch (WebFetch is 403'd by SEC fair-access). Litigation financial_materiality dim → rpc_market_snapshot for live USD mcap (entities.market_cap_usd is 100% NULL — has no writer). Both are split-call compute RPCs; see below.

- Split-call compute RPC discipline (migration 20260429020000). Every compute RPC — rpc_rescore_with_dims, rpc_assess_thesis, rpc_render_candidate_markdown, rpc_market_snapshot, rpc_edgar_fetch — is TWO separate execute_sql calls: (1) enqueue returns bigint request_id, (2) rpc_compute_collect(request_id, max_wait_ms) returns the jsonb result. NEVER collapse them into one statement — the old single-call pattern deadlocked 60s every time on a pg_net in-transaction visibility bug. Dollar-quote every JSON payload ($json$...$json$); the MCP has no bind-param support.

- On rescore RPC failure, classify the Postgres error (modal_5xx / modal_4xx / timeout / payload_invalid / unknown) and append gate_reasons += ['rescore_rpc_failure:<class>'] BEFORE leaving the row in status='scoring' for the sweeper. The split-call migration dropped the old one-shot 502/503/504 retry — every retry now flows through attempt_count + gate_reasons.

- rubric_engine is authoritative (skill step 6). Never hand-calculate score/band. The signals UPDATE (step 7) fires the reactor webhook — do NOT hand-compute convergence; reactor publishes band_with_bonus.

- Branch on band_with_bonus after reactor settles (poll ~3s):
  - watchlist / archive / discard → job status='scoring_complete_below_immediate' with gate_reasons=['resolved_watchlist' | 'resolved_archive' | 'resolved_discard']. Terminal. Loop.
  - immediate → step 9 (quota) → step 9.5 (pre-filter H1/H2/H3) → step 10 (inline draft) → step 11 (challenger + gate + promote/DLQ).

- Step 9.5 pre-filter on the inline-draft path. Run the same three structural pre-checks as thesis_writer §4.5 (H1 repeat-decline / H2 megacap-broad-class / H3 stale-catalyst). On a hit, skip the draft entirely: §8c-flagged dossier UPSERT with decline_verdict.declined_by='signal_resolver' and flag_extensions.declined_by='signal_resolver' (override thesis_writer's hard-coded 'thesis_writer'), then close the job status='promoted' with gate_reasons=['routine_declined_flagged','prefilter_H<n>']. Flagged-pass promotions don't consume quota.

- Inline-draft branch is the differentiator vs thesis_writer. signal_resolver promotes directly into candidates in the same pass when band_with_bonus='immediate' — it does NOT re-queue into status='queued' for thesis_writer to pick up. The "inline_draft_infra_unavailable_handoff" non-terminal token is the only exception (when assess_thesis_v2 is unreachable AFTER step 7); leave the row in status='queued' for thesis_writer, never write any of the forbidden zombie tokens.

- Honest-decline on the inline-draft branch (thesis_writer §6.5): confidence='low' OR insufficient_signal=true → route through §8c-flagged (promote-flagged, NOT DLQ). Skip BOTH gates and skip retry.

- Two gates on the inline-draft branch, BOTH authoritative (thesis_writer §6.8 + §7). Semantic gate (challenger routine) runs BEFORE the syntactic gate. Verdict routing per thesis_writer §8f:
  - confirm → proceed to syntactic gate.
  - challenge (1st) → amend once addressing challenger.strongest_counter + required_fixes. (2nd) → DLQ with final_reasons=['challenger_challenge_exhausted', ...].
  - kill → DLQ immediately, no retry, no syntactic gate, final_reasons=['challenger_kill', ...challenger.reasons]. Terminal.

- Two independent retry budgets on the same thesis_jobs row: attempt_count (max 2 drafts) and challenge_count (max 2 challenges). Increment challenge_count BEFORE invoking the challenger. Exceeding either → DLQ. Worst case 4 Claude calls per immediate-band job; happy path 2 (draft + confirm).

- First syntactic-gate-fail → one corrective retry with prior gate_reasons surfaced to the next draft. Second → DLQ.

- all_drafts shape: array of {draft, gate_verdict, challenge_verdict} triples, one per attempt. Written to candidate_events.payload.drafts on promotion AND to thesis_drafting_failures.all_drafts on DLQ.

- candidate_events.event_type discipline: 'created' on INSERT (xmax=0), 'thesis_drafted_by_claude' on UPDATE. Never 'thesis_updated' (in enum but NOT in fanout email-trigger set).

- Catalyst date → (date, window) pair per thesis_writer step 8a.2. Exactly one of (next_catalyst_date, next_catalyst_window) non-NULL per the candidates_catalyst_exactly_one CHECK.

- Reuse step-4 research in step 10 — do NOT re-search. Total research budget ≤6 across step 4 + step 10, matching thesis_writer.

- Forbidden gate_reasons (zombie-wave provenance, do NOT use or invent variants): *_no_research_in_resolver_run, *_low_fidelity_or_boilerplate_exhibit, *_keyword_false_positive, pending_human_review_*, wrong_fit_*, mna_keyword_hit_on_*, distress_keyword_*_no_ticker, profile_mismatch_*, immediate_band_inline_draft_unavailable, thesis_writer_should_pickup. If the signal looks like a poor fit, score honestly with all-3 dims; the rubric will archive it through step 7+8 with 'resolved_archive'.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/signal_resolver.md
- Queue source: thesis_jobs WHERE status='needs_scoring' AND signal_id maps to scoring_profile IN ('binary_catalyst','fda_event') — FDA-only post-teardown.
- Front-of-funnel entity normalization is handled separately by the signal_entity_resolver skill before signals land in needs_scoring; signal_resolver assumes entity_id is already resolved.
- Scanners feeding this queue (post-FDA-filter): edgar (8-K PDUFA / CRL / AdCom), fda_calendar, any scanner whose default_scoring_profile lands in the FDA-allowed set.

Report JSON: {processed, rescored_below_immediate, drafted_and_promoted, flagged_prefilter, flagged_honest_decline, dlq_syntactic, dlq_challenger_kill, dlq_challenger_challenge_exhausted, deferred_no_profile, rescore_rpc_failures, retried_and_passed, challenger_retried_and_passed, skipped_over_quota, inline_draft_infra_handoff, empty_queue_exit}.
