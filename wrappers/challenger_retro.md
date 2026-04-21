Run the challenger_retro skill. Follow $CONAN_ROOT/.claude/skills/challenger_retro.md steps 1–9 verbatim. Sample ≤10 historically outcome-labeled candidates, re-invoke thesis_challenger in drafting mode on each historical thesis, classify verdicts against actual outcomes, write one accuracy_metrics row and raise operator_flags on threshold breach.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — accuracy_metrics INSERT (exactly one row per run, auditor='challenger_retro'); operator_flags UPSERT on miss_rate / pass_through_rate / timing_blindspot breach; operator_flags auto-resolve (PATCH resolved_at) on subsequent runs where the condition clears. Read-only against candidates / outcomes / candidate_events / thesis_jobs / signals (invariant 1). Returned report JSON summarizes the sample — does not replace writes. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Bounded quota (invariant 4): max 10 thesis_challenger invocations per run. Stratified sampling (invariant 3): 3 pre_edge_hit + 3 dead_catalyst + 2 post_edge_miss + 2 any remaining labeled rows. Redistribute slots across buckets if any bucket has fewer rows than its slot count. Total cap = 10 regardless of surplus.

- Fresh context per challenger invocation (invariant 2). Each call is a separate routine invocation. No shared prior between samples. No shared prior with the original drafting session. Context contamination invalidates the "would today's challenger disagree" property you're measuring.

- Drafting mode only (invariant 6). Invoke thesis_challenger with mode:"drafting" on every sample. The aging-mode retro is a separate future extension.

- Window: outcomes.created_at >= now() - interval '90 days' AND outcome_label IN ('pre_edge_hit','dead_catalyst','post_edge_miss'). Other outcome_label values exist but don't fit the classification matrix — excluded from rate metrics.

- Classification matrix (step 5):
  - pre_edge_hit × confirm = calibrated_hit ✓
  - pre_edge_hit × challenge = ambiguous_hit
  - pre_edge_hit × kill = MISS ✗
  - dead_catalyst × kill = save ✓
  - dead_catalyst × challenge = partial_save
  - dead_catalyst × confirm = PASS_THROUGH ✗
  - post_edge_miss × {kill, challenge} = timing_catch ✓
  - post_edge_miss × confirm = TIMING_MISS ✗

- Rate flags require minimum samples (invariant 5). miss_rate needs pre_edge_hit_sampled ≥ 5; pass_through_rate needs dead_catalyst_sampled ≥ 5; timing_blindspot needs post_edge_miss_sampled ≥ 3. Below threshold → no flag, regardless of rate. Prevents noise from tiny samples.

- Flag thresholds (step 8):
  - challenger_retro_miss: warn at miss_rate ≥ 0.10, critical at ≥ 0.25 (both with pre_edge_hit_sampled ≥ 5).
  - challenger_retro_pass_through: warn at pass_through_rate ≥ 0.25 (with dead_catalyst_sampled ≥ 5).
  - challenger_retro_timing_blindspot: warn at timing_miss_n ≥ 2 (with post_edge_miss_sampled ≥ 3).

- One accuracy_metrics row per run (invariant 7), always. Even on empty-sample runs (zero labeled outcomes in window): write one row with insufficient_sample=true, sample_n=0, rates all NULL. Preserves the time series so Pedro can see the auditor ran.

- Auto-resolve open flags when the condition clears on a subsequent run (step 8 tail). PATCH resolved_at = now() + resolved_note='auto-resolved: rate recovered'. Query for open flags of this source+kind with no matching current breach; resolve each.

- Read-only against live state (invariant 1). Never UPDATE candidates, outcomes, thesis_jobs, thesis_drafting_failures, candidate_events, signals. The retro observes; it doesn't act. Only accuracy_metrics + operator_flags writes.

- Sample payload (step 4) per thesis_challenger drafting mode: {mode:"drafting", draft:<historical thesis JSON from candidate_events.payload.thesis>, signal:<signals row>, entity:<entity row>, scanner:<scanner row>, filing_text:<≤32KB of cited text or empty string>}. Capture the full verdict JSON verbatim in evidence_jsonb for Pedro's audit.

- evidence_jsonb (step 7) is an array of per-sample records: [{candidate_id, ticker, mic, outcome_label, verdict, reasons, strongest_counter}, ...]. This is the full trace of which candidates were sampled and what today's challenger said — load-bearing for Pedro's audit.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/challenger_retro.md
- Window: 90 days of labeled outcomes; runs Sunday 09:00 UTC (after coverage_auditor at 04:00 UTC, before reporting_weekly_cron at 12:00 UTC).
- Companion auditors (Modal, SQL-only): precision_auditor + timing_auditor in modal_workers/observability.py, Sunday 02:15 UTC, write to the same accuracy_metrics table.
- This skill shares the thesis_challenger Cowork-routine quota with thesis_writer + signal_resolver. Retro is stratified-sampled to keep the total cheap — max 10 calls/run vs thesis_writer's worst-case 4 calls/job.

Report JSON: {sampled_total, pre_edge_hit_sampled, dead_catalyst_sampled, post_edge_miss_sampled, unlabeled_sampled, calibrated_hit_n, ambiguous_hit_n, miss_n, save_n, partial_save_n, pass_through_n, timing_catch_n, timing_miss_n, miss_rate, pass_through_rate, save_rate, calibrated_hit_rate, flags_raised, flags_resolved, empty_sample_exit}.
