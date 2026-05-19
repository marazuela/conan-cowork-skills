# AI Tasks Overview

Single-page reference for how every AI-handled task in Conan intertwines. Companion to [SETUP.md](SETUP.md) (machine setup) and [README.md](README.md) (repo orientation).

**Last revised:** 2026-05-19. Adds the front-of-funnel `signals → fda_assets`
bridge + the new `signal_entity_resolver` skill (§3a); Stream 2 is now
scheduled via pg_cron (no longer Modal-cron-cap-blocked); open-work refreshed.
Prior revision 2026-05-08 (v3 Phase 0, D-115 → D-123).

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
                                       ▼ INSERT
                              convergence_assessments (band derived from percentile)
                                       │
                              ┌────────┴───────────┐
                              ▼                    ▼
                        fanout entry-D     post_mortem_queue
                        (band='immediate')  (resolves at outcome window)
                              │
                              ▼ render HTML/text + Storage upload + Resend + Realtime broadcast
                        operator email + asset:<id> channel
```

**Stream 2 (closed feedback loop, daily — SCHEDULED via pg_cron `v3-feedback-loop-daily` @ 02:00 UTC, migration `20260518000000_v3_feedback_loop_pg_cron.sql`; dispatches `feedback_loop_kickoff` to the `conan-v3-feedback-loop` app, bypassing Modal's free-tier cron cap):**

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

Code anchors: [conan-fda-orchestrator-plugin/skills/](../Conan/conan-fda-orchestrator-plugin/skills/) (3 sub-agent files), [modal_workers/orchestrator_app.py](../Conan/modal_workers/orchestrator_app.py), [modal_workers/feedback_loop_app.py](../Conan/modal_workers/feedback_loop_app.py), [modal_workers/shared/post_mortem_runner.py](../Conan/modal_workers/shared/post_mortem_runner.py). Decisions: D-100, D-102, D-103, D-104, D-105, D-115, D-117, D-118, D-119, D-122, D-123 in [DECISIONS.md](../Conan/DECISIONS.md).

---

## 3a. Front-of-funnel: `signals → fda_assets` bridge + resolver

§3's flow starts at `documents → asset_documents`, but FDA assets must first
*exist* in `fda_assets` for any of it to run. That seeding is the front of the
funnel:

```
binary_catalyst / fda_event signal  (scanners, e.g. pre_phase3_readout, eop2)
   │ INSERT
   ▼
reactor  → {skipped:"fda_profile_routed_to_orchestrator"}   (no v2 convergence)
   │
   ▼ trigger bridge_signal_to_v3_row()  (supabase/migrations/20260522000000)
   ├─ ticker AND drug_name AND sponsor all resolved  ──► INSERT fda_assets (seed)
   └─ otherwise  ──► operator_flags(source='bridge_signal_to_v3')   [dead-end]
                          │
                          ▼ drained by:
   signal_entity_resolver  (Cowork, every 30 min, zero API spend)
     │ recover missing field(s): SEC issuer index (sponsor→ticker),
     │ CT.gov-by-sponsor / NCT (→ drug_name), 8-K for eop2
     │ → seed fda_assets  OR  close flag with explicit exclusion reason
     │   (large-cap / foreign-unlisted / academic = correct exclusion,
     │    NOT seeded — anti-breadth, FDA-depth strategy)
     ▼
   fda_assets  ──► asset_linker_backfill links documents ──► §3 v3 flow
```

Why it matters: the `pre_phase3_readout` scanner gates emission on a US
public-issuer match (#24/#32, live in prod since 2026-05-14), so the bulk of
non-tradeable sponsors are dropped at the scanner. The bridge handles the
residue; `signal_entity_resolver` recovers genuine small/mid-caps whose sponsor
string failed fuzzy-match and explicitly excludes the rest. A one-time
2026-05-14 backfill batch of 141 flags was drained 2026-05-19 (15 seeded /
123 excluded / 3 escalated; 0 open). Steady-state inflow is low (eop2 a
few/week); the recurring task handles it.

The skill's two `operator_flags.source` values
(`signal_entity_resolver_hard_halt`, `signal_entity_resolver_run`) are added by
migration `20260529000000_signal_entity_resolver_sources.sql` (drift-proof
append; PR #90, applied to live DB out-of-band 2026-05-18, **merge pending**).
Enrollment of the `conan-signal-entity-resolver` Cowork task (cron
`*/30 * * * *`) is **pending** — must be done in the runner's Cowork session
to keep zero-API-spend.

Code anchors: [skills/signal_entity_resolver.md](skills/signal_entity_resolver.md),
`bridge_signal_to_v3_row()` in `supabase/migrations/20260522000000_v3_bridge_signal_to_fda_assets.sql`,
`pre_phase3_readout_scanner.py` (#24/#32 public-issuer gate).

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
| `operator_flags`              | translation_health, scanner_probe, convergence_qa, coverage_auditor, challenger_retro, FDA review skills; `bridge_signal_to_v3` (unseeded FDA signals → drained by signal_entity_resolver); `signal_entity_resolver_run`/`_hard_halt` (audit + kill-switch) | dashboard operator panel; bridge flags = signal_entity_resolver work queue |
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
| signal_entity_resolver        | v3 front-of-funnel | every 30 min (`conan-signal-entity-resolver`, **enrollment pending**) | 25/run, 150/UTC day | Cowork subscription, $0 API |
| bulk_orchestrator_run         | v3 Tier 2   | daily 09:00 UTC (p=1) + weekly Mon (p=2) | 50 Tier-2 runs/UTC day (~$25)         | Sonnet, $0.30–0.80/run |
| orchestrator (Tier 1)         | v3          | event-driven (`new_doc`, `cross_source`, `operator_refresh`, `tier2_escalation`) | N=7 ensemble + Tier-1 hard-kill ceiling | $10–15/run, ~3–4 min |
| post_mortem_runner            | v3 Stream 2 | **scheduled** — pg_cron `v3-feedback-loop-daily` 02:00 UTC | —                       | Haiku 4.5     |
| nightly_calibration_refit     | v3 Stream 2 | **scheduled** (same daily kickoff)    | n ≥ 200 D-103 gate                       | compute only  |
| rollback_monitor              | v3 Stream 2 | **scheduled** (same daily kickoff)    | n ≥ 30 trigger                           | compute only  |

Stream 2 was previously blocked by Modal's free-tier 5-cron cap. Resolved by
scheduling from **pg_cron** instead (`20260518000000_v3_feedback_loop_pg_cron.sql`):
a single Supabase cron at 02:00 UTC posts `{"action":"feedback_loop_kickoff"}`
to the `conan-v3-feedback-loop` multiplex, which fans out post_mortem_runner →
nightly_calibration_refit → rollback_monitor. No Modal cron slot consumed.
Rollback: `select cron.unschedule('v3-feedback-loop-daily');`.

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
| `daily_feedback_loop`          | Modal cron (when scheduled)     | Stream 2 unified entrypoint                        |
| `post_mortem_drain_dry_run`    | manual `modal run`              | Dry-run validation of Stream 2                     |
| `rollback_monitor_dry_run`     | manual `modal run`              | Dry-run validation of rollback                     |

---

## 9. Where things live

- **v2 trio + auditors + FDA review skills + `signal_entity_resolver`:** [skills/](skills/) (this repo, symlinked into `Conan/.claude/skills/` on the Mac). `signal_entity_resolver` is the v3 front-of-funnel resolver (§3a); its DB sources are added by `Conan` migration `20260529000000_signal_entity_resolver_sources.sql` (PR #90).
- **v3 sub-agents:** [conan-fda-orchestrator-plugin/skills/](../Conan/conan-fda-orchestrator-plugin/skills/) (in the `marazuela/conan` repo, NOT this one — they're Cowork plugin skills with `context: fork`, MCP tool lists, output schemas).
- **v3 orchestrator runtime:** [modal_workers/orchestrator_app.py](../Conan/modal_workers/orchestrator_app.py).
- **v3 feedback loop:** [modal_workers/feedback_loop_app.py](../Conan/modal_workers/feedback_loop_app.py) + [modal_workers/shared/post_mortem_runner.py](../Conan/modal_workers/shared/post_mortem_runner.py).
- **v2 reactor / fanout edge functions:** [supabase/functions/reactor/index.ts](../Conan/supabase/functions/reactor/index.ts), [supabase/functions/fanout/index.ts](../Conan/supabase/functions/fanout/index.ts).
- **Decisions log:** [DECISIONS.md](../Conan/DECISIONS.md) (D-100+).
- **Memory files (per asset):** `memory_files/asset_<id>.md` — appended to by post_mortem_runner (Contract C5).

---

## 10. Open work (as of 2026-05-19)

- ✅ Stream 2 scheduling — **done** via pg_cron `v3-feedback-loop-daily`
  (migration `20260518000000`), no longer blocked by the Modal cron cap.
- ⏳ Merge PR #90 (`20260529000000_signal_entity_resolver_sources.sql`,
  drift-proof; already applied to live DB). Blocked on GitHub merge auth.
- ⏳ Enroll the `conan-signal-entity-resolver` Cowork task (`*/30 * * * *`)
  in the runner's Cowork session — must run there for zero-API-spend.
- ⏳ eval_harness re-curation: the 271-case bench is corrupted by the same
  entity-resolution gap (keyword-join noise, polluted `drug_name`); blocks
  the single-shot-vs-chain backtest (D-128). See plan §1c.
- ⏳ Create Modal secret `anthropic-orchestrator` (D-123 falls back to `scanner-secrets`).
- ⏳ Build `sub_agent_options_microstructure` (Phase 5 stub).
- ⏳ 8 MCP servers planned for Phase 4.7 (PubMed, bioRxiv, openFDA, FDA AdComm, Polygon, internal RAG, compute, clinicaltrials) — `compute_mcp.py` is a stub today.
- ⏳ Stream 3: Stage 10 finalization + post_mortem_queue population.
- ⏳ v3 dashboard surfaces (Phase A foundation lift in progress per D-111).
