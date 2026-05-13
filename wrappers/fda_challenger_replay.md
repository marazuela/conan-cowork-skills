Run the fda_challenger_replay skill. Follow $CONAN_ROOT/.claude/skills/fda_challenger_replay.md steps 1–8 verbatim, including the SQL kernel call (step 1), the per-row context fetch (step 2), the Stage 3-only replay (step 3, never Stage 1+2), the 11-row classification matrix (step 4), the single accuracy_metrics insert (step 5), and the per-run + rolling-30d flag logic (steps 6–7).

Outputs wired into the Conan v3 app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — `accuracy_metrics` INSERT (exactly one row, auditor='challenger_retro', with per-classification n-counts, rates, evidence jsonb); `operator_flags` UPSERT (source='challenger_retro', kind ∈ {challenger_retro_miss, challenger_retro_pass_through, challenger_retro_timing_blindspot, challenger_retro_rolling_miss}). Returned summary describes writes — does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Once per week, kernel-gated. The SQL kernel `v3_challenger_retro_sql_kernel(90)` returns at most 10 rows + a tier flag. Per-run flags fire only when `tier='full'` (universe >= 5 pre_edge_hit AND >= 5 dead_catalyst). At `tier='preview'` or `tier='insufficient'`, write the accuracy_metrics row but skip per-run flags. The rolling-30d aggregator is independent and can fire across preview runs once `rolling_n >= 8`.

- Stage 3 replay only (invariant 2). Never replay Stage 1 (synthesis) or Stage 2 (hypothesis enumeration). Replaying earlier stages produces a different evidence ledger and breaks the v2-parity contract on the matrix. Re-use the canonical STAGE_3_SYSTEM prompt from `orchestrator_runtime/premortem.py` — DO NOT fork or paraphrase.

- One accuracy_metrics row per run (invariant 3). Exactly one. `measured_at=now()`, `auditor='challenger_retro'`, `window_days=90`. If insufficient sample, still write the row with zero n-counts and `insufficient_sample=true`. Rolling aggregator depends on row cardinality; two rows per run double-counts.

- Verdict matrix is canonical (invariant 5). Use the 11-row table verbatim. Outside-matrix combinations (wildcard stratum, no-hypotheses, parse_failure) log as `unclassified` with reason in evidence jsonb — do NOT count toward miss/save/pass_through rates.

- New verdict rollup picks most-extreme: precedence falsified > weakened > survives. Map to v2 challenger verbs (confirm/challenge/kill). Any hypothesis with `is_declined=true` in the replay output overrides to `decline` per the rollup precedence.

- operator_flags source CHECK extension: until a migration adds `'challenger_retro'` to operator_flags_source_check, raise warnings via stdout instead of failing the INSERT. Pair the CHECK extension with the M6 pg_cron schedule rollout for this skill — same migration.

- No Tier-1 escalations from this skill (invariant 6). Observational only. If a replay produces a striking new verdict, the operator can refresh via dashboard.

- Cost is best-effort. ~$0.30/replay × 10 = ~$3/run. Log it but don't gate on it; Cowork's seat absorbs the spend.

- Idempotent rolling aggregator. The rolling-30d query is over `accuracy_metrics WHERE auditor='challenger_retro' AND measured_at >= now() - interval '30 days'`. A re-run on the same UTC week would double-count its own contribution — but the scheduler runs only weekly, so this is theoretical. On-demand re-runs (operator phrase) should be rare and the operator accepts the duplication trade.

- Output write order per run: (1) kernel SQL call, (2) per-row context reads, (3) per-row Stage 3 replay, (4) classification, (5) ONE accuracy_metrics INSERT, (6) per-run operator_flags UPSERTs (if tier='full'), (7) rolling-30d operator_flag UPSERT (if rolling_n threshold met). Steps 5–7 wrap in a single SQL transaction batch via Supabase MCP.

Project context:

- Project: Conan v3
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/fda_challenger_replay.md
- Source: `v3_challenger_retro_sql_kernel(90)` returning up to 10 stratified rows from `convergence_assessments JOIN post_mortem_queue WHERE status='post_mortem_complete'`.
- Stage 3 prompt source: `STAGE_3_SYSTEM` constant in `orchestrator_runtime/premortem.py`. DO NOT fork — reuse the canonical prompt for v2 parity.
- Writer target: `accuracy_metrics` (migration 20260425000000) carries every previous challenger_retro row from v2. Same auditor column, same schema, same matrix.
- Peer skill: `fda_aging_review` (daily 06:00 UTC) shares Pedro's Cowork seat. Different cron slot, different day-of-week. Mutual non-interference.

Report JSON: {sample_n, tier, universe_sizes:{pre_edge_hit, dead_catalyst, post_edge_miss}, classifications:{calibrated_hit_n, ambiguous_hit_n, miss_n, save_n, partial_save_n, pass_through_n, timing_catch_n, timing_miss_n, decline_n, over_decline_n, early_save_n, timing_save_n, unclassified_n}, rates:{miss_rate, pass_through_rate, save_rate, calibrated_hit_rate}, replay_cost_usd_total, flags_raised:[...], rolling_30d:{rolling_miss_rate, rolling_n, flag_fired}, accuracy_metrics_id}.
