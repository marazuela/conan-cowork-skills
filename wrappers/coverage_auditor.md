Run the coverage_auditor skill. Follow $CONAN_ROOT/.claude/skills/coverage_auditor.md steps 1–7 verbatim. Mechanical only — NO Claude reasoning, NO challenger, NO Anthropic app routines. Pure SQL bucketing + Storage upload.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — operator_flags UPSERT for the top-10 "should have caught" cases (source='reporting_weekly', kind='coverage_miss' or 'coverage_empty_window'); weekly markdown report PUT to Storage at 'reports/coverage/<iso_week>.md'. Returned JSON summarizes writes — does not replace them. If you cannot reach the Supabase MCP or Storage REST, do not fabricate success; report the failure.

Guardrails:

- Primary window is the most recent COMPLETE UTC ISO week (Mon 00:00 → Sun 23:59:59 UTC). NOT the partial current week. Use date_trunc('week', ...) with the 7-day back offset per skill step 1. Key by to_char(..., 'IYYY-"W"IW').

- Trend window is the last 180 days ending at window_end. Only used for the executive-summary rollup in the markdown — not for flag writes.

- Material-only for coverage metrics (invariant 4). Filter catalyst_universe.material_outcome='yes' in denominators. Non-material rows stay in the raw ledger for future false-positive analysis but don't count toward recall rates.

- Fail-loud on empty windows (invariant 5). If catalyst_universe has zero material rows in the window, emit operator_flags(severity='warn', source='reporting_weekly', kind='coverage_empty_window') and stop. That means the universe fetchers are broken, not that the world was quiet.

- Bucket logic is deterministic (step 3-4): caught_pre_edge requires at least one emissions_ledger row with gate_decision='promoted' AND promoted_at <= catalyst_date. caught_post_edge = promoted after. emitted_but_<decision> picks the EARLIEST emission's gate_decision. never_emitted = zero emissions in the 90-day pre-catalyst window. Ticker-first join, entity_id fallback.

- Top-10 flag writes (step 5) rank by realized_price_move DESC NULLS LAST, catalyst_date DESC (recency fallback when move is NULL). Exclude caught_pre_edge — those aren't misses. Severity: 'warn' for never_emitted or caught_post_edge; 'info' for emitted_but_*. Max 10 rows upserted.

- Idempotent operator_flags (invariant 3). ON CONFLICT target matches the operator_flags_open_uniq partial unique index: (source, kind, coalesce(scanner_id::text,''), coalesce(entity_id::text,''), coalesce(signal_id,''), coalesce(candidate_id::text,'')) WHERE resolved_at IS NULL. Re-running the same week UPDATES open flags rather than inserting duplicates.

- NEVER write to signals / thesis_jobs / candidates / outcomes (invariant 6). Read-only against the pipeline; only operator_flags + Storage are written.

- Storage upload via bash curl with SUPABASE_SERVICE_ROLE_KEY in env. Path: reports/coverage/<iso_week>.md with text/markdown + x-upsert: true header. Overwrites on re-run (invariant 2) — intentional, catches newly-landed catalyst_universe rows.

- Entity-id resolution is partial (~15% for EDGAR 8-K per 2026-04-21 baseline). The ticker-first join in step 3 handles the ~100% ticker-resolved rows; entity_id fallback catches the rest. Don't expect 100% entity coverage; report what the ledger has.

- Final skill-output summary (step 7): one machine-parseable line. "coverage_auditor iso_week={W} material={N} pre_edge={X} post_edge={Y} emitted_not_promoted={Z} never_emitted={M} top_miss_flags={K} report_url=reports/coverage/{W}.md". The Cowork session surfaces this to Pedro.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/coverage_auditor.md
- Tables read: catalyst_universe (material catalysts in the world), emissions_ledger view (what the pipeline emitted + gate decision + candidate state + outcome), entities (entity_id lookup).
- Live universe feeds (2026-04-21): fda_adcomm_pdufa, sec_8k_mna. Others deferred.
- Runs Sunday 04:00 UTC, before reporting_weekly_cron (Sun 12:00 UTC) so coverage misses surface in the weekly executive PDF.

Report JSON: {iso_week, material_n, pre_edge, post_edge, emitted_not_promoted, never_emitted, top_miss_flags, report_url, empty_window_exit}.
