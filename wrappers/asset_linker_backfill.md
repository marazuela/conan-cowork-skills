Run the asset_linker_backfill skill. Follow $CONAN_ROOT/.claude/skills/asset_linker_backfill.md steps 0–9 verbatim, including the yield-first tiering in step 2 and the always-stamp marker discipline in step 7.

Opus-driven asset_linker pass-1 for Conan v3: classify unprocessed `documents` into `asset_documents` links on Pedro's Claude.app subscription. Replaces the Modal Sonnet pg_cron `v3-asset-linker-pass1` (disabled 2026-05-13, burned ~$42/week on near-zero-yield 2023 EDGAR noise). Zero Anthropic API spend — all reasoning happens inside this session, all I/O via the supabase MCP `execute_sql`.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — `asset_documents` INSERTs with `extraction_method='cowork_backfill'` and `verified_by_pass2=false`, ON CONFLICT (asset_id, document_id, link_type) DO NOTHING; the `documents.linker_classified_{at,result,asset_set_hash}` marker triple UPDATE on every doc touched; one `asset_linker_runs` row per invocation with `pass='cowork_backfill'`, `model='claude-app-cowork'`. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Hard-halt check first (skill step 0). Predicate is on `operator_flags.resolved_at IS NULL AND source='asset_linker_hard_halt' AND created_at > now() - interval '24 hours'` — NOT on a `status` column. If `halt > 0`, log `"asset_linker 24h hard halt active; skipping"` and exit.

- Quotas: ≤25 docs per run, ≤300 docs per UTC day (counted as `asset_documents WHERE extraction_method='cowork_backfill' AND created_at::date = today UTC`). Soft cap — stop at the limit, do not enqueue beyond.

- Yield-first claim order (invariant 9). Both Modal pass-1 (disabled 2026-05-13) and pass-2 Haiku verifier (removed 2026-05-21) are gone — no concurrent writers, no race-safety reason to claim oldest-first. Tier 1: `source IN ('conan_signal','press_release')`. Tier 2: `source IN ('clinicaltrials','dailymed','openfda','fda_advisory')`. Tier 3: everything else. Within tier, `published_at DESC NULLS LAST`. The prior pass-1/pass-2 distinction is dead — this skill IS pass-1.

- Always stamp the marker (invariant 2) on every doc touched — including prefilter-skipped and zero-link docs. Without the stamp, the next run re-claims the doc. If the marker UPDATE fails, abort the run immediately — do not continue and leave debris.

- Cite or don't link (invariant 3). Every emitted link MUST include 1–3 verbatim `extracted_spans` (≤300 chars each) quoting `raw_text`. No fabrication. `is_material` is investor-grade — boilerplate 13F / market-table / competitor-filing name-drops are NOT material.

- `extraction_method='cowork_backfill'` on every row (invariant 8). Never write `'agent_pass1'` / `'agent_pass2'` (legacy Modal). Leave `verified_by_pass2=false` so if pass-2 ever resurrects, it'll re-verify these rows.

- link_type taxonomy fixed (invariant 6): `primary` | `mentions` | `pipeline_context` | `safety_signal` | `literature`. No variants.

- Out of scope: `signals`, `convergence_assessments`, `orchestrator_runs`, `thesis_jobs`, `extracted_facts`, `fda_assets`. Read `documents.raw_text` and `fda_assets`; INSERT only `asset_documents`; UPDATE only the `documents.linker_classified_*` marker triple. Never DELETE.

- Transient infra failures: do NOT add per-run retry loops. The next `*/30` tick reclaims the same docs (claim is yield-first and idempotent — an aborted run loses no work, only defers ~30 min). `skill_watchdog` escalates to `operator_flags` if this skill stays dark beyond SLA.

Project context:

- Project: Conan v3
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/asset_linker_backfill.md
- Queue source: `documents WHERE linker_classified_at IS NULL OR linker_classified_asset_set_hash IS NULL OR linker_classified_asset_set_hash <> <current>`
- Replaces: Modal `v3-asset-linker-pass1` pg_cron (disabled 2026-05-13)

Report JSON: {processed, prefilter_passed, prefilter_skipped, links_inserted, linked, no_match, parse_error, errors, daily_count, daily_cap, asset_set_hash, hard_halt_active, empty_queue_exit}.
