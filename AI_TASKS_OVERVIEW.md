# AI Tasks Overview

Single-page reference for how every AI-handled task in Conan intertwines. Companion to [SETUP.md](SETUP.md) (machine setup) and [README.md](README.md) (repo orientation).

**Last revised:** 2026-05-19 (audit of §10 against live code: Stream 2, options_microstructure, and Stream 3 marked done; MCP server status corrected; v3 dashboard restated).

---

## 1. Two-tier coexistence model

Two AI tiers run in production today:

- **v2 trio (legacy, multi-vertical).** `signal_resolver`, `thesis_writer`, `candidate_aging` plus weekly auditors. Drains the categorical-band signal pipeline (`signals → reactor → thesis_jobs → candidates`). Vertical-agnostic (activist, merger arb, litigation, etc.). Runs as Cowork scheduled tasks on the runner machine.
- **v3 FDA orchestrator (new, FDA-only).** `asset_documents → orchestrator_runs → convergence_assessments` driven by probabilistic `conviction_pct` from a 7-member ensemble + isotonic calibration. Three sub-agents (literature, regulatory history, competitive landscape) run in parallel as Cowork plugin sub-agents. Stream 2 closes the feedback loop (post-mortem → calibration refit → rollback monitor).

Both tiers share the Supabase project `xvwvwbnxdsjpnealarkh` but write to disjoint tables. v2 short-circuits FDA traffic: reactor returns `{skipped: "fda_profile_routed_to_orchestrator"}` for `binary_catalyst` / `fda_event` profile signals so v3 owns FDA end-to-end (D-122).

---

## 2. v2 data flow

```
scanner (Modal, 19 functions)
   │
   ▼ INSERT
signals ─────► reactor (edge fn, v12)  ──► alerts        (audit only, no email)
                  │                    └──► thesis_jobs  (queued for AI draft)
                  │                    └──► failed_reactor_events (DLQ)
                  ▼ classifyGroup + bonus stamping (skipped for FDA profiles)
                signals.score_with_bonus / band_with_bonus

signal_resolver  (every 10 min)
   │ drains thesis_jobs WHERE status='needs_scoring'
   │ + provisional heuristic rows (extensions.scoring_meta.requires_resolution)
   │ → researches filing, fills dimensions, rescores via rubric_engine
   │ → if resolved band='immediate', drafts thesis inline same as thesis_writer
   ▼

thesis_writer    (hourly :00 UTC, 15 promotions/UTC day)
   │ drains thesis_jobs WHERE status='queued' AND band='immediate'
   │ → draft → thesis_challenger.confirm/challenge/kill → syntactic gate
   │ → INSERT candidates (state='watch')  +  candidate_events (event_type='created')
   │ → DLQ: thesis_drafting_failures
   ▼

fanout (edge fn, v8)  on candidate_events.INSERT (event_type='created')
   │ → render pre-edge email → Resend dispatch
   │ → notifications_prefs.email_on_immediate audience
   ▼

candidate_aging  (daily 06:00 UTC, 15 Claude evals/UTC day)
   │ Stage A (mechanical, free): catalyst-elapsed transitions, 60d watch→archive,
   │                              30d active no-near-catalyst → watch
   │ Stage B (Claude-mediated): kill-condition evaluation against 14d signal window
   │ → state transitions {watch, active, killed, delivered}
   │ → DLQ: candidate_aging_failures
   ▼
candidates + candidate_events  ─► dashboard (Next.js, separate repo)
                                 thesis_jobs.resolved_at = user dismissal
```

Code anchors: [supabase/functions/reactor/index.ts](../Conan/supabase/functions/reactor/index.ts), [supabase/functions/fanout/index.ts](../Conan/supabase/functions/fanout/index.ts), [modal_workers/app.py](../Conan/modal_workers/app.py) (RPCs `assess_thesis`, `render_candidate`, `regex_search`, `multi_fetch`, `rubric_apply_caps`).

---

## 3. v3 data flow

```
ingestion (FDA + EDGAR fetchers, Modal)
   │
   ▼ INSERT
documents ──► asset_documents ──► reactor (edge fn, v12)
                                       │ on `payload.table == "asset_documents"`
                                       │ derive trigger_type:
                                       │   `cross_source` if sibling primary in 24h
                                       │   `new_doc` otherwise
                                       ▼
                              orchestrator_runs (queue)
                                       │
                                       ▼
                              orchestrator_app.orchestrator_drain_queue (Modal, max 5/run)
                                       │
                                       ▼ parallel dispatch
   ┌───────────────────────────────────┼───────────────────────────────────┐
   ▼                                   ▼                                   ▼
sub_agent_literature_reviewer  sub_agent_regulatory_history  sub_agent_competitive_landscape
(PubMed, bioRxiv, RAG)         (openFDA, AdComm, base rates) (clinicaltrials, openFDA, RAG)
   └────────────────────────► literature_review_v1.json + regulatory_history_v1.json + competitive_landscape_v1.json
                                       │
                                       ▼
                              Stage 1 evidence ledger
                              Stage 2 hypothesis enumeration {bull, base, bear} ≥2 kill_conditions each (D-115)
                              Stage 3 pre-mortem (caps conviction at 30pp if all_falsified, D-115/D-117)
                              Stage 4 reference-class anchor + renormalize_priors (D-118)
                              Stage 5–7 ensemble (N=7) + constitutional gate
                              Stage 8 isotonic calibration (D-103 gated)
                              Stage 10 finalize → conviction_pct + direction + citations
                                       │
                                       ▼ INSERT (atomic via persist_assessment_v3 RPC)
                              convergence_assessments  +  post_mortem_queue (status='pending')
                                       │
                              ┌────────┴───────────┐
                              ▼                    ▼
                        fanout entry-D     Stream 2 drains at outcome_window_end
                        (band='immediate')
                              │
                              ▼ render HTML/text + Storage upload + Resend + Realtime broadcast
                        operator email + asset:<id> channel
```

**Stream 2 (closed feedback loop, daily 02:00 UTC via pg_cron `v3-feedback-loop-daily`):**

```
post_mortem_runner.run_post_mortem_drain
   │ drains post_mortem_queue WHERE outcome_window_end < now() AND status='pending'
   │ → label_event() resolves T+30/60/90/180 returns + HIT/MISS verdict (D-116)
   │ → prediction_error = predicted_conviction_pct − realized_outcome_score
   │ → Haiku 4.5 retrospective (200 words)
   │ → UPSERT reference_class_base_rates (Wilson 95% CI)
   │ → append to memory_files/asset_<id>.md (idempotent <!-- assessment:<id> --> marker)
   ▼
nightly_calibration_refit
   │ pulls (raw_conviction/100, direction_aligned_outcome, asset_id) triples
   │ → fits isotonic curve via PAV (compute.fit_isotonic_curve)
   │ → D-103 gate: n≥200, brier_delta>0, paired_bootstrap p<0.05, AUC_delta≥0.05, max_asset≤5%
   │ → INSERT calibration_curves (is_active=false unless ENABLE_PROMOTION=true)
   │ → INSERT eval_runs with gate decision
   ▼
rollback_monitor
   │ daily Spearman(realized_return_30d, conviction_pct_calibrated) over past 30d
   │ → if n≥30 AND (corr<0.20 OR Δcorr ≤ −0.15): restore prior calibration_curves snapshot
```

Code anchors: [conan-fda-orchestrator-plugin/skills/](../Conan/conan-fda-orchestrator-plugin/skills/) (4 sub-agent files incl. options_microstructure), [modal_workers/orchestrator_app.py](../Conan/modal_workers/orchestrator_app.py), [orchestrator_runtime/runtime.py](../Conan/orchestrator_runtime/runtime.py) (Stage 10 finalize), [modal_workers/feedback_loop_app.py](../Conan/modal_workers/feedback_loop_app.py), [modal_workers/shared/post_mortem_runner.py](../Conan/modal_workers/shared/post_mortem_runner.py), `supabase/migrations/20260528000000_persist_assessment_v3_rpc.sql` (atomic INSERT convergence_assessments + post_mortem_queue). Decisions: D-100, D-102, D-103, D-104, D-105, D-115, D-117, D-118, D-119, D-122, D-123 in [DECISIONS.md](../Conan/DECISIONS.md).

---

## 4. Tier-2 (breadth) escalation

`bulk_orchestrator_run` ([skills/bulk_orchestrator_run.md](skills/bulk_orchestrator_run.md)) is a Cowork-scheduled sweep across `fda_assets` keyed by `watch_priority`:

| watch_priority | Cadence              | Notes                                     |
|----------------|---------------------|-------------------------------------------|
| 1              | daily 09:00 UTC     | post overnight ingest, pre US cash open   |
| 2              | weekly Mon 09:00 UTC| folds in priority-1 sweep                 |
| ≥3             | event-only          | Tier-1 picks them up via `new_doc`        |

Each run uses a single Sonnet pass (no ensemble, no constitutional gate) producing a `convergence_assessment_v1` JSON. The runtime persists it as a `tier=2` row, then applies the **escalation rule** (D-128): on high conviction, direction change, or new primary doc, enqueue a Tier-1 run for the same asset. Tier-2 cap is 50 runs/UTC day (~$25/day at $0.50/run); Tier-1 escalations consume the Tier-1 hard-kill ceiling instead (D-125).

---

## 5. FDA review side-channel (decision-support, hourly)

Three Cowork skills drain `fda_agent_reviews`. They are **decision-support only** — they never set score or band directly; they emit JSON-validated payloads consumed by Tier-1 / Tier-2:

| Skill                       | Cadence       | Quota   | Emits                                                            |
|-----------------------------|---------------|---------|------------------------------------------------------------------|
| `fda_medical_review`        | hourly :15 UTC| 10/day  | endpoint quality, safety, effect size, precedent class           |
| `fda_regulatory_review`     | hourly :30 UTC| 10/day  | evidence_confidence_boost (±0.40), resubmission_pathway label    |
| `fda_microstructure_review` | hourly :45 UTC| 10/day  | options_liquidity_score, implied_move_pct, borrow_cost_bps       |

Failures route to `failed_reactor_events` (filter `payload->>'source' = '<skill_name>'`) plus an `operator_flags` row.

---

## 6. Failure surfaces

| Table                         | Written by                                           | Read by                          |
|-------------------------------|------------------------------------------------------|----------------------------------|
| `failed_reactor_events`       | reactor edge fn AND Cowork preflight skills          | operator triage; filter on `payload->>'source'` |
| `thesis_drafting_failures`    | thesis_writer (challenger decline / syntactic gate fail) | operator triage; `error_kind` enum |
| `candidate_aging_failures`    | candidate_aging                                      | operator triage; `error_kind`, `consecutive_failures` |
| `operator_flags`              | translation_health, scanner_probe, convergence_qa, coverage_auditor, challenger_retro, FDA review skills | dashboard operator panel |
| `post_mortem_queue` (status=`no_outcome`) | post_mortem_runner when ticker delisted/halted/sentinel | calibration runs skip these |

---

## 7. Quotas, schedules, cost (consolidated)

| Task                          | Tier        | Schedule                              | Quota                                    | Compute       |
|-------------------------------|-------------|---------------------------------------|------------------------------------------|---------------|
| signal_resolver               | v2          | every ~10 min                         | shares thesis_writer 15/day cap          | Sonnet        |
| thesis_writer                 | v2          | hourly :00 UTC                        | 15 promotions/UTC day                    | Sonnet        |
| thesis_challenger             | v2 (called) | post-draft / post-aging-claim         | 1 call/invocation, fresh context         | Sonnet        |
| candidate_aging               | v2          | daily 06:00 UTC                       | 15 Claude evals/UTC day (Stage A free)   | Sonnet        |
| challenger_retro              | v2          | weekly Sun 09:00 UTC                  | 10 challenger invocations/run            | Sonnet        |
| coverage_auditor              | v2 (Modal)  | inside `reporting_weekly` cron `0 12 * * 0` | none — pure SQL                    | none          |
| fda_medical_review            | v2 side     | hourly :15 UTC                        | 10/day                                   | Sonnet        |
| fda_regulatory_review         | v2 side     | hourly :30 UTC                        | 10/day                                   | Sonnet        |
| fda_microstructure_review     | v2 side     | hourly :45 UTC                        | 10/day                                   | Sonnet        |
| bulk_orchestrator_run         | v3 Tier 2   | daily 09:00 UTC (p=1) + weekly Mon (p=2) | 50 Tier-2 runs/UTC day (~$25)         | Sonnet, $0.30–0.80/run |
| orchestrator (Tier 1)         | v3          | event-driven (`new_doc`, `cross_source`, `operator_refresh`, `tier2_escalation`) | N=7 ensemble + Tier-1 hard-kill ceiling | $10–15/run, ~3–4 min |
| daily_feedback_loop (drain → monitor → refit) | v3 Stream 2 | daily 02:00 UTC via Supabase pg_cron `v3-feedback-loop-daily` | drain batch 200; refit n≥200 (D-103 gate); rollback n≥30 trigger | Haiku 4.5 (post-mortem text) + compute |

Modal's free-tier 5-cron cap is fully consumed by conan-v2 (1 Period + 4 Cron). D-123's three Stream 2 steps were chained into a single `daily_feedback_loop` function and triggered via Supabase pg_cron (`v3-feedback-loop-daily`, `0 2 * * *`) — zero Modal cron slots consumed. Same pattern as v3 orchestrator drain and asset-linker pg_cron jobs. Migration: `20260508114735_v3_feedback_loop_pg_cron`.

---

## 8. RPC endpoints (Modal-backed, called from Supabase / Cowork)

| Endpoint                       | Caller                          | Purpose                                            |
|--------------------------------|---------------------------------|----------------------------------------------------|
| `rpc_rescore_with_dims`        | signal_resolver                 | Re-score signal after dimensions filled            |
| `rpc_assess_thesis`            | thesis_writer                   | Gate-check drafted thesis (v2 schema)              |
| `rpc_render_candidate_markdown`| thesis_writer                   | Render thesis as markdown for Storage              |
| `rpc_regex_search`             | candidate_aging                 | Pattern-match regulatory events vs kill_conditions |
| `rpc_multi_fetch`              | signal_resolver, thesis_writer  | Fetch filings / Storage objects                    |
| `orchestrator_run_one`         | reactor (v3)                    | Run Tier-1 orchestrator on one asset               |
| `orchestrator_drain_queue`     | Modal cron                      | Drain orchestrator_runs queue (max 5/run)          |
| `persist_assessment_v3`        | Stage 10 (orchestrator runtime) | Atomic INSERT convergence_assessments + secondaries + post_mortem_queue |
| `daily_feedback_loop`          | pg_cron `v3-feedback-loop-daily`| Stream 2 unified entrypoint (drain → monitor → refit) |
| `post_mortem_drain_dry_run`    | manual `modal run`              | Dry-run validation of Stream 2                     |
| `rollback_monitor_dry_run`     | manual `modal run`              | Dry-run validation of rollback                     |

---

## 9. Where things live

- **v2 trio + auditors + FDA review skills:** [skills/](skills/) (this repo, hardlinked into `Conan/.claude/skills/` on the Mac).
- **v3 sub-agents:** [conan-fda-orchestrator-plugin/skills/](../Conan/conan-fda-orchestrator-plugin/skills/) and [modal_workers/sub_agents/](../Conan/modal_workers/sub_agents/) — four roles: `literature`, `regulatory_history`, `competitive`, `options_microstructure`, plus the `ic_memo` synthesis runner. Cowork plugin skills with `context: fork`, MCP tool lists, output schemas in `schemas/` (this repo).
- **v3 MCP servers:** [conan-fda-orchestrator-plugin/mcp_servers/](../Conan/conan-fda-orchestrator-plugin/mcp_servers/) — `pubmed`, `biorxiv` (intentional stub), `openfda`, `fda_adcomm`, `polygon`, `internal_rag`, `compute`, `clinicaltrials`.
- **v3 orchestrator runtime:** [modal_workers/orchestrator_app.py](../Conan/modal_workers/orchestrator_app.py) (Modal app entry) + [orchestrator_runtime/runtime.py](../Conan/orchestrator_runtime/runtime.py) (stages 1–10, including Stage 10 finalize).
- **v3 feedback loop:** [modal_workers/feedback_loop_app.py](../Conan/modal_workers/feedback_loop_app.py) + [modal_workers/shared/post_mortem_runner.py](../Conan/modal_workers/shared/post_mortem_runner.py).
- **v2 reactor / fanout edge functions:** [supabase/functions/reactor/index.ts](../Conan/supabase/functions/reactor/index.ts), [supabase/functions/fanout/index.ts](../Conan/supabase/functions/fanout/index.ts).
- **Decisions log:** [DECISIONS.md](../Conan/DECISIONS.md) (D-100+).
- **Memory files (per asset):** `memory_files/asset_<id>.md` — appended to by post_mortem_runner (Contract C5).

---

## 10. Open work (as of 2026-05-19)

Validated against live code on 2026-05-19. Items completed since the 2026-05-08 revision have been removed.

**Operator action required:**

- ⏳ **Create Modal secret `anthropic-orchestrator`** — blocks `orchestrator_app.py` deploy (DECISIONS.md D-537, D-572, D-629). Run on the Mac runner: `modal secret create anthropic-orchestrator ANTHROPIC_API_KEY=<rotated_key>`. Verify with `python modal_workers/scripts/preflight_axs05.py` (Gate 0.1 flips fail → pass). `feedback_loop_app.py` continues to use `scanner-secrets` as a temporary Anthropic key fallback (D-123) until then; a follow-up PR will add `anthropic-orchestrator` to its `secrets=[...]` list (additive — `scanner-secrets` still needed for Polygon).

**Engineering work outstanding:**

- ⏳ **v3 dashboard surfaces (D-111 Phase A).** Decision locked 2026-05-07 (12 Q&A conventions, orchestrator output contract §13). **Code not started.** Phase A scaffold (types regen, `dashboard/lib/api/`, four components: `<ConvictionDisplay />`, `<SubAgentPanels />`, `<CitationViewer />`, `<TierBadge />`) outlined in D-111 but unmerged. Blocked on: (a) backend ratification of the §13 contract, (b) new RPCs (`fda_asset_set_watch_priority`, `fda_asset_set_active`, `fda_asset_pin_reference_class`, `eval_case_open`, `eval_case_resolve`), (c) `marazuela/conan-dashboard` repo consuming the contract. In-repo `ui_v2/` is v2-only (see [ui_v2/spec.md](../Conan/ui_v2/spec.md), [tasks/ui_v2_todo.md](../Conan/tasks/ui_v2_todo.md), still gated on Pedro review of the datapoints catalog). Phase B/C/D (visual lock) deferred until Phase A ships.

**Minor / Wave-6 follow-ups (optional):**

- ⏳ **MCP server gaps (low priority).** All 8 Phase 4.7 servers exist (D-105 / RAG infrastructure); 6 are fully implemented and wired (pubmed, openfda, fda_adcomm, polygon, internal_rag, compute, clinicaltrials). Two items remain: (a) `biorxiv_mcp.py` is an intentional v1 stub returning empty results (52 lines, flagged `v1_stub_returns_empty` in `.mcp.json`) — promote to real preprint coverage when preprint demand justifies; (b) `clinicaltrials_mcp.py` is fully implemented (86 lines, real CT.gov v2 HTTP) and registered in `.mcp.json` but no sub-agent's `tool_defs` enables it yet — wire into `RegulatoryHistoryRunner` or `LiteratureRunner` when a use case lands.

- ⏳ **Stream 3 observability** (implementation is complete per [`orchestrator_runtime/runtime.py:946-1364`](../Conan/orchestrator_runtime/runtime.py) and `persist_assessment_v3` RPC; remaining is instrumentation only): (a) Wave-6 audit alert when a `post_mortem_queue` row remains `status='pending'` beyond 180d past `outcome_window_end`; (b) Stream-3 health view (assessments/day, queue depth, calibration fit quality); (c) backfill 30+ historical assessments to prime `reference_class_base_rates` Wilson-CI estimates before live calibration kicks in.
