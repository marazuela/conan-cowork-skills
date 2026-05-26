Run the challenger_retro skill. Follow $CONAN_ROOT/.claude/skills/challenger_retro.md steps 1–9 verbatim. Sample ≤10 historically outcome-labeled candidates, re-invoke thesis_challenger in drafting mode on each historical thesis (or read the historical decline_verdict for DLQ'd samples), classify verdicts on the 4-axis matrix against actual outcomes, write one accuracy_metrics row, and raise operator_flags on threshold breach.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — accuracy_metrics INSERT (exactly one row per run, auditor='challenger_retro', with decline_n / over_decline_n / early_save_n / timing_save_n populated); operator_flags UPSERT on per-run miss/pass_through/timing_blindspot breach AND rolling-30d miss breach AND per-prefilter-heuristic precision drift; operator_flags auto-resolve (PATCH resolved_at) on subsequent runs where the condition clears. Read-only against candidates / outcomes / candidate_events / thesis_jobs / thesis_drafting_failures / signals (invariant 1). If the Supabase MCP is unreachable, do not fabricate success; report the failure.

Guardrails:

- Bounded quota (invariant 4): max 10 thesis_challenger invocations per run. Stratified sampling (invariant 3): 3 pre_edge_hit + 3 dead_catalyst + 2 post_edge_miss + 2 any remaining labeled rows; redistribute slots on bucket underflow; total cap = 10 regardless of surplus. DLQ'd samples with a historical decline_verdict do NOT consume a challenger call — the verdict is read from thesis_drafting_failures.all_drafts[-1].decline_verdict.

- Fresh context per challenger invocation (invariant 2). Each call is a separate routine invocation. No shared prior between samples, no shared prior with the original drafting session.

- Drafting mode only (invariant 6). Invoke thesis_challenger with mode:"drafting" on every live sample. The aging-mode retro is a v2 extension not covered here.

- Window: outcomes.created_at >= now() - interval '90 days' AND outcome_label IN ('pre_edge_hit','dead_catalyst','post_edge_miss'). Other outcome_label values excluded from rate metrics.

- 4-axis classification matrix (step 5). Decline verdicts come from the drafter's §6.5 self-decline + §4.5 fast-prefilter (H1/H2/H3); the challenger was NOT invoked on those, so the historical decline_verdict IS the verdict:
  - pre_edge_hit × {confirm=calibrated_hit ✓ | challenge=ambiguous_hit | kill=MISS ✗ | decline=OVER_DECLINE ✗ (tracked separately, not folded into miss_rate)}
  - dead_catalyst × {kill=save ✓ | challenge=partial_save | confirm=PASS_THROUGH ✗ | decline=early_save ✓ (drafter caught it without burning a challenger call)}
  - post_edge_miss × {kill|challenge=timing_catch ✓ | confirm=TIMING_MISS ✗ | decline=timing_save ✓}
  Legacy DLQ rows with prose `routine_declined: …` and no decline_verdict object are excluded from the decline tally.

- Two-tier sample gating (invariant 5). tier='full' iff pre_edge_hit_sampled ≥ 5 AND dead_catalyst_sampled ≥ 5; tier='preview' iff hit ≥ 3 OR catalyst ≥ 3; else tier='insufficient'. Per-run rate flags require tier='full'. Rolling-30d miss flag is independent and fires once cumulative depth supports it, counting preview runs.

- Per-run flag thresholds (step 8): challenger_retro_miss warn ≥ 0.10 / critical ≥ 0.25; challenger_retro_pass_through warn ≥ 0.25; challenger_retro_timing_blindspot warn at timing_miss_n ≥ 2 (with post_edge_miss_sampled ≥ 3). All require tier='full'.

- Rolling 30d aggregator (step 6.5): SUM(calibrated_hit_n + miss_n) over the last 4 challenger_retro rows with insufficient_sample=false. challenger_retro_rolling_miss warn at rolling_n ≥ 8 AND rolling_miss_rate ≥ 0.10. Closes the early-life gap when no single Sunday hits tier='full'.

- Per-prefilter-heuristic precision tracking. thesis_writer §4.5 records which check fired (H1 repeat-decline, H2 megacap-on-broad-class, H3 stale-catalyst) in extensions.decline_verdict.prefilter_check, with gate_reasons including 'prefilter_H1'|'prefilter_H2'|'prefilter_H3'. Group over_decline samples by prefilter_check; per spec, if any heuristic's precision drops below 90% over the 30d window, raise challenger_retro_prefilter_drift (severity=warn, evidence=per-H breakdown) so Pedro can retune. Capture the breakdown in evidence_jsonb.prefilter_precision = {H1:{n,over_decline_n}, H2:{...}, H3:{...}}.

- caller_spec_sha drift detection. thesis_writer (§4.5/§challenger invocation) stamps caller_spec_sha (sha256 of thesis_challenger.md as observed locally at draft time) on every historical verdict and prefilter stub. Compute the current sha256 of $CONAN_ROOT/.claude/skills/thesis_challenger.md once per run; for each sample, compare historical vs current. Stash {historical_sha, current_sha, drifted:bool} on each evidence_jsonb sample record so Pedro can tell whether a miss/pass_through reflects today's challenger behavior or a since-edited prior version. Drift alone does not raise a flag; it is annotation.

- One accuracy_metrics row per run (invariant 7), always. Empty-sample runs write one row with insufficient_sample=true, sample_n=0, rates NULL. Preserves the time series.

- Auto-resolve open flags when the condition clears on a subsequent run. PATCH resolved_at = now(), resolved_note = 'auto-resolved: rate recovered'. Applies to per-run, rolling, and prefilter_drift flags.

- Read-only against live state (invariant 1). Never UPDATE candidates, outcomes, thesis_jobs, thesis_drafting_failures, candidate_events, signals. Only accuracy_metrics + operator_flags writes.

- Live-sample payload (step 4): {mode:"drafting", draft:<historical thesis from candidate_events.payload.thesis>, signal:<signals row>, entity:<entity row>, scanner:<scanner row>, filing_text:<≤32KB of cited text or empty>}. Capture verdict JSON verbatim.

- evidence_jsonb (step 7) is an object: {tier:'preview'|'full'|'insufficient', samples:[{candidate_id, ticker, mic, outcome_label, source:'challenger'|'decline_verdict', verdict, reasons, strongest_counter, prefilter_check?, historical_sha, current_sha, drifted}, ...], prefilter_precision:{H1,H2,H3}}.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/challenger_retro.md
- Window: 90 days of labeled outcomes; runs Sunday 09:00 UTC (after coverage_auditor at 04:00 UTC, before reporting_weekly_cron at 12:00 UTC).
- Companion auditors (Modal, SQL-only): precision_auditor + timing_auditor in modal_workers/observability.py, Sunday 02:15 UTC, same accuracy_metrics table.
- Shares the thesis_challenger Cowork-routine quota with thesis_writer + signal_resolver; stratified sampling keeps the total cheap (≤10 calls/run, minus DLQ-decline samples that need no call).

Report JSON: {sampled_total, pre_edge_hit_sampled, dead_catalyst_sampled, post_edge_miss_sampled, unlabeled_sampled, calibrated_hit_n, ambiguous_hit_n, miss_n, save_n, partial_save_n, pass_through_n, timing_catch_n, timing_miss_n, decline_n, over_decline_n, early_save_n, timing_save_n, miss_rate, pass_through_rate, save_rate, calibrated_hit_rate, tier, rolling_n, rolling_miss_rate, prefilter_precision:{H1,H2,H3}, sha_drift_n, flags_raised, flags_resolved, empty_sample_exit}.
