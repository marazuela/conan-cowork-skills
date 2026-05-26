Run the fact_extractor_opus skill. Follow `$CONAN_ROOT/.claude/skills/fact_extractor_opus.md` steps 1–5 verbatim. Hourly cadence — Opus-driven structured fact extraction from material `asset_documents` into `extracted_facts`. Replaces the disabled Modal Sonnet `v3-fact-extractor` pg_cron — runs under Pedro's Claude.app subscription, zero API spend.

Outputs wired into the Conan app via the Supabase MCP (project ref: `xvwvwbnxdsjpnealarkh`). All persistence must actually happen on that project — INSERT rows into `public.extracted_facts` with `extraction_model='claude-opus-4-7'`, `evidence_quote` (verbatim ≤300 chars) and `citation_span` jsonb `{start, end}` char offsets into `documents.raw_text`. Returned JSON summarizes writes — does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Read-only on `asset_documents` + `documents`; INSERT-only on `extracted_facts` (invariant 1). Never UPDATE/DELETE. Idempotent: skip docs that already have any `extracted_facts` row for the same `(document_id, asset_id)` pair via the `NOT EXISTS` clause in step 1.

- Material docs only — `asset_documents.is_material = true` (invariant 3). The linker already filtered boilerplate; trust its judgment. Ordering is `ORDER BY ad.created_at DESC LIMIT 10` per skill step 1 — newest material links first.

- **Quotas (changed 2026-05-23, commit 154090c — raised 50 → 200 docs/UTC-day):** ≤10 docs per run, ≤200 docs per UTC day. Soft caps. Step 2 checks `count(*) FROM extracted_facts WHERE extraction_model LIKE 'claude-opus%' AND extracted_at::date = today` against 200 — if reached, log `"daily quota reached"` and exit. Do NOT enqueue beyond.

- Empty pending result from step 1 → log `"no pending material docs"` and exit cleanly with `{processed: 0, facts_inserted: 0, empty_queue_exit: true}`.

- **Quote-or-skip (invariant 2).** Every fact MUST have `evidence_quote` (verbatim ≤300 chars from `raw_text`) AND `citation_span` jsonb with valid char offsets. No fabrication. If you can't locate the exact offset, omit the fact. No facts emitted from prior knowledge of a drug — quote `raw_text` or emit nothing.

- **Honest empty (invariant 5).** A doc that doesn't yield investor-grade structured facts produces ZERO rows. The doc isn't lost — `asset_documents` keeps the link; only `extracted_facts` stays empty. False positives waste downstream Stage 0 attention.

- `fact_type` ∈ {trial_result, mechanism, dose_response, adverse_event, regulatory_milestone, sponsor_action, market_data, competitive_context, safety_signal, indication_label, endpoint_meeting, enrollment, manufacturing, ip, other} per §3 dispatch table.

- Out-of-scope (invariant 6): no INSERT/UPDATE on `convergence_assessments`, `orchestrator_runs`, `asset_documents`, `signals`, `thesis_jobs`. No score, no band, no thesis — Stage 0 of `run_one` consumes these facts downstream.

- Don't re-enable the disabled `v3-fact-extractor` pg_cron — it's intentionally off because this skill replaces it.

- Transient infra failures (API `ConnectionRefused`/401, stream idle-timeout, mid-run abort): do NOT add per-run retry loops. The next hourly tick re-selects the same pending docs — INSERTs are idempotent, so an aborted run loses no work, only defers ~1h. `skill-watchdog` escalates to `operator_flags` if this skill stays dark beyond SLA.

Project context:

- Project: Conan v3
- Supabase ref: `xvwvwbnxdsjpnealarkh`
- Skill file on disk: `$CONAN_ROOT/.claude/skills/fact_extractor_opus.md`
- Source: `asset_documents` WHERE `is_material=true` AND no existing `extracted_facts` row, ordered by `created_at DESC`.
- Sink: `extracted_facts`, `extraction_model='claude-opus-4-7'`.

Report JSON: `{processed, facts_inserted, docs_with_zero_facts, daily_quota_used, daily_quota_cap: 200, quota_reached, empty_queue_exit, anomalies}`.
