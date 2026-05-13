# Skills v1 → v2 Comparative Document

**Comparison of `conan-cowork-skills/` (legacy / v1) vs. `Skills v2/` (new, not yet implemented)**

Generated: 2026-05-05 · Methodology-level depth · Purely descriptive

---

## 0. Executive overview

The two folders are not "version 1" and "version 2" of the same system. They are two structurally different systems that share a domain (Conan / Investment-tool research) but solve different problems:

- **`conan-cowork-skills/` (v1)** is a **runtime pipeline**. Nine markdown-only skill prompts plus eight Cowork scheduled-task wrappers. Skills drive a Supabase-backed signals → thesis → candidate workflow. Every skill is a Claude reasoning routine that calls Supabase MCP and (sometimes) shells out to Python in a sibling repo. There is no Python in this repo.

- **`Skills v2/` (v2)** is an **analyst toolkit / archive**. Thirteen self-contained skill *bundles* — each is a folder with `SKILL.md` (methodology), usually a `helpers/` directory with executable Python (12 of 13 skills; U2 is the exception), and `outputs/` (smoke-test artifacts). These are diligence, synthesis, and calibration tools meant to be invoked on a single candidate or a single batch, not to drain a queue on a schedule.

The shift is: **Claude-as-pipeline-operator → Claude-as-analyst-with-tools**. v1's skills mutate a live database every 10–15 minutes; v2's skills read primary sources, do bounded analysis, write atomic files, and stop.

---

## 1. Repository-level diff

### 1.1 Top-level layout

| Item | v1 (`conan-cowork-skills/`) | v2 (`Skills v2/`) |
|---|---|---|
| Top-level dirs | `skills/`, `wrappers/`, `reference/`, `schemas/` | `skills/` (only) |
| Top-level docs | `README.md` | `README.md`, `INDEX.md`, `STATUS.md`, `HOW_TO_USE.md`, `working_folder_CLAUDE_reference.md` |
| Build metadata | none | `skill_build_plan.json` (immutable plan), `skill_build_state.json` (per-skill completion record) |
| Total `.md` files | ~22 (skills + wrappers + reference + README) | 14 SKILL.md + 5 top-level docs + smoke-test markdown outputs |
| Total `.py` files | **0** (Python lives in sibling `marazuela/conan` repo, accessed via `$CONAN_ROOT`) | **70** across 12 of 13 skills (U2 ships methodology only — no helpers) |
| Versioning model | Git repo synced across two machines (Mac primary, second machine read-only) | Archive snapshot of a build window (2026-04-28 → 2026-04-29), 13 skills marked completed |
| Connection to runtime | Symlinked into Claude Desktop's `.claude/skills/`; pulled hourly by scheduled tasks | Standalone bundle; intended runtime is Claude session reading SKILL.md and invoking helpers |

### 1.2 Supporting infrastructure

**v1 supporting infra** (carried into the repo):
- `reference/spec.md` (2,306 lines, ~20k words, ~171KB ≈ 25–27k tokens): authoritative Conan v2 system spec (thesis structure §7.4–7.5, candidate state machine §8, emissions ledger, operator-flag vocabulary, rubric dimension mappings). Cited from inside skill bodies.
- `reference/CONAN_SCORING_METHOD.md`: defines the multi-dimensional rubric engine, 1–5 dim scale, immediate/watchlist/archive/discard banding, two-gate property (semantic + syntactic), reasoning-tag discipline `[verified]/[inferred]/[speculated]`, auto-cap mechanics. Cited by `thesis_writer`, `signal_resolver`, `challenger_retro`.
- `schemas/fda_agent_medical.json`, `fda_agent_regulatory.json`, `fda_agent_microstructure.json`: JSON Schema files used by the three FDA review wrappers for output validation.
- `wrappers/README.md`: registration pattern, `$CONAN_ROOT` env-var contract, edits flow (this repo → git push → second machine pulls → next scheduled firing picks up).

**v2 supporting infra** (carried into the repo):
- `skill_build_plan.json`: immutable plan ratified by Pedro 2026-04-29 (anti-drift lock). Defines all 13 skills, required inputs, test_candidate, dependency chain.
- `skill_build_state.json`: per-skill completion record (status, attempts, smoke-test result, notes). All 13 marked `completed` with `smoke_test_result: passed`.
- `STATUS.md`: tier-1 (validated against real data) vs. tier-2 (smoke-tested with synthetic inputs, needs live-source validation) split. 7 in tier 1, 6 in tier 2.
- `INDEX.md`: file-by-file map of the bundle.
- `HOW_TO_USE.md`: invocation pattern (read SKILL.md → gather inputs → call helpers → write to outputs/), dependency graph, profile coverage, smoke-test re-run protocol.
- `working_folder_CLAUDE_reference.md`: copy of the standing constraint set (working folder vs. reference folder, primary-source discipline, atomic writes, reversibility).

The asymmetry is informative: v1 ships *external* references (`spec.md`, scoring method) that the runtime is expected to read at every invocation; v2 ships *internal* metadata (build plan, state, status) that explains how the bundle was constructed.

### 1.3 Reference docs that exist in v1 but disappear in v2

`reference/spec.md` and `reference/CONAN_SCORING_METHOD.md` have **no analogue** in v2. v2 SKILL.md files reference paths like `Investment tool backup/02_System/engine/framework/profile_merger_arb.md`, `02_System/engine/training/historical_events_ledger.json`, `02_System/engine/docs/feedback_primary_source_discipline.md` — i.e. v2 still depends on a sibling system, but it is the `Investment tool backup/` working folder, not the Conan/Supabase project.

In other words, v1's "external dependency" is the live Conan codebase + Supabase project; v2's "external dependency" is the working folder of an offline investment research system.

### 1.4 Schemas

v1 ships three JSON-Schema files for the FDA agents (`schemas/fda_agent_*.json`); v2 ships zero JSON-Schema files. v2 enforces output structure inside SKILL.md prose (e.g. "every numeric field carries `confidence` and `source`") and inside helper Python (validation in `analyze.py`), not via external schema documents.

---

## 2. The skill inventory, side by side

### 2.1 Headline counts

| Metric | v1 | v2 |
|---|---|---|
| Skills | 9 | 13 |
| Wrappers (paste-ready scheduled-task prompts) | 8 | 0 |
| Average SKILL length | ~280 lines (range 125–762) | ~325 lines (range 237–610) |
| Skills shipping Python helpers | 0 | 12 / 13 (U2 ships methodology only, no helpers) |
| Skills with `outputs/` smoke-test artifacts | 0 | 13 / 13 |
| Schedule-driven skills | 6 (every 10/15/30 min, hourly, daily, weekly) | 0 (all on-demand) |

### 2.2 v1 skills (9)

| # | Skill | Cadence | Writes to |
|---|---|---|---|
| 1 | `signal_resolver` | every 10 min | `signals` (dims, score, band), `thesis_jobs` |
| 2 | `thesis_writer` | hourly :00 UTC | `thesis_jobs`, `candidates`, `candidate_events`, Storage |
| 3 | `thesis_challenger` | called synchronously by writer/resolver/aging/retro | (no DB writes — pure verdict) |
| 4 | `candidate_aging` | daily 06:00 UTC | `candidates`, `candidate_events`, `outcomes`, `candidate_aging_failures`, `operator_flags` |
| 5 | `coverage_auditor` | Sunday 04:00 UTC (Modal cron, not Cowork) | `operator_flags`, Storage report |
| 6 | `challenger_retro` | Sunday 09:00 UTC | `accuracy_metrics`, `operator_flags` |
| 7 | `fda_medical_review` | hourly :15 UTC | binary-catalyst review rows |
| 8 | `fda_regulatory_review` | hourly :30 UTC | binary-catalyst review rows |
| 9 | `fda_microstructure_review` | hourly :45 UTC | binary-catalyst review rows |

### 2.3 v2 skills (13)

| # | Phase ID | Skill | Tier | Profile coverage |
|---|---|---|---|---|
| 1 | U1 | `analyze-candidate-financials` | 1 (validated) | all 5 profiles |
| 2 | U2 | `compose-thesis-with-discipline` | 1 (validated) | all 5 profiles |
| 3 | U3 | `compare-to-historical-precedents` | 1 (validated) | all 5 profiles |
| 4 | U4 | `monitor-kill-conditions` | 1 (validated) | all 5 profiles |
| 5 | P1 | `analyze-fda-approval-prospects` | 2 (needs validation) | binary_catalyst |
| 6 | P2 | `research-clinical-class-precedent` | 2 | binary_catalyst (feeds P1) |
| 7 | P3 | `research-activist-filer` | 2 | activist_governance |
| 8 | P4 | `research-acquirer-history` | 2 | merger_arb |
| 9 | P5 | `analyze-litigation-expected-value` | 2 | litigation |
| 10 | P6 | `assess-takeover-vulnerability` | 2 | merger_arb (pre-edge / forward-looking) |
| 11 | M1 | `harvest-historical-events` | 1 (validated) | all 5 profiles, dispatched |
| 12 | M2 | `label-outcomes-from-prices` | 1 (validated) | all 5 profiles, dispatched |
| 13 | M3 | `extract-event-features` | 1 (validated) | all 5 profiles, dispatched |

### 2.4 Skill correspondence matrix

This is the central mapping. For each v1 skill, the closest v2 equivalent (or "no equivalent"); for each v2 skill, whether it is a renamed v1 skill, an evolution of a v1 skill, or net-new.

| v1 skill | v2 equivalent | Relation |
|---|---|---|
| `signal_resolver` | — | **Removed.** Pipeline glue (drains `thesis_jobs WHERE status='needs_scoring'`). v2 has no queue model. |
| `thesis_writer` | U2 `compose-thesis-with-discipline` | **Heavily transformed.** v1 drains queue → drafts → two gates → DB writes. v2 composes a single thesis from upstream skill outputs and refuses if six required fields are missing. |
| `thesis_challenger` | (absorbed into U2) | **Merged.** v1's adversarial-reviewer routine is replaced by v2's "refusal protocol": U2 enforces six-field discipline up front rather than drafting and challenging. |
| `candidate_aging` | U4 `monitor-kill-conditions` | **Partial overlap.** v1 does state transitions (active↔watch↔killed, mechanical Stage A + Claude Stage B with regex + challenger gate). v2 only does the kill-condition check half (read-mostly, recommendation-only, never mutates dossiers). |
| `coverage_auditor` | — | **Removed.** SQL-only weekly recall audit on `catalyst_universe` × `emissions_ledger`. No analogue in v2. |
| `challenger_retro` | — | **Removed.** Weekly precision-drift audit of `thesis_challenger`. No analogue in v2 (no challenger to audit). |
| `fda_medical_review` | P1 `analyze-fda-approval-prospects` (partial), P2 `research-clinical-class-precedent` (partial) | **Restructured.** v1 medical review is one of three parallel JSON-emitting agents per FDA event. v2 collapses medical evidence + class precedent + sponsor history into one binary-catalyst skill (P1) with a class-base-rate feeder (P2). |
| `fda_regulatory_review` | P1 (regulatory dimensions), P2 (AdCom history) | **Restructured.** v1 regulatory review (AdCom risk, CRL precedent, resubmission pathway, evidence_confidence_boost) becomes inline regulatory-risk steps in P1's methodology and class-AdCom history in P2. |
| `fda_microstructure_review` | — | **Removed.** v1 microstructure review (options liquidity, implied move, borrow cost, crowding) has no analogue in v2. |

| v2 skill | v1 ancestor | Relation |
|---|---|---|
| U1 `analyze-candidate-financials` | — | **Net-new.** No v1 skill does universal financial diligence (Sloan accruals, capital allocation, hidden value). |
| U2 `compose-thesis-with-discipline` | `thesis_writer` + `thesis_challenger` | **Replaces with different design.** v1 drafts then gates; v2 demands six fields up front and refuses if they cannot be populated. |
| U3 `compare-to-historical-precedents` | — | **Net-new.** K-NN over historical events ledger; no v1 equivalent. |
| U4 `monitor-kill-conditions` | `candidate_aging` (kill-eval portion only) | **Narrowed evolution.** Drops mechanical state transitions, drops Supabase writes, adds atomic JSONL action ledger. |
| P1 `analyze-fda-approval-prospects` | merge of `fda_medical_review` + `fda_regulatory_review` | **Consolidation.** Probability range (not point), assumption ledger, K-NN feature output for U3. |
| P2 `research-clinical-class-precedent` | (regulatory_review's class history portion) | **Net-new specialist.** Class membership via ChEMBL/openFDA, n_approvals/n_decided with Wilson CI, sparse-class detection. |
| P3 `research-activist-filer` | — | **Net-new.** Activist filer's 13D history, success rate, tier classification. |
| P4 `research-acquirer-history` | — | **Net-new.** Acquirer M&A track record, MAC clauses, regulatory outcomes by jurisdiction. |
| P5 `analyze-litigation-expected-value` | — | **Net-new.** Outcome tree × magnitude × time-to-resolution NPV. |
| P6 `assess-takeover-vulnerability` | — | **Net-new.** Pre-edge takeover candidate scoring; the only forward-looking discovery skill in v2. |
| M1 `harvest-historical-events` | — | **Net-new.** Resumable event-backfill engine for the historical ledger. |
| M2 `label-outcomes-from-prices` | — | **Net-new.** Forward-return resolver, profile-specific HIT/MISS rules. |
| M3 `extract-event-features` | — | **Net-new.** Iter-4 feature engineering with D-097 leakage check. |

**Aggregate:** 4 of 9 v1 skills have any v2 trace; 11 of 13 v2 skills are net-new; 5 v1 skills are entirely removed (signal_resolver, coverage_auditor, challenger_retro, fda_microstructure_review, and the wrapper layer in its entirety).

---

## 3. Architectural deltas

### 3.1 Runtime model

**v1: queue-drain.** Skills are activated by a scheduler (Cowork or Modal cron) every 10 minutes / hourly / daily / weekly. Each invocation reads from a Supabase queue, processes ≤5 jobs, writes back, and exits. State lives in Postgres tables (`signals`, `thesis_jobs`, `candidates`, `candidate_events`, `outcomes`, `accuracy_metrics`, `operator_flags`).

**v2: invocation-on-demand.** Skills are read by a Claude session that has been pointed at one candidate (ticker / CIK / case ID). The session loads SKILL.md, gathers inputs, calls helpers, writes to `outputs/`, and is done. State lives in atomic-written files (`<TICKER>_metrics.json`, `<TICKER>_thesis.md`, `<run_id>_events.json`, etc.). No queue; no scheduler; no daily-quota concept.

A direct consequence: v1 has rich quota discipline (15 promotions/day shared between writer and resolver, 10 invocations per FDA agent, 10-sample stratified retro, etc.). v2 has none of this. v2 is bounded by per-invocation budgets (M1 wall-clock 20s, max 150 events; M2 25s) but not by daily caps.

### 3.2 State and persistence

**v1 writes to a database.** Every skill body has explicit Supabase MCP calls (`UPDATE candidates ...`, `INSERT candidate_events ...`, `UPSERT operator_flags ...`). Idempotency, conflict resolution, and partial-failure handling are encoded in SQL semantics (`ON CONFLICT`, partial unique indexes, `last_aging_evaluated_at` filters).

**v2 writes to files.** Every skill calls `helpers/atomic_write.py` (temp file + `os.replace()`, POSIX-atomic). Idempotency comes from per-invocation `run_id` keys and resumable checkpoints. Partial failures are handled inline in the orchestrator, not by the storage layer.

The contract change: v1's "source of truth" is Postgres; v2's "source of truth" is the filesystem (`outputs/` directories under each skill). v2 explicitly notes that skills are read-only with respect to the reference folder (`Investment tool backup/`); recommendations are emitted, never archive *actions*.

### 3.3 Reasoning-tag and gate discipline

**v1's defining concept is the two-gate property.** Every promoted thesis must pass:
1. **Semantic gate** (`thesis_challenger`, drafting mode) — adversarial reviewer with a different system prompt and fresh context. Returns `confirm | challenge | kill`.
2. **Syntactic gate** (`assess_thesis_v2` RPC) — boilerplate-banned-phrase regex, reasoning-tag discipline (≥5 total tags, ≥1 `[verified]`, no boilerplate, no >2 untagged load-bearing sentences).

Challenger runs *before* syntactic. A `kill` verdict short-circuits syntactic. Two retry budgets (`attempt_count`, `challenge_count`), both max 2.

**v2 replaces this with the U2 refusal protocol.** U2 demands six required fields (variant perception, preconditions, kill criteria, expected-return distribution, time horizon + milestones, sizing inputs) each populated to an explicit bar (e.g. variant must be falsifiable + name consensus position; probabilities sum to 1.0 ± 0.01). If any field fails, U2 emits `<candidate>_thesis_REFUSED.md` with a gap list — no partial thesis.

**Observation:** v1 produces a thesis and asks the challenger to break it. v2 demands the inputs that would *make* a thesis defensible and refuses if the pre-conditions are not met. The directionality is inverted.

### 3.4 Confidence discipline

**v1** treats confidence as a per-thesis label (`confidence: low|medium|high` in the draft JSON). When `low`, the skill takes the "honest decline" path — promotes flagged with `extensions.routine_declined=true`, skips both gates, skips quota, skips email.

**v2** treats confidence as a per-row, per-numeric-field annotation. Every field in a JSON output carries `confidence ∈ [0.0, 1.0]` and `source` (URL or file path). Confidence floors trigger refusal at *the skill level* (P5 refuses when party_confidence < 0.85; P2 caps confidence at 0.50–0.60 when n_class < 5). Aggregation is harmonic-mean — the weakest input dominates.

This is a stricter, more compositional model: v1's confidence is metadata about a single output; v2's confidence is metadata about every claim, propagating through K-NN aggregations, sparse-class checks, and sidecar mismatches.

### 3.5 Refusal and decline

| Behavior | v1 | v2 |
|---|---|---|
| Refusal / decline | "Honest decline" path: stub thesis + decline_verdict, promote-flagged with `routine_declined=true`, dossier still rendered | Skill emits `*_REFUSED.md` (U2) or `error_class` response (P5 `auth_status=courtlistener_auth_required`); no synthesis written |
| Where it lives | `thesis_writer §6.5` and `signal_resolver §9.5` (pre-filter heuristics H1/H2/H3) | U2 §3 (six-field validation), P5 §1 (party_confidence_low), P2 (sparse-class flag) |
| Effect on quota | does not consume promotion quota | n/a — no quota model |
| Observability | `gate_reasons=['routine_declined_flagged']`, `prefilter_H1\|H2\|H3` | gap list inside refusal markdown; `auth_status` in JSON |

### 3.6 Adversarial review

**v1** has a dedicated `thesis_challenger` skill (191 lines) with two modes (drafting, aging), called synchronously by `thesis_writer §6.8`, `signal_resolver §11`, `candidate_aging §5.5`, and `challenger_retro §4`. Verdict is `confirm | challenge | kill`. A separate weekly `challenger_retro` skill audits the challenger's precision drift against historical outcomes.

**v2** has no challenger. Adversarial discipline is moved upstream: U2 refuses if inputs are insufficient. There is no separate reviewer routine, no aging-mode adversary, no retro audit.

This is the single biggest behavioral simplification between the two systems: v1's two-system-prompt property (drafter ≠ challenger, no shared prior) is gone. v2 trusts the skill's own refusal protocol to enforce discipline.

### 3.7 Adversarial drift / calibration

**v1** captures calibration via `challenger_retro` (per-run `accuracy_metrics` rows + `operator_flags` on threshold breach + 30-day rolling aggregator). The signal is "did the challenger correctly distinguish historical winners from losers".

**v2** captures calibration via the M1 → M2 → M3 → U3 chain. The signal is "what does the historical events ledger say about this candidate's reference class". M1 harvests events from EDGAR / FDA / CourtListener; M2 labels them HIT/MISS/PARTIAL via yfinance forward returns; M3 extracts iter-4 features with D-097 leakage check; U3 does K-NN against the candidate.

Same goal (closed-loop calibration), opposite mechanism: v1 audits Claude's reviewer; v2 audits the population of historical events. v1's signal updates `operator_flags`; v2's signal updates K-NN base rates that feed U2 sizing.

### 3.8 Primary-source discipline

Both systems take primary-source discipline seriously, but encode it differently.

**v1** enforces it through `thesis_writer §5` ("≤6 web searches; ≥1 disconfirming"; ≥1 [verified] tag required) and through `rpc_edgar_fetch` (which respects SEC User-Agent policy where `WebFetch` does not). The challenger checks at §6.8 that kill conditions map to "concrete queryable data sources (EDGAR, openFDA, CourtListener)".

**v2** enforces it through skill-level helper code. P1 calls `helpers/fetch_trial_data.py` which hits ClinicalTrials.gov directly. P3 calls `helpers/edgar_filer_history.py` which uses EFTS full-text search. P5 calls `helpers/courtlistener_client.py` (auth-required; gracefully degrades on missing token). Every output row has `source: <url-or-path>`. The discipline is structural rather than reviewed.

### 3.9 Sparse-class handling

**v1** has no equivalent concept (its statistics live in `accuracy_metrics` with explicit `tier='full'|'preview'|'insufficient'` based on per-label sample counts — pre_edge_hit ≥5, dead_catalyst ≥5, etc., for `tier='full'` flag-eligibility).

**v2** makes sparse-class detection a first-class output annotation. P2 sets `data_quality_notes: ["sparse class — base rate weakly anchored"]` and caps confidence at 0.50–0.60 when `n_class < 5`. P3 does the same for activist track records with `n_campaigns < 5`. P5 does it for precedent settlements with `sparse_class: true` and `confidence ≤ 0.30 per row`. U3 has a five-tier sparse-handling cascade (K achieved → K−1..K−4 achieved → K=2 → K=1 → refusal) with explicit confidence floors.

### 3.10 Idempotency

**v1** idempotency is encoded in:
- `last_aging_evaluated_at::date < today` (one Stage B per candidate per UTC day)
- `ON CONFLICT operator_flags_open_uniq UPDATE SET ...` (re-running same week UPDATEs flags rather than duplicating)
- `xmax=0` vs `xmax!=0` distinguishes INSERT from UPDATE for `candidate_events` event-type selection
- Two-statement RPC pattern (enqueue + collect) to work around `pg_net` in-transaction visibility deadlock (D-???, 2026-04-23)

**v2** idempotency is encoded in:
- `helpers/atomic_write.py` (temp file + `os.replace`, POSIX-atomic, near-atomic NTFS)
- `run_id` keying on outputs (re-running same `run_id` continues from checkpoint, doesn't re-process)
- M1/M2 checkpoints every 50 events
- U4 daily snapshot (whole-file atomic), not append-only (so re-runs on same `as_of_date` produce same outputs modulo timestamps)

---

## 4. Skill-by-skill methodology comparison

For the four pairs that have any structural overlap.

### 4.1 v1 `thesis_writer` ↔ v2 U2 `compose-thesis-with-discipline`

| Aspect | v1 | v2 |
|---|---|---|
| Trigger | hourly :00 UTC, drains `thesis_jobs WHERE status='queued'` | on-demand, called with explicit `candidate_id` and `supporting_skill_outputs` dict |
| Scope per invocation | up to 5 jobs (FIFO + short-positioning sub-quota) | one candidate |
| Web research | inline, ≤6 queries, ≥1 disconfirming | none — relies on upstream skill outputs (U1, P1–P6, U3) |
| Output structure | JSON draft with `situation`, `why_underpriced`, `next_catalyst`, `kill_conditions`, `steelman`, `web_research`, `structured_kill_conditions`, `confidence`, `insufficient_signal`, `primary_source_citations` | Six required fields: variant perception, preconditions, kill criteria, expected-return distribution, time horizon + milestones, sizing inputs |
| Validation | reasoning-tag count, boilerplate phrase blocklist, `assess_thesis_v2` RPC syntactic gate | Six-field discipline, probabilities sum to 1.0 ± 0.01, top-level confidence = `min(supporting_skill_confidences) × variant_anchoring_factor` |
| Fail mode | DLQ to `thesis_drafting_failures` with `all_drafts` array; or honest-decline → promote-flagged `state='watch'` | emit `*_thesis_REFUSED.md` with explicit gap list; no partial thesis written |
| Promotion | UPSERT `candidates` on `(ticker, mic)`, INSERT `candidate_events`, render markdown via RPC, upload to Storage | atomic-write `<candidate>_thesis.md` + verification block listing every upstream skill consulted with content checksum |
| Retry budget | 2 syntactic + 2 challenger | none (refusal is terminal for the run) |
| Adversarial review | yes (challenger §6.8 before syntactic) | none (refusal protocol replaces it) |

The methodologies are not compatible. v1 is generative-then-gated; v2 is precondition-checked-then-generative. The output schemas are not the same shape (v1's `kill_conditions` JSONB has `status='pending'`, etc.; v2's six-field schema has explicit scenario probability distribution).

### 4.2 v1 `candidate_aging` ↔ v2 U4 `monitor-kill-conditions`

| Aspect | v1 | v2 |
|---|---|---|
| Trigger | daily 06:00 UTC | on-demand or scheduled (`as_of_date` parameter) |
| Scope | all `state IN ('active','watch')` candidates with `last_aging_evaluated_at::date < today` | scope=`all_active` or `single` dossier |
| Stage A (mechanical) | yes — promote watch→active near catalyst, age-out 60d watch → kill, demote 30d stale active → watch, flag elapsed catalyst | no — universal checks (catalyst-date staleness, score-band drift, frontmatter status drift) but no mechanical state mutations |
| Stage B (Claude eval) | yes — for each kill_condition, determine `new_status ∈ {pending, triggered, cleared}`; recommend `kill | demote_to_watch | deliver | maintain` | yes — profile-specific kill-checkers (deal closed/withdrawn, 13D withdrawn, PDUFA decision issued, judgment entered, insider reversal) |
| Adversarial pass | yes — `thesis_challenger` aging mode on every triggered claim, before regex check | no — confidence gates instead (≥0.85 single trigger; ≥0.60 for two converging signals) |
| Regex integrity check | yes — Python `re.search` on `observable.search_pattern` vs. `signal.raw_payload + source_url` | no — primary-source clients return structured data; matching done in helper code |
| State mutations | yes — UPDATE `candidates`, INSERT `outcomes`, INSERT `candidate_events`, UPSERT `operator_flags` | no — emits archive recommendations only; never mutates dossiers |
| Quota | 15 Stage B Claude evals / day | none |
| Failure tracking | `candidate_aging_failures` table with `consecutive_failures` counter; ≥3 → `operator_flags(kind='aging_stuck')` | structured status in JSONL ledger; archive recommendations emitted when confidence high enough |
| Output | DB rows | `<YYYY-MM-DD>_kill_sweep.md` + `<YYYY-MM-DD>_actions.jsonl` |

v2 strips the state-machine portion of v1 entirely. v2 U4 is the kill-monitor *only*. The v1 lifecycle promotions/demotions/expirations are not reproduced anywhere in v2 — v2 has no `state` field on dossiers in the same sense.

### 4.3 v1 FDA review trio ↔ v2 P1 + P2

v1 splits the binary-catalyst analysis into three parallel JSON-emitting agents:
- `fda_medical_review` (~234 lines): endpoint quality, safety, effect size, class precedent, `fair_probability_modifier ±0.10`
- `fda_regulatory_review` (~154 lines): AdCom risk, staff review red flags, CRL precedent, resubmission pathway, `evidence_confidence_boost ±0.40`
- `fda_microstructure_review` (~125 lines): options liquidity, implied move (only when Polygon unavailable), borrow cost, crowding

Each is hourly-scheduled, quota 10 reviews/day each, schema-validated (the `schemas/fda_agent_*.json` files), feeds a deterministic feature-builder downstream, never sets score/band directly.

v2 collapses this into:
- **P1 `analyze-fda-approval-prospects`** (~275 lines): probability *range* (p_low, p_mid, p_high), trial-data forensics + AdCom risk + label risk + CMC risk all in one skill, with assumption ledger; output is consumed by U2 sizing
- **P2 `research-clinical-class-precedent`** (~420 lines): class membership via ChEMBL/openFDA + class approval rate with Wilson 95% CI + sparse-class detection + sponsor prior-CRL/breakthrough history. Feeds P1.

| v1 agent | What survived | Where it landed in v2 |
|---|---|---|
| `fda_medical_review` | trial forensics, endpoint integrity, safety, class outcome | P1 §1–2 (trial set + forensics), with class outcome moved to P2 |
| `fda_regulatory_review` | AdCom risk, CRL precedent, resubmission pathway, label risk | P1 §3–4 (AdCom + label risk + CMC); class AdCom history moved to P2 |
| `fda_microstructure_review` | — | **dropped.** No options-liquidity / implied-move / borrow-cost analysis in v2. |

Other deltas:
- v1 outputs JSON schema-validated payloads with bounded modifiers (`±0.10`, `±0.40`); v2 outputs probability ranges with assumption ledgers. Both are decision-support, not score-setting.
- v1 schedules hourly (`:15`, `:30`, `:45` UTC); v2 invoked on-demand per drug.
- v1 cites ≥3 primary sources via `WebFetch`/`rpc_edgar_fetch`; v2 cites ≥3 via `helpers/fetch_trial_data.py` + `helpers/adcom_history_lookup.py` + `helpers/cmc_risk_lookup.py`.

### 4.4 What had no v1 ancestor

The following v2 skills have no v1 counterpart at all:

- **U1 `analyze-candidate-financials`**: universal financial diligence (Sloan accruals, capital allocation scorecard, hidden value scanner). v1's `thesis_writer` does only ≤6 web searches; it never does balance-sheet forensics.
- **U3 `compare-to-historical-precedents`**: K-NN over historical events ledger. v1 has no equivalent reference-class lookup.
- **P3 `research-activist-filer`**, **P4 `research-acquirer-history`**, **P5 `analyze-litigation-expected-value`**: per-profile counterparty intelligence. v1's `thesis_writer` does inline web research per signal but no specialist track-record skill.
- **P6 `assess-takeover-vulnerability`**: pre-edge takeover candidate scoring. v1 has no forward-looking discovery skill at all.
- **M1, M2, M3**: the calibration trio (event harvest → outcome labeling → feature extraction). v1's calibration is via `challenger_retro` measuring challenger precision; v2's is via populating a historical events ledger with HIT/MISS labels and forward-return statistics.

---

## 5. The wrapper layer (v1 only)

v1 ships eight wrappers at `wrappers/*.md`, one per scheduled task. Each wrapper is a paste-ready Cowork prompt giving:
- which skill to invoke
- cadence (every 10 min, hourly :00, hourly :15, daily 06:00, Sunday 04:00, Sunday 09:00)
- guardrails (reset stuck rows, quota checks, two-gate property, honest-decline short-circuit)
- expected report JSON shape (every wrapper specifies an exact field set: `processed`, `promoted`, `dlq_*`, `empty_queue_exit`, etc.)

v2 has zero wrappers. There is no Cowork-scheduled-task layer in v2. v2's `HOW_TO_USE.md` describes invocation as "a Claude session reads SKILL.md, gathers inputs, calls helpers, writes to outputs/" — the wrapper concept is absent.

This is consistent with §3.1: v1 is a queue-drain pipeline (needs schedule + cadence + report shape per task); v2 is on-demand tooling (one invocation per candidate).

---

## 6. Helper-script architecture (v2 only)

v1 has zero Python files. The Conan codebase that v1 skills shell out to (`modal_workers.shared.rubric_engine.rescore_with_dims`, `modal_workers.shared.candidate_gate.assess_thesis_v2`) lives in a separate `marazuela/conan` repo accessed via `$CONAN_ROOT`.

v2 ships 70 Python helpers, organized one folder per skill (12 of 13 skills — U2 `compose-thesis-with-discipline/` is methodology-only). The pattern:

```
<skill-name>/
├── SKILL.md
├── helpers/
│   ├── analyze.py          (orchestrator entry-point — present in 9 of 13 skills)
│   ├── atomic_write.py     (shared utility — present in 11 of 13)
│   ├── <domain1>.py        (e.g. sloan_accruals, ownership_concentration, kill_checks_merger_arb)
│   ├── <domain2>.py
│   └── ...
└── outputs/
    ├── <test_case>.md
    └── <test_case>.json
```

A few notable architectural choices visible in the helpers:

- **`atomic_write.py` is duplicated** across 9 skills (each has its own copy under `helpers/atomic_write.py`). This is by design — each skill bundle is self-contained and ships independently. Skills without it (U1, P1, P2) inline the temp-file-and-rename pattern in their orchestrators.
- **`analyze.py` is the canonical orchestrator name** for the four U-skills, four of the six P-skills, and U3. M1 uses `harvest.py`, M2 uses `label.py`, M3 uses `extract.py` instead.
- **Per-profile feature extractors are split into per-profile files** for M3 (`feature_extractors_merger_arb.py`, `_activist_governance.py`, `_binary_catalyst.py`, `_insider.py`, `_litigation.py`) and U4 (`kill_checks_merger_arb.py`, etc.). The dispatch happens in the orchestrator.
- **Auth-required clients fail gracefully**, not loudly. P5's `courtlistener_client.py` returns `auth_status: "courtlistener_auth_required"` with `next_steps` instructions when the token is missing, rather than raising. This is the v2 substitute for v1's `failed_reactor_events` DLQ row.
- **The smoke-test outputs are checked in.** Each skill's `outputs/` folder contains the artifacts produced during the 2026-04-29 build run (e.g. `RPAY_financial_assessment.md`, `AXSM_thesis.md`, `2026-04-29_kill_sweep.md`, `merger_arb_merger_arb_2020-01-01_2024-12-31_51947cf8_events.json`). Tier-1 skills also have `*_verified_*` files from a second-pass live-source validation.

---

## 7. Profile model

Both systems carry a profile concept but the inventory differs.

| Profile | v1 | v2 |
|---|---|---|
| `merger_arb` | yes (5 dims: deal_pace, conditions_density, asymmetry, financing, regulatory_clarity) | yes (U1 lens, P4, P6) |
| `activist_governance` | yes (7 dims) | yes (U1 lens, P3) |
| `litigation` | yes (6 dims; 30d eval window vs. 14d standard) | yes (U1 lens, P5) |
| `binary_catalyst` | yes (5 dims) | yes (U1 lens, P1, P2) |
| `insider` | yes — handled in U2 inputs only, no dedicated counterparty skill | yes (U1 lens, no dedicated skill) |
| `short_positioning` | yes (sub-quota: scanner-config-driven daily promotion limit, default 5; profile_deferred_short_limit overflow path) | **out of scope** per build plan |
| `takeover_candidate` (forward-looking, pre-edge) | listed in CONAN_SCORING_METHOD as a profile but no v1 skill targets it | **yes** — P6 is the dedicated skill |
| `congressional_trading` | yes — but skipped via `_provenance='deferred_no_profile'` (no fitting profile) | not present |

The `short_positioning` profile is the most visible casualty: v1 treats it as a sub-quota profile with explicit deferral semantics; v2 explicitly drops it.

---

## 8. The "calibration loop" delta

This is the deepest methodological shift between the two systems.

**v1's calibration loop:**
- `coverage_auditor` (Sunday 04:00 UTC) — for every material catalyst in `catalyst_universe` last week, did `emissions_ledger` catch it pre-edge? → top-10 `coverage_miss` flags + weekly markdown.
- `challenger_retro` (Sunday 09:00 UTC) — sample 10 historically labeled candidates, re-invoke `thesis_challenger` in drafting mode, compare verdict to actual outcome → `accuracy_metrics` row + threshold-based `operator_flags`.
- 30-day rolling aggregator on `challenger_retro` fires independently when cumulative depth ≥8 samples.

The signal is "did Claude's reviewer correctly distinguish historical winners from losers". The action is operator flags (humans see them in the dashboard).

**v2's calibration loop:**
- M1 `harvest-historical-events` (resumable, profile-dispatched) — backfills events from EDGAR EFTS / FDA approvals / CourtListener / Form 4 cluster detection. Bounded budget, checkpointed every 50 events. Outputs `<profile>_<run_id>_events.json`.
- M2 `label-outcomes-from-prices` — for each event, fetch yfinance forward returns, apply profile-specific HIT/MISS rules. Outputs `<profile>_<run_id>_outcomes.json`.
- M3 `extract-event-features` — extract iter-4 features per labeled event, run D-097 leakage check, output `<profile>_<run_id>_features.json` + `<profile>_feature_dictionary.md`.
- U3 `compare-to-historical-precedents` — K-NN against the labeled, feature-extracted ledger. Returns top-K neighbors with hit_rate, median_return, similarity-weighted aggregates. Feeds U2 sizing.

The signal is "what does the population of historical precedents say about this candidate's reference class". The action is base rates that anchor U2's expected-return distribution and sizing inputs.

**Same goal, opposite end of the loop:**
- v1 audits the *reasoner* (challenger precision). v2 audits the *data* (event labeling + feature extraction).
- v1's loop runs on the operator's flag dashboard. v2's loop runs on every U2 invocation that consumes U3 output.
- v1's loop has bounded sample sizes (10 per Sunday run, stratified). v2's loop has potentially-unbounded sample sizes (M1 can backfill thousands of events; the limit is wall-clock budget per invocation, not weekly cadence).

---

## 9. Discovery vs. diligence balance

`STATUS.md` makes this explicit: of the 13 v2 skills, **only P6 is forward-looking discovery** — i.e. surfaces a candidate before scanners would pick up a definitive event. The other 12 turn an already-surfaced candidate into a defensible position (U1, P1–P5) or anchor it to base rates (U2, U3) or audit it (U4) or feed the historical ledger (M1–M3).

v1's discovery surface area is similarly thin — `signal_resolver` drains `signals` whose dims are unscored, but doesn't generate signals. The actual discovery layer in v1 lives in scanners (Modal-side, not in this repo): congressional_trading, takeover_candidate_scanner, etc.

So both systems treat scanner-driven discovery as out-of-scope for the skill layer and diligence as the skill layer's job. v2 makes this slightly more explicit by labeling P6 as "the only forward-looking discovery skill" and flagging it as the highest-leverage gap.

---

## 10. Things v2 explicitly drops

Cataloging the v1 features with no v2 representation:

1. **The two-gate property** (semantic challenger + syntactic gate). Replaced by U2's six-field refusal protocol.
2. **Reasoning-tag discipline** (`[verified]/[inferred]/[speculated]`, ≥5 tags, ≥1 verified, no >2 untagged load-bearing sentences). Replaced by per-field `confidence` + `source` annotations.
3. **The honest-decline path** (promote-flagged with `state='watch'` and `extensions.routine_declined=true`). Replaced by `*_REFUSED.md` outputs with gap lists.
4. **Daily promotion quota** (15/day shared between writer and resolver). No quota model in v2.
5. **`auto_caps_triggered`** (low-fidelity signal, widely-watched event, no asymmetry → archive band). No equivalent in v2.
6. **The pre-filter decline heuristics H1/H2/H3** (`prefilter_H1` repeat-decline within 30d; `H2` megacap-broad-class without info_asymmetry; `H3` stale-catalyst-only). No equivalent.
7. **Coverage auditing** (`coverage_auditor` writes weekly recall report + top-10 miss flags). No equivalent.
8. **Adversarial-precision auditing** (`challenger_retro`). No equivalent (no challenger to audit).
9. **FDA microstructure analysis** (options liquidity, implied move, borrow cost, crowding). Dropped entirely.
10. **The `short_positioning` profile** with its scanner-driven sub-quota and `profile_deferred_short_limit` overflow path. Out of scope.
11. **The `congressional_trading` deferral** (PTR filings → `_provenance='deferred_no_profile'`). Not present.
12. **Convergence re-drafts** (UPSERT on `(ticker, mic)` allowing multiple signals to converge on same candidate; `event_type='thesis_drafted_by_claude'` on UPDATE distinguishing from initial creation). v2 has no UPSERT model — a thesis is written once per `compose-thesis-with-discipline` invocation.
13. **State-machine semantics** for candidates (`active | watch | killed | delivered | expired` with formal lifecycle transitions). v2's U4 emits archive *recommendations* but never moves a dossier between states.
14. **The two-statement RPC pattern** (enqueue + collect, working around `pg_net` in-transaction visibility). No equivalent — v2 has no Supabase RPC use.
15. **The wrapper layer** in its entirety (eight `.md` files at `wrappers/` with cadence, guardrails, report JSON shape). No equivalent.
16. **JSON Schema files** (`schemas/fda_agent_*.json`). Output structure enforced by helper code instead.
17. **`spec.md`** (~25–27k tokens authoritative system specification). Replaced by individual SKILL.md methodology sections.
18. **`CONAN_SCORING_METHOD.md`** (rubric + banding + auto-caps). No equivalent — v2 doesn't band signals, it produces probability ranges and confidence-annotated metrics.
19. **`operator_flags` table writes**. v2 writes recommendations into output markdown; no operator-flag inbox.
20. **`accuracy_metrics` table writes**. v2 doesn't measure its own accuracy in a tracked time series.

---

## 11. Things v2 adds

1. **Per-skill Python helpers** (70 files total across 12 of 13 skills). Algorithms previously implicit in Claude reasoning are now explicit in code (Sloan accruals, Wilson confidence intervals, K-NN distance, MAC clause extractor, ownership concentration, profile-specific kill checks, etc.).
2. **Atomic-write discipline** as a shared concern (9 skills carry their own `helpers/atomic_write.py`; the rest inline equivalent logic).
3. **Confidence-on-every-field** discipline (every JSON output row carries `confidence ∈ [0.0,1.0]` and `source`). Harmonic-mean aggregation.
4. **Sparse-class detection** as a structural output annotation (`data_quality_notes`, `sparse_class: true`, confidence floors per sparsity tier).
5. **Probability-range outputs instead of point estimates** (P1 emits `(p_low, p_mid, p_high)`; P5 emits outcome tree with branch probabilities + magnitudes).
6. **Assumption ledger** (P1, P5) — every adjustment recorded with rationale, source, confidence so downstream consumers can re-evaluate.
7. **Six-field thesis discipline** with explicit refusal (U2). Variant perception, preconditions, kill criteria, expected-return distribution, time horizon + milestones, sizing inputs.
8. **K-NN reference-class lookup** (U3) with sparse-handling cascade and similarity-weighted aggregates.
9. **Resumable historical-event harvest** (M1) with `run_id`, checkpointing, multi-source dispatch (EDGAR / FDA / CourtListener / OpenFIGI / Form 4 cluster).
10. **Forward-return labeling** (M2) with profile-specific HIT/MISS rules, corporate-action fallback (NT 10-K / 8-K Item 3.01 / 5.03 / 1.02), and synthetic-return imputation only when ≥60% neighbors null.
11. **Iter-4 feature engineering** (M3) with explicit D-097 leakage check (`is_completed`, `is_terminated`, `has_results`, `why_stopped_present` for binary_catalyst).
12. **Forward-looking takeover discovery** (P6) — five-dimensional score with explicit weights (1.5, 2.0, 2.0, 1.5, 1.0), inverted defenses dimension, plausible-acquirer ranking, expected-timeline heuristic.
13. **Verification artifacts as a first-class output type** — `<test_case>_verified_<date>.md` files alongside the original outputs document a second-pass live-source validation. Tier-1 skills have these; tier-2 skills do not.
14. **Build-state tracking** (`skill_build_state.json`, `STATUS.md`). Records per-skill smoke-test result, attempts, notes; provides explicit tier-1 (validated) vs. tier-2 (needs validation) split.
15. **An immutable build plan** (`skill_build_plan.json`, anti-drift lock). Captures the original 13-skill spec ratified 2026-04-29.
16. **Online vs. offline mode** as an explicit invocation parameter on several skills (M1, M2, M3, U3, P6) — offline path uses cached / illustrative inputs for smoke tests; online path requires live primary-source access.

---

## 12. Naming, structure, and convention diffs

| Convention | v1 | v2 |
|---|---|---|
| Skill filename | `<skill>.md` (e.g. `thesis_writer.md`) | `<skill-name>/SKILL.md` (e.g. `compose-thesis-with-discipline/SKILL.md`) |
| Skill name format | snake_case | kebab-case |
| Frontmatter | YAML with `name`, `description`, `mode` (drafting\|aging), `trigger`, `quota` | YAML with `name`, `description`, `type: skill`, `when-to-use` |
| Skill body structure | Numbered steps (§1, §2, §6.5, §6.8, §8a, §8b, §8c, §8c-flagged), invariants block, examplars | Methodology sections, required inputs, outputs schema, worked example, frontmatter, references |
| Average length | ~280 lines (range 125–762) | ~325 lines (range 237–610) |
| Dependency citation | inline path references to `02_System/engine/...` (Conan repo) and `reference/spec.md` | inline path references to `Investment tool backup/02_System/engine/...` and `helpers/<file>.py` (sibling) |
| Cadence section | every wrapper specifies it (every 10m, hourly :00/15/30/45, daily 06:00, Sun 04:00/09:00) | not present — invocation is on-demand |
| Output schema | inline JSON examples, sometimes referencing the `schemas/` JSON-Schema files | inline JSON examples, schema enforced in `helpers/<orchestrator>.py` |

---

## 13. Summary table: 30-line cheat sheet

```
                            v1                              v2
runtime model               queue-drain pipeline            on-demand toolkit
unit of work                a job in thesis_jobs            a single candidate / batch
state of truth              Postgres tables (Supabase)      atomic-written files in outputs/
schedule                    every 10m / hourly / daily / weekly  none (on-demand)
quota                       15 promotions/day shared,
                              10/day per FDA agent          none
adversarial review          dedicated thesis_challenger     replaced by U2 refusal protocol
calibration loop            challenger_retro audits         M1→M2→M3→U3 audits historical
                              challenger precision           events ledger
gate model                  semantic + syntactic two-gate   six-field refusal
confidence semantics        per-thesis label                per-row, per-field annotation
sparse-class handling       tier='full|preview|insufficient'  data_quality_notes + confidence floor
refusal output              honest-decline → promote-flagged  *_REFUSED.md with gap list
discovery                   scanners (out of repo)          P6 only (forward-looking)
diligence                   inline web-research in writer   U1 + P1–P5 specialist skills
profile model               6 profiles incl. short          5 profiles, short out of scope,
                                                              takeover_candidate added (P6)
helper code                 zero (.md only)                 ~70 Python files
schemas                     3 JSON-Schema files             none (validation in code)
external reference          spec.md + scoring method        each SKILL.md is self-contained
wrappers                    8 paste-ready scheduled-tasks   none
total skills                9                               13
v1→v2 carryover             4 of 9 v1 skills                11 of 13 v2 skills net-new
```

---

## 14. Footnotes on uncertainty in this comparison

A few claims in this document depend on inference rather than direct evidence:

1. The mapping `fda_medical_review + fda_regulatory_review → P1 + P2` is structural inference based on overlapping concerns (trial forensics, AdCom history, CRL precedent, class-base rates). The two systems do not share schemas or function names, so the mapping is approximate.
2. The "U4 = candidate_aging minus state machine" mapping relies on overlap of kill-condition evaluation logic, but v1's `candidate_aging` is mechanically richer (it owns lifecycle transitions); v2's U4 is read-mostly.
3. The "v2 has no challenger" claim is based on the absence of a challenger skill in `Skills v2/skills/`. U2 enforces refusal up-front rather than gating after the fact, but a future v2 extension could reintroduce post-hoc adversarial review.
4. v2 SKILL.md files cite paths under `Investment tool backup/02_System/engine/...` — those paths were not inspected for this comparison. The "feedback_primary_source_discipline.md", "scorecard_iteration_4.md", "historical_events_ledger.json" references may carry constraints not surfaced here.
5. v1's `reference/spec.md` is ~25k tokens (2,306 lines, ~20k words); this document summarizes it at the section-headline level only. The full specification likely contains constraints on profile dimensions, candidate state transitions, and emissions-ledger semantics that this comparison did not enumerate.
