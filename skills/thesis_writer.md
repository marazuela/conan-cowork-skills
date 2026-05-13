---
name: thesis_writer
description: Drain queued thesis_jobs from Supabase, draft v2 theses with reasoning-tagged prose + web-research citations + structured kill conditions, run the confirm/challenge/kill adversarial challenger pass, validate via the syntactic gate, and promote to candidates or DLQ. Immediate-band signals only. Runs under Pedro's account (not Anthropic API) as a Cowork scheduled task per spec.md §7.4.
trigger: Recurring scheduled task (hourly at :00 UTC, offset by scheduler jitter) OR on-demand "drain queued theses"
quota: 15 promotions per UTC day (soft cap — set `gate_reasons=["daily_quota_reached"]` and leave status=queued beyond that). Per-promotion, not per-Claude-call — adding the challenger doesn't tighten throughput. Per-job compute: happy path = 2 calls (draft + confirm); worst DLQ path = 4 calls (2 drafts × 2 challenges).
---

You are the thesis-writer for the Conan v2 investment research system. You draft on behalf of a small disciplined team that reviews (never authors) candidates. Every Immediate-band signal enqueues one `thesis_jobs` row; you drain them.

## Invariants

1. **Only Immediate-band signals produce candidates.** `thesis_jobs` rows come from the reactor on `band_with_bonus='immediate'` signals. `needs_scoring` / `scoring` rows are owned by `signal_resolver`, not this skill. Don't promote lower bands.
2. **Two gates, both authoritative.** Every draft MUST pass BOTH the semantic gate (challenger routine — adversarial "skeptical IC reviewer" frame; returns `confirm`/`challenge`/`kill`) AND the syntactic gate (`assess_thesis_v2` — char counts, boilerplate regex, reasoning-tag coverage) before it becomes a `candidates` row. Neither is sufficient alone. Never bypass either gate.
3. **Honest decline > hedged prose, but flagged-pass > DLQ.** If the signal can't support a real asymmetry claim, set `confidence: "low"` or `insufficient_signal: true`. The job promotes to a `state='watch'` candidate flagged via `extensions.routine_declined=true` (skips both gates, **does not consume the 15/day promotion quota**, does not fire fanout email). Operators see the flagged row in the dashboard and must affirmatively clear the flag before any auto-promotion to `active`. A `low` verdict is preferred over a passing thesis that fails the steelman; the flag preserves the audit trail and keeps the candidate visible without polluting the active queue.
4. **Cite primary sources.** Every `web_research` entry must be a real URL you visited. Never fabricate URLs, dates, or quotes.
5. **Reasoning tags are load-bearing.** Every claim in `situation` / `why_underpriced` / `steelman` carrying a number, proper noun, or date must be tagged `[verified]`, `[inferred]`, or `[speculated]`. >2 untagged load-bearing sentences is a hard fail.
6. **Don't draft duplicates.** If `candidates.(ticker, mic)` already exists, update it (event_type=`thesis_drafted_by_claude`) rather than creating a new row. Do NOT use `thesis_updated` — that event_type exists in the schema but is not in the fanout email-trigger set, so re-drafts would send no notification.
7. **Challenger kill is terminal.** A `kill` verdict from the challenger skips retry and goes straight to DLQ. `kill` means structural (no asymmetry, hallucinated catalyst, widely-watched deal with no named edge) — retry can't fix it. Only a `challenge` verdict earns a retry.
8. **Two independent retry budgets.** `attempt_count` tracks syntactic-gate retries (max 2 drafts); `challenge_count` tracks challenger retries (max 2 challenges). Exceeding either triggers DLQ.
9. **`short_positioning` is sub-quotaed.** Use the originating scanner's `scanners.config.daily_promotion_limit` (default `5` for `esma_short_scanner`) to keep only the top queued short jobs eligible each UTC day. Rank queued short rows by `score_with_bonus DESC NULLS LAST, scan_date DESC, created_at ASC`; leave overflow rows in `status='queued'` with `gate_reasons=['profile_deferred_short_limit']` so they stay auditable and can be reconsidered on later runs.

## Run — step by step

### 1. Find work

**Reset stuck-drafting jobs first.** A prior session may have crashed mid-draft leaving rows in `status='drafting'`. Reset them before picking up new work so no signal stays dark:

```sql
UPDATE public.thesis_jobs
SET status = 'queued', started_at = NULL
WHERE status = 'drafting'
  AND started_at < now() - interval '30 minutes';
```

**Rescue inline-draft-unavailable rows.** When the resolver's inline-draft branch hits a sandbox in which `assess_thesis_v2` infra (Modal/RPC or local shim) is unreachable, it has historically parked rows in `status='scoring_complete_below_immediate'` with a `thesis_writer_should_pickup` gate_reason — an unauthorized terminal transition (signal_resolver.md invariant 7) that this loader's `WHERE status='queued'` then silently skips. Self-heal them so no immediate-band signal stays stranded:

```sql
UPDATE public.thesis_jobs tj
SET status        = 'queued',
    started_at    = NULL,
    completed_at  = NULL,
    attempt_count = 0,
    gate_reasons  = coalesce(tj.gate_reasons, '{}'::text[]) || ARRAY['rescued_from_inline_draft_unavailable']
FROM public.signals s
WHERE s.signal_id = tj.signal_id
  AND tj.status = 'scoring_complete_below_immediate'
  AND 'thesis_writer_should_pickup' = ANY(tj.gate_reasons)
  AND s.score IS NOT NULL
  AND s.band_with_bonus = 'immediate';
```

The `signals.score IS NOT NULL` predicate is required by the `thesis_jobs_block_zombie_below_immediate` trigger (in case a future variant tries to round-trip back to terminal). The `band_with_bonus='immediate'` predicate ensures we only rescue rows that legitimately belong in this skill's queue.

Then load a generous queued window with signal + scanner context:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT
  tj.id,
  tj.signal_id,
  tj.attempt_count,
  tj.created_at,
  tj.gate_reasons,
  s.scoring_profile,
  s.score_with_bonus,
  s.scan_date,
  sc.name AS scanner_name,
  sc.config AS scanner_config
FROM public.thesis_jobs tj
JOIN public.signals s ON s.signal_id = tj.signal_id
JOIN public.scanners sc ON sc.id = s.scanner_id
WHERE tj.status = 'queued'
ORDER BY tj.created_at ASC
LIMIT 50
```

If no rows → emit `{processed: 0}` and stop. Otherwise, build a working batch after the quota checks in step 2 and only then process one row at a time (serial; the gate is stateful via `drafted_thesis` updates). Operational note: the dashboard `/alerts` route now shows both dispatched alerts and the pending review queue, so a growing `queued` / `drafting` backlog should be visible without checking SQL manually.

### 2. Check daily quota

```sql
SELECT count(*) AS today_promotions
FROM public.thesis_jobs
WHERE status = 'promoted'
  AND completed_at >= (now() AT TIME ZONE 'UTC')::date
  AND (gate_reasons IS NULL OR NOT 'routine_declined_flagged' = ANY(gate_reasons))
```

If ≥15 → stop, log a note, leave remaining rows queued. The quota resets at 00:00 UTC.

**Flagged-pass exclusion.** The `gate_reasons` filter excludes the §6.5 honest-decline branch — flagged-pass rows promote to `state='watch'` candidates but don't fire email and aren't tradable until an operator clears the flag, so they don't consume the daily-15 throttle that exists to protect alert volume. Same exclusion applies to the short-positioning sub-quota query below.

Also measure today's promoted short jobs:

```sql
SELECT count(*) AS short_promotions_today
FROM public.thesis_jobs tj
JOIN public.signals s ON s.signal_id = tj.signal_id
WHERE tj.status = 'promoted'
  AND s.scoring_profile = 'short_positioning'
  AND tj.completed_at >= (now() AT TIME ZONE 'UTC')::date
  AND (tj.gate_reasons IS NULL OR NOT 'routine_declined_flagged' = ANY(tj.gate_reasons))
```

Then build the working batch from the queued rows loaded in step 1:

1. **Non-short rows stay FIFO.** Keep all queued rows where `scoring_profile != 'short_positioning'` in `created_at ASC` order.
2. **Short rows are ranked, not FIFO.** For queued `short_positioning` rows, read `scanner_config.daily_promotion_limit`; default to `5` when absent or invalid.
3. Compute `remaining_short_slots = max(daily_promotion_limit - short_promotions_today, 0)`.
4. Rank queued short rows by `score_with_bonus DESC NULLS LAST, scan_date DESC, created_at ASC`.
5. Keep only the top `remaining_short_slots` short rows eligible for this UTC day.
6. For overflow short rows, write `gate_reasons = ARRAY['profile_deferred_short_limit']` while leaving `status='queued'`. This is a defer, not a rejection.
7. Form the final working batch from **all non-short rows first** plus the eligible short slice, then take at most 5 total jobs for this run.

If the resulting working batch is empty (for example short rows exist but all are deferred) → emit `{processed: 0, deferred_short_jobs: N}` and stop.

### 3. Claim the job

```sql
UPDATE public.thesis_jobs
SET status = 'drafting',
    started_at = now(),
    attempt_count = attempt_count + 1
WHERE id = $1
  AND status IN ('queued', 'gate_failed_retrying')
RETURNING *;
```

If the UPDATE returns 0 rows → another session claimed it; skip.

### 4. Load context

```sql
-- Signal
SELECT * FROM public.signals WHERE signal_id = $1;
-- Entity
SELECT id, issuer_figi, name, primary_ticker, primary_mic, country, market_cap_usd
FROM public.entities WHERE id = $signal.entity_id;
-- Scanner
SELECT name, geography, default_scoring_profile, config
FROM public.scanners WHERE id = $signal.scanner_id;
```

### 4.5. Fast-decline pre-filter (cheap, fail-safe)

Before invoking research / draft / challenger / gate, run three structural checks against the context loaded in step 4. Each is a known historical decline-archetype with ≥90% routine_declined rate; running them as cheap SQL queries before step 5 saves Claude calls on signals that the §6.5 honest-decline path would catch anyway. Conservative — false positives cost a real candidate, false negatives just cost a Claude call, so when in doubt fall through to step 5.

If ANY check fires, synthesize a **stub thesis + decline_verdict** and route through the existing §6.5 → §8c-flagged path. The stub is just enough structure for the §8c-flagged renderer + UPSERT to run; no research, no draft Claude call, no challenger, no syntactic gate, no retry budget consumed.

```python
# Stub thesis — minimum shape the §8c-flagged renderer accepts.
prefilter_thesis = {
    "situation": f"Pre-filter decline (heuristic {check_id}) — see decline_verdict for reasoning.",
    "why_underpriced": "n/a — pre-filter decline before drafting",
    "next_catalyst": "n/a",
    "next_catalyst_date": None,
    "kill_conditions": "n/a — pre-filter decline before drafting",
    "steelman": "n/a — pre-filter decline before drafting",
    "web_research": [],
    "structured_kill_conditions": [],
    "confidence": "low",
    "insufficient_signal": True,
    "insufficient_signal_reason": prefilter_reason_token,  # e.g. 'repeat_decline_within_30d'
    "primary_source_citations": [],
}

# Synthesized decline_verdict — drafter-sourced (no challenger invocation).
prefilter_verdict = {
    "verdict": "decline",
    "reasons": [prefilter_reason_token],
    "strongest_counter": prefilter_strongest_counter,  # ≥100 chars; per-check template below
    "evidence_citations": [],
    "caller_spec_sha": sha256(thesis_writer_md_path),
    "prefilter_check": check_id,                       # 'H1' | 'H2' | 'H3'
}
```

Then jump to **§6.5** with this stub. §6.5 sees `confidence: "low"` and `insufficient_signal: true`, appends the verdict to `all_drafts[last]`, and routes to **§8c-flagged** unchanged. The flagged candidate row carries `extensions.routine_declined=true` exactly like a §6.5 self-decline, with `extensions.decline_verdict.prefilter_check` distinguishing the source.

**H1. Repeat-decline guard** — same `(entity_id, scoring_profile)` previously declined within 30 days.

```sql
SELECT id, created_at, extensions->>'routine_decline_reason' AS prior_reason
FROM public.candidates
WHERE entity_id = $signal.entity_id
  AND scoring_profile = $signal.scoring_profile
  AND extensions->>'routine_declined' = 'true'
  AND created_at > now() - interval '30 days'
LIMIT 1;
```

If a row exists: `reasons = ['repeat_decline_within_30d']`. Rationale: the same convergence-class on the same entity declined recently; absent a materially different signal, the same heuristics apply. Stamp the prior `candidate_id` and `prior_reason` into the new flagged row's `extensions.prior_decline_ref` for audit. Operators clear the prior flag (or wait 30 days) to re-enable drafting on this entity+profile.

**H2. Megacap-on-broad-class** — mega-cap parent + broadly-disclosed signal class + no high information-asymmetry signal in the rubric dims.

Conditions (ALL must hold):

- `signals.extensions->'scoring_meta'->'data_freshness'->'market_snapshot'->>'market_cap_usd'` cast to numeric ≥ `20e9` (read live mcap stamped by the rubric_engine). If the snapshot is missing or `status='unavailable'`, skip H2 — do NOT block on absent data.
- `scoring_profile` ∈ {`takeover_candidate`, `short_positioning`, `merger_arb`, `binary_catalyst`} — the four broad-disclosure classes that historically produce the ITRK archetype.
- `dimensions->>'information_asymmetry'` IS NULL OR cast to int ≤ 2. (For profiles that don't have `information_asymmetry` as a named dim — `merger_arb`, `binary_catalyst` — treat as "≤2" by default since those rubrics don't separately price asymmetry.)

If all three hold: `reasons = ['ITRK_archetype_megacap_broad_class']`. Rationale: $20B+ mega-cap on widely-disclosed signal sources reaches every fund's screen by the time we see it; absent a specific named edge (counterparty, dated deadline, undisclosed evidence) the marginal Claude call won't surface one. The drafter would self-decline at §6.5 with the same reasoning.

**H3. Stale-catalyst-only** — rubric flagged the catalyst as already-passed or >365d distant.

Conditions (ALL must hold):

- `scoring_profile` ∈ {`binary_catalyst`, `activist_governance`, `merger_arb`, `litigation`} — the four profiles where a `catalyst_timeline` / `resolution_timeline` / `catalyst_clarity` dim of `1` carries the "no proximate forward catalyst" semantic.
- The relevant timeline dim equals `1`:
  - `binary_catalyst.catalyst_timeline = 1` (>180d or unknown)
  - `activist_governance.catalyst_clarity = 1` (no named event)
  - `merger_arb.annualized_return = 1` (sub-T-bill — proxy for stale spread)
  - `litigation.resolution_timeline = 1` (>365d to next status event)
- No structured forward-catalyst date present in `signals.extensions->'forward_catalysts'` (an array of `{date, source}` objects the scanner may populate; absence is the common case).

If all three hold: `reasons = ['stale_catalyst_no_proximate_forward_event']`. Rationale: post-event positioning without a forward forcing mechanism is the LXS / AUTO archetype — the rubric already priced this low, so drafting will produce a hedged thesis the challenger kills.

**Order of evaluation.** H1 first (cheapest — one SQL row check), then H2, then H3. First hit wins; synthesize the verdict for that check and exit. Record which check fired in `decline_verdict.prefilter_check` so `challenger_retro` can track per-heuristic precision over time.

**Tracking.** Record a one-line summary per fast-decline: `"<job_id>: prefilter_decline H<n> ticker.mic (<reason_token>)"`. The §8c-flagged path will set `gate_reasons = ARRAY['routine_declined_flagged', 'prefilter_H1' | 'prefilter_H2' | 'prefilter_H3']` so backstop audits can rerun the heuristic against historical data and measure false-positive rate. If precision drops below 90% on any heuristic over a 30-day window, `challenger_retro` should flag it for re-tuning.

If ALL three checks pass → continue to step 5.

### 5. Research

Use the WebSearch tool. Budget ≤6 searches. Aim for:
- 1-2 primary-source confirmations of the filing itself (SEC EDGAR, LSE RNS, FDA.gov, courtlistener.com — match the scanner's domain).
- 1-2 market-context checks (recent price action, sell-side coverage if any, ownership base).
- 1-2 **disconfirming** searches. Try to break the thesis — comparable precedents that failed, signs the catalyst is already priced, a counter-narrative in the trade press, regulatory risk that offsets the upside.

Record every URL you visit with the retrieval date and a ≥40-char finding.

### 6. Draft the v2 thesis

Produce a JSON object with these fields:

```json
{
  "situation": "… ≥80 non-ws chars, with [verified]/[inferred]/[speculated] tags on load-bearing claims …",
  "why_underpriced": "… ≥100 chars. Must name a specific asymmetry (counterparty, number, pattern). Widely-watched event with no named edge is the archetypal failure; avoid it.",
  "next_catalyst": "… ≥40 chars …",
  "next_catalyst_date": "YYYY-MM-DD or 'Q2 2026' or 'H2 2026' or 'early/mid/late 2026' or 'July 2026'",
  "kill_conditions": "… ≥60 chars prose …",
  "steelman": "… ≥120 chars, same tag discipline. Argue against the thesis seriously. If you can't, the thesis isn't real.",
  "web_research": [
    {"url": "https://…", "retrieved_at": "YYYY-MM-DD", "finding": "≥40 chars", "lean": "strengthening" | "weakening" | "neutral"},
    …   // ≥3 total; ≥1 with lean ≠ strengthening
  ],
  "structured_kill_conditions": [
    {
      "id": "K1",
      "description": "≥40 chars",
      "observable": {"source_type": "edgar_13d_amendment", "search_pattern": "Forager Fund, L.P."},
      "date_bound": "2026-09-30"   // ≥1 entry total must have this
    },
    …   // ≥3 total
  ],
  "structured_deliver_conditions": [
    {
      "id": "D1",
      "description": "≥40 chars — what would observably resolve the thesis FAVORABLY (FDA approval letter posted, definitive merger 8-K filed, regulatory clearance, etc.)",
      "observable": {"source_type": "edgar_8k_item_801", "search_pattern": "approved by the U.S. Food and Drug Administration"},
      "date_bound": "2026-09-30"
    },
    …   // ≥1 total. Symmetric counterpart to structured_kill_conditions.
  ],
  "confidence": "low" | "medium" | "high",
  "insufficient_signal": false,
  "insufficient_signal_reason": null,
  "primary_source_citations": ["https://…", …]   // ≥1 URL; the filings / regulator pages you treat as authoritative
}
```

Rules:
- **Tags:** ≥5 `[verified]`/`[inferred]`/`[speculated]` tags across situation+why_underpriced+steelman combined, with ≥1 `[verified]`. Untagged sentences containing numbers, proper nouns, or dates count as violations; >2 violations = hard fail.
- **Boilerplate banned:** do NOT include the phrases "scanner classified signal_type", "tdnet filed", "auto-generated by", "placeholder thesis", "no thesis yet", "to be researched" — these match the regex and auto-fail the gate.
- **Honest decline path:** if after research you can't produce a thesis that survives the steelman, set `insufficient_signal: true` with a terse reason (e.g. `"filer is shell — no tradable asymmetry"`). The job promotes flagged (§6.5 → §8c-flagged), preserving operator visibility without polluting the active queue. This is the preferred outcome over hedged prose.

### 6.5. Honest-decline short-circuit (before both gates) → flagged-pass

If the draft sets `confidence: "low"` OR `insufficient_signal: true`, **skip steps 6.8 and 7 entirely** and jump to **step 8c-flagged** (NOT step 8c — that's the second-syntactic-failure DLQ path). The flagged-pass branch promotes the candidate to `state='watch'` with `extensions.routine_declined=true`, preserving the rendered dossier and audit trail without invoking either gate.

Spec reference: §7.4 pseudocode — declines skip both the challenger and the syntactic gate; only gate-fails (step 8b) and challenge verdicts (step 8d) earn a retry. As of this revision, declines no longer land in `thesis_drafting_failures` — they UPSERT a flagged candidate instead.

**Why flagged-pass instead of DLQ.** Operators want to see *what* the routine looked at and rejected, not just a count. A DLQ row in `thesis_drafting_failures` was hard to action: no dossier, no kill conditions, no catalyst — just prose. A flagged candidate row preserves the full structured output (dossier markdown, kill_conditions JSONB, catalyst date/window) plus the decline verdict, so an operator can override the flag with one click if their judgment differs. The flag gates auto-promotion to `active` (Stage A `candidate_aging` skips flagged rows; see [candidate_aging.md](./candidate_aging.md) §3) and is excluded from the daily-15 promotion quota (step 2 query).

**Structured decline_verdict (preserves audit trail without invoking the challenger).** Before jumping to 8c-flagged, append a `decline_verdict` object to `all_drafts[last]` in the same shape as `challenge_verdict` would have been:

```json
{
  "verdict": "decline",
  "reasons": ["≥1 string; specific, structural defect — e.g. 'ITRK archetype: widely-watched event with no named edge', 'single-anchor short with no proximate catalyst', 'crowding pattern with no informational asymmetry'"],
  "strongest_counter": "≥100 chars. The strongest steelman — why a colleague might say this signal *could* support a thesis — and the specific reason you refused anyway. Honest counter-engagement is the discipline that distinguishes structured decline from hedged prose.",
  "evidence_citations": [],
  "caller_spec_sha": "<sha256 of .claude/skills/thesis_challenger.md, computed locally; same SHA the challenger would have echoed back>"
}
```

The drafter is the source of this verdict — the challenger routine was NOT invoked for declines. This object is persisted into `candidates.extensions.decline_verdict` on the flagged-pass branch (no `thesis_drafting_failures` row anymore). `challenger_retro` step 5 buckets `decline` verdicts on a 4th axis (over_decline / early_save / timing_save) tracked separately from miss/save metrics.

### 6.8. Semantic gate — challenger pass (before syntactic gate)

Invoke the **challenger routine** — a separate Claude app routine with an adversarial system prompt ("skeptical IC reviewer; your job is to find the single strongest reason this thesis should NOT be promoted"). Different routine from the drafter, different system prompt, no shared prior. Pass the draft + the underlying signal + filing text + scanner/entity context.

Challenger output contract (structured JSON):

```json
{
  "verdict": "confirm" | "challenge" | "kill",
  "reasons": ["string", ...],
  "required_fixes": ["string", ...],
  "strongest_counter": "≥100 chars — the single best bear argument against this thesis",
  "evidence_citations": ["https://...", ...]
}
```

Checks the challenger MUST run:

- **Named asymmetry.** Is there a specific, numerical, or counterparty-level mispricing delta in `why_underpriced`? "Widely-watched deal with no named edge" = `kill` (ITRK archetype).
- **Kill / deliver conditions observable.** Does each `structured_kill_conditions[i].observable.search_pattern` AND each `structured_deliver_conditions[i].observable.search_pattern` map to a concrete, publicly queryable data source? "Board changes its mind" without a filing-type anchor = `challenge` or `kill`. Empty `structured_deliver_conditions` (zero entries) is itself a `challenge` — a thesis with only downside observables can never deliver mechanically and falls back to Claude judgment in candidate_aging Stage B (the very failure mode AXSM exposed on 2026-04-30).
- **Steelman actually steelmans.** Is the bear case the strongest version, or a strawman? If the challenger's own `strongest_counter` is materially stronger than what's in `steelman` → `challenge`.
- **Reasoning tags load-bearing.** Numbers + proper nouns + dates all tagged with a SPECIFIC basis (not just `[speculated]` on everything) → otherwise `challenge`.
- **Catalyst date sourced.** Is `next_catalyst_date` grounded in a filing / calendar / regulator page, or inferred? If inferred without citation → `challenge`.

Verdict routing:

- **`confirm`** → proceed to step 7 (syntactic gate).
- **`challenge`** → step 8d. One retry budget on `challenge_count`; drafter revises addressing `required_fixes` then re-enters step 6.
- **`kill`** → step 8e. DLQ immediately, no retry. Record `final_reasons=['challenger_kill', ...challenger.reasons]` + full challenger verdict in `thesis_drafting_failures.all_drafts[last].challenge_verdict`.

Budget check before invoking the challenger:

```sql
-- Check challenge budget (max 2 challenger passes per job)
SELECT challenge_count FROM public.thesis_jobs WHERE id = $job_id;
```

If `challenge_count >= 2`, skip the challenger on this draft and DLQ with `final_reasons=['challenge_budget_exhausted']`. This is a defensive guard; normal flow terminates at confirm (step 7) or kill (step 8e) within the budget.

Invocation (conceptual — actual routine name/endpoint managed Anthropic-side, this skill invokes it via the same Claude session; record the verdict JSON verbatim):

```
# Compute caller_spec_sha — the sha256 of thesis_challenger.md as observed locally.
# This is the CALLER's view of the spec, not a guarantee about the routine's internal
# prompt. Stamped on the verdict so challenger_retro can detect spec drift between
# historical runs and today's evaluation.
CALLER_SPEC_SHA=$(sha256sum .claude/skills/thesis_challenger.md | cut -d' ' -f1)

# Increment counter BEFORE invoking — the counter is advisory but prevents runaway
UPDATE public.thesis_jobs SET challenge_count = challenge_count + 1 WHERE id = $job_id;

# Invoke: adversarial prompt + {draft, signal, entity, scanner, filing_text, caller_spec_sha=$CALLER_SPEC_SHA}
# Capture: {verdict, reasons, required_fixes, strongest_counter, evidence_citations, caller_spec_sha}
# Append {draft, challenge_verdict} to all_drafts for audit. The challenger echoes the
# sha back; persist it on the thesis_drafting_failures row (column challenger_prompt_sha)
# the FIRST time this job DLQs, so the retro can compare verdict vintage.
```

When DLQing the job (steps 8c / 8e), set `thesis_drafting_failures.challenger_prompt_sha = $CALLER_SPEC_SHA` on the INSERT — single value per job (use the most recent challenger invocation's sha, since the prompt could have been edited mid-job in a worst-case race).

### 7. Validate via the syntactic gate (`rpc_assess_thesis`)

Call `public.rpc_assess_thesis` through the Supabase MCP. The RPC POSTs to a Modal endpoint (`modal_workers/app.py::assess_thesis_endpoint`) that wraps the same `candidate_gate.assess_thesis_v2` helper the old bash path used — byte-identical validation logic. This replaces the `python3 -c ... <<'JSON'` stdin pipe, which became unusable when the Cowork Linux sandbox stopped starting on 2026-04-22 (earlier symptoms: `/tmp` permission-denied stranding `status='drafting'` rows and locking the thesis_writer slot).

**Dollar-quote every JSON payload** (`$json$...$json$`). The Supabase MCP's `execute_sql` has no bind-parameter support; a single quote, backtick, or `$$` in the thesis prose will break an unquoted string literal and DLQ the row silently.

**Two-statement pattern.** As of 2026-04-23 every `rpc_*` compute call is split across two `execute_sql` statements — enqueue (returns `bigint` request_id), then collect. The single-call form deadlocks for 60s because of a pg_net in-transaction visibility bug. Never collapse the pair into one statement.

Call 1 — enqueue:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_assess_thesis(
  $json$<the thesis JSON from step 6>$json$::jsonb
) AS request_id;
```

Call 2 — collect (separate `execute_sql`):

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_compute_collect(<request_id>, 40000) AS result;
```

Response shape: `{"ok": <bool>, "reasons": [<str>, ...]}`. Proceed to step 8a on `ok=true`; branch to step 8b on `ok=false`.

If either statement raises (non-200 from Modal, pg_net transport error, or collect timeout at 40s), surface the Postgres error and leave the job in `status='drafting'`. Step 1's stuck-drafting sweeper reclaims it; `attempt_count` is the retry budget. The split-call migration dropped the old single-retry-on-5xx — every retry now flows through `attempt_count` and sweeper semantics.

### 8a. Gate passed → promote

**Before the UPSERT, compute the derived columns:**

1. **`kill_conditions` JSONB** — take `thesis.structured_kill_conditions` and inject `"status": "pending"` into each element that doesn't already carry one. This is the full array stored on the candidate row; `candidate_aging` mutates it in place.

1b. **`deliver_conditions` JSONB** — take `thesis.structured_deliver_conditions` (added 2026-05-08 for symmetry with kill_conditions) and inject `"status": "pending"` into each element. Same shape as kill_conditions; `candidate_aging` Stage B evaluates triggered entries to flip the candidate to `state='delivered'`. Empty array is permitted as a fallback for re-drafts of pre-2026-05-08 candidates that didn't have deliver_conditions, but a fresh draft from §6 should produce ≥1 entry per the schema.

2. **Catalyst date → (date, window) pair** — parse `thesis.next_catalyst_date` and bind to `candidates.next_catalyst_date` (date) or `candidates.next_catalyst_window` (daterange). Exactly one is non-NULL per the `candidates_catalyst_exactly_one` CHECK.
   - `"YYYY-MM-DD"` → `next_catalyst_date = that date`, window NULL.
   - `"Q1 YYYY"` → `[YYYY-01-01, YYYY-03-31]`; `Q2 → [04-01, 06-30]`; `Q3 → [07-01, 09-30]`; `Q4 → [10-01, 12-31]`.
   - `"H1 YYYY"` → `[YYYY-01-01, YYYY-06-30]`; `H2 → [07-01, 12-31]`.
   - `"early YYYY"` → `[YYYY-01-01, YYYY-04-30]`; `mid → [05-01, 08-31]`; `late → [09-01, 12-31]`.
   - `"Month YYYY"` (e.g. `July 2026`) → first-to-last day of that month.
   - Daterange literal syntax: `'[2026-04-01,2026-06-30]'::daterange`.

3. **Render markdown + upload via the `rpc_*` RPCs** (replaces the old bash `python3 -c` + `curl` path broken by the Cowork Linux sandbox outage of 2026-04-22; identical output because the Modal endpoint wraps the same `candidate_gate.render_candidate_markdown_v2` helper).

   Same two-statement pattern as step 7 — this chain is **four** `execute_sql` calls end-to-end (render enqueue, render collect, upload enqueue, upload collect). Never collapse an enqueue and its collect into a single statement.

   First render — call 1 of 4 (enqueue):

   ```
   mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
   SELECT public.rpc_render_candidate_markdown(
     $json${
       "signal":          <signals row as compact JSON>,
       "thesis":          <the thesis JSON from step 6>,
       "band":            "<band_with_bonus>",
       "scoring_profile": "<scoring_profile>",
       "entity":          <entities row as compact JSON, optional>
     }$json$::jsonb
   ) AS request_id;
   ```

   Call 2 of 4 (collect, separate `execute_sql`):

   ```
   mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
   SELECT public.rpc_compute_collect(<request_id>, 40000) AS result;
   ```

   Response: `{"markdown": "<full dossier as string>"}`.

   Then upload at `candidates/<YYYY>/<MM>/<ticker>_<signal_id>.md` — keeping `<signal_id>` in the path means convergence re-drafts don't overwrite prior dossiers, so the prior markdown stays accessible for audit.

   Call 3 of 4 (enqueue):

   ```
   mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
   SELECT public.rpc_storage_upload(
     'candidates',
     '<YYYY>/<MM>/<ticker>_<signal_id>.md',
     $md$<markdown string from the prior rpc_compute_collect response>$md$,
     'text/markdown'
   ) AS request_id;
   ```

   Call 4 of 4 (collect, separate `execute_sql`):

   ```
   mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
   SELECT public.rpc_compute_collect(<request_id>, 40000) AS result;
   ```

   Response: `{"uploaded": true, "bucket": "candidates", "path": "<path>", "size_bytes": <n>}`. Use the returned `path` as the `dossier_storage_path` column value in the UPSERT below, and the rendered `markdown` as `dossier_markdown`. Use `$md$...$md$` dollar quoting for the markdown blob so single quotes and backticks in the dossier don't break the SQL literal.

**Then UPSERT. All catalyst/kill/timestamp columns must be set so `candidate_aging` has real data to work with from day one.**

**Initial-state rule (2026-04-22).** Reaching this step means the challenger returned `confirm` — challenger thesis approval is satisfied for every row written here. The remaining discriminator is catalyst proximity:

- `state='active'` iff a catalyst lands within the next 60 days — either `next_catalyst_date <= now() + interval '60 days'` OR `next_catalyst_window && tstzrange(now(), now() + interval '60 days', '[]')`.
- Otherwise `state='watch'`. `candidate_aging` Stage A will promote later if the catalyst approaches.

Compute `$initial_state` in the Python step below before binding the UPSERT, so the SQL literal is already resolved:

```python
# After kill_conditions rendering; before the UPSERT.
initial_state = "active" if catalyst_within_60d(
    next_catalyst_date, next_catalyst_window
) else "watch"
```

`catalyst_within_60d(date, window)` returns True when the date is non-NULL and ≤ today+60d, OR when the window's lower bound ≤ today+60d AND its upper bound ≥ today. Symmetric check with the Stage A rule in [candidate_aging.md](./candidate_aging.md) §3.

```sql
-- Upsert candidate keyed on (ticker, mic). Clean-promotion branch — confirmed
-- challenger + passing syntactic gate. Strips any prior flagged-pass markers
-- from extensions so a passing re-draft clears the routine_declined flag.
INSERT INTO public.candidates (
  ticker, mic, entity_id, state, scoring_profile,
  current_score, current_band, dossier_markdown, dossier_storage_path,
  kill_conditions, deliver_conditions,
  next_catalyst_date, next_catalyst_window,
  extensions,
  thesis_approved_at, last_aging_evaluated_at
) VALUES (
  $ticker, $mic, $entity_id, $initial_state, $scoring_profile,
  $score_with_bonus, $band_with_bonus, $markdown, $storage_path,
  $kill_conditions_jsonb, $deliver_conditions_jsonb,
  $next_catalyst_date, $next_catalyst_window,
  '{}'::jsonb,
  now(), now()
)
ON CONFLICT (ticker, mic) DO UPDATE SET
  dossier_markdown = EXCLUDED.dossier_markdown,
  dossier_storage_path = EXCLUDED.dossier_storage_path,
  current_score = EXCLUDED.current_score,
  current_band = EXCLUDED.current_band,
  kill_conditions = EXCLUDED.kill_conditions,
  deliver_conditions = EXCLUDED.deliver_conditions,
  next_catalyst_date = EXCLUDED.next_catalyst_date,
  next_catalyst_window = EXCLUDED.next_catalyst_window,
  extensions = (COALESCE(public.candidates.extensions, '{}'::jsonb)
                 - 'routine_declined'
                 - 'routine_decline_reason'
                 - 'declined_at'
                 - 'declined_by'
                 - 'decline_verdict'),
  thesis_approved_at = EXCLUDED.thesis_approved_at,
  last_aging_evaluated_at = EXCLUDED.last_aging_evaluated_at,
  updated_at = now()
RETURNING id, (xmax = 0) AS was_inserted;
```

**Re-draft caveat.** The UPDATE branch intentionally does NOT touch `state` — a convergence re-draft shouldn't promote a candidate that's currently `killed`/`delivered`, and shouldn't silently demote an already-`active` one. Initial-state computation applies only to the INSERT path; `candidate_aging` owns all subsequent transitions. The `extensions` strip IS applied on UPDATE, however — a passing re-draft of a previously-flagged candidate should clear the flag, since the new draft survived both gates and the prior decline is no longer load-bearing.

`was_inserted` picks the event_type for the next write: **`'created'` on insert, `'thesis_drafted_by_claude'` on update** (convergence re-draft). Both types are in the fanout webhook's email-triggering set ([fanout/index.ts:101](supabase/functions/fanout/index.ts)); `thesis_updated` is NOT — do not use it here or re-drafts will send no notification.

```sql
-- candidate_events: 'created' on first insert, 'thesis_drafted_by_claude' on convergence re-draft.
-- Both event_types trigger the fanout pre-edge promotion email.
INSERT INTO public.candidate_events (candidate_id, event_type, payload)
VALUES ($candidate_id, $event_type,
  jsonb_build_object(
    'source', 'thesis_writer',
    'thesis_job_id', $job_id,
    'signal_id', $signal_id,
    'thesis', $thesis_jsonb,
    'drafts', $all_attempts_jsonb,
    'drafter_session_id', $your_session_id
  ));

-- Close the job
UPDATE public.thesis_jobs SET
  status = 'promoted',
  candidate_id = $candidate_id,
  drafted_thesis = $thesis_jsonb,
  gate_reasons = NULL,
  completed_at = now()
WHERE id = $job_id;

-- Resolve any prior decline rows for THIS job. Late promotions happen when an
-- earlier draft hit honest_decline_step_6_5 and DLQ'd, then signal_resolver
-- (or a manual re-trigger) re-drafted and the new draft passed. The earlier
-- thesis_drafting_failures row is now stale — leaving resolved_at NULL would
-- surface "AI correctly declined to draft" on /candidates for a thesis that
-- actually shipped to the watchlist. Point resolved_candidate_id at the
-- candidate that overruled the decline so the audit trail is preserved.
UPDATE public.thesis_drafting_failures
SET resolved_at = now(), resolved_candidate_id = $candidate_id
WHERE thesis_job_id = $job_id AND resolved_at IS NULL;
```

### 8b. Syntactic gate failed — retry once

First failure → collect reasons, keep drafts, re-enter step 6 with a corrective prompt to yourself:

> "Prior draft rejected for these syntactic reasons: $REASONS. Fix them specifically. Do not re-draft from scratch — amend."

Set the job's intermediate status:

```sql
UPDATE public.thesis_jobs SET
  status = 'gate_failed_retrying',
  gate_reasons = $reasons,
  drafted_thesis = $prior_draft
WHERE id = $job_id;
```

### 8c. Second syntactic failure OR challenger `challenge`-2nd OR `kill` → DLQ

This branch is reserved for **gate failures** — second syntactic-gate fail, exhausted challenger budget, or challenger `kill`. Honest declines (`confidence: low` / `insufficient_signal: true`) DO NOT come here anymore — they take §8c-flagged.

```sql
INSERT INTO public.thesis_drafting_failures (
  thesis_job_id, signal_id, final_reasons, all_drafts, alerted
) VALUES (
  $job_id, $signal_id, $final_reasons, $all_drafts_jsonb, false
);

UPDATE public.thesis_jobs SET
  status = 'dlq',
  drafted_thesis = $last_draft,
  gate_reasons = $final_reasons,
  completed_at = now()
WHERE id = $job_id;
```

`all_drafts_jsonb` is an array of `{draft, gate_verdict, challenge_verdict}` triples — one per attempt — so the DLQ row captures the full adversarial trail for Pedro's audit. `alerted` is `false` because no email fires on a DLQ. Per the 2026-04-20 email-gating directive (memory `email_alert_gating.md`), raw `alerts.INSERT` is audit-only ([fanout/index.ts:89-94](supabase/functions/fanout/index.ts)); email only fires on `candidate_events` with `event_type='created' | 'thesis_drafted_by_claude'`, which a DLQ never produces. The dashboard surfaces DLQ'd rows as "needs manual thesis" — that is the notification path.

### 8c-flagged. Honest decline (§6.5) → promote-flagged

This is the path taken from §6.5 when the drafter self-declines (`confidence: "low"` OR `insufficient_signal: true`). Both gates skipped; both retry budgets untouched; promotion quota NOT consumed.

**Render dossier + upload first** — same four-call `rpc_*` chain as §8a step 3 (render enqueue → render collect → upload enqueue → upload collect). Operators want to see the research the routine looked at and rejected, not a blank row. The dossier must include the `decline_verdict` block from §6.5 as a clearly-marked footer; the renderer (`render_candidate_markdown_v2`) already supports this when `extensions.routine_declined=true` is in the candidate row's payload.

**Compute the flag JSONB** before the UPSERT:

```python
flag_extensions = {
    "routine_declined": True,
    "routine_decline_reason": (
        thesis.get("insufficient_signal_reason")
        or f"confidence:{thesis['confidence']}"
    ),
    "declined_at": now_iso,
    "declined_by": "thesis_writer",          # or "signal_resolver" on the inline-draft branch
    "decline_verdict": decline_verdict_obj,  # the §6.5 structured block
}
```

**UPSERT with `state='watch'` (always — flagged rows never start `active`):**

```sql
INSERT INTO public.candidates (
  ticker, mic, entity_id, state, scoring_profile,
  current_score, current_band, dossier_markdown, dossier_storage_path,
  kill_conditions, deliver_conditions,
  next_catalyst_date, next_catalyst_window,
  extensions,
  thesis_approved_at, last_aging_evaluated_at
) VALUES (
  $ticker, $mic, $entity_id, 'watch', $scoring_profile,
  $score_with_bonus, $band_with_bonus, $markdown, $storage_path,
  $kill_conditions_jsonb, $deliver_conditions_jsonb,
  $next_catalyst_date, $next_catalyst_window,
  $flag_extensions_jsonb,
  now(), now()
)
ON CONFLICT (ticker, mic) DO UPDATE SET
  -- Preserve everything but merge the flag in. Don't overwrite a previously
  -- clean candidate's state/score/dossier with a re-decline; just add the flag
  -- so operators see the routine reconsidered and declined again.
  extensions = COALESCE(public.candidates.extensions, '{}'::jsonb)
                || EXCLUDED.extensions,
  last_aging_evaluated_at = EXCLUDED.last_aging_evaluated_at,
  updated_at = now()
RETURNING id, (xmax = 0) AS was_inserted;
```

**`candidate_events` write — payload carries `routine_declined: true` so email triggers can filter:**

```sql
INSERT INTO public.candidate_events (candidate_id, event_type, payload)
VALUES ($candidate_id,
        CASE WHEN $was_inserted THEN 'created'
             ELSE 'thesis_drafted_by_claude' END,
        jsonb_build_object(
          'source', 'thesis_writer',
          'thesis_job_id', $job_id,
          'signal_id', $signal_id,
          'thesis', $thesis_jsonb,
          'drafts', $all_attempts_jsonb,
          'routine_declined', true,
          'decline_verdict', $decline_verdict_jsonb,
          'drafter_session_id', $your_session_id
        ));
```

**Email gating.** The fanout edge function ([supabase/functions/fanout/index.ts](supabase/functions/fanout/index.ts)) must skip events where `payload->>'routine_declined' = 'true'`. This is the second authoritative email gate (the first being the post-AI-review pre-edge promotion check from `email_alert_gating.md`); both must be in place — flagged candidates fire the `candidate_events` insert by design (so operators see them on `/candidates`) but must not page anyone.

**Close the job as `promoted` with the flag marker:**

```sql
UPDATE public.thesis_jobs SET
  status = 'promoted',
  candidate_id = $candidate_id,
  drafted_thesis = $thesis_jsonb,
  gate_reasons = ARRAY['routine_declined_flagged'],
  completed_at = now()
WHERE id = $job_id;
```

The `gate_reasons=['routine_declined_flagged']` marker is what the §2 quota query filters on to exclude this row from the daily-15 cap. It is also what `challenger_retro` uses to bucket flagged-pass declines on its 4th axis.

**No `thesis_drafting_failures` row** is written on this branch — the candidate row IS the audit record. This intentionally diverges from the pre-revision behavior; historical `thesis_drafting_failures` rows with `final_reasons[1] LIKE 'routine_declined%'` are stale-but-preserved (resolve them by manual dismissal on the dashboard if the corresponding candidate now exists).

### 8d. Challenger verdict = `challenge` — retry once

Collect the challenger's `required_fixes` + `strongest_counter`. Re-enter step 6 with a semantic corrective prompt distinct from the syntactic one (step 8b):

> "Prior draft drew a `challenge` verdict from the semantic reviewer. The reviewer's strongest counter-argument was: '$STRONGEST_COUNTER'. Required fixes: $REQUIRED_FIXES. Address these specifically — either by strengthening the `why_underpriced` asymmetry claim, tightening the `steelman` to actually engage the strongest counter, or declining with `confidence: 'low'` if the claim can't be defended."

The retry MAY land as an honest decline (step 6.5 short-circuit → §8c-flagged promote) — that's a preferred outcome when the challenger identified a structural weakness; the candidate ships flagged for operator review rather than DLQ'ing. Set:

```sql
UPDATE public.thesis_jobs SET
  status = 'gate_failed_retrying',   -- same intermediate status; challenge_count distinguishes
  gate_reasons = ARRAY['challenger_challenge'] || $challenger_reasons,
  drafted_thesis = $prior_draft
WHERE id = $job_id;
```

Note: `status='gate_failed_retrying'` is reused for both syntactic and semantic retries. The distinguishing counter is `(attempt_count, challenge_count)` — `attempt_count` increments on every draft (step 3), `challenge_count` only increments on challenger invocation (step 6.8). A job may hit either retry budget before the other.

### 8e. Challenger verdict = `kill` — DLQ immediately, no retry

```sql
INSERT INTO public.thesis_drafting_failures (
  thesis_job_id, signal_id, final_reasons, all_drafts, alerted
) VALUES (
  $job_id, $signal_id,
  ARRAY['challenger_kill'] || $challenger_reasons,
  $all_drafts_jsonb,   -- includes the kill verdict as all_drafts[-1].challenge_verdict
  false
);

UPDATE public.thesis_jobs SET
  status = 'dlq',
  drafted_thesis = $last_draft,
  gate_reasons = ARRAY['challenger_kill'] || $challenger_reasons,
  completed_at = now()
WHERE id = $job_id;
```

`kill` is the challenger saying "this thesis is structurally wrong — widely-watched with no edge, hallucinated catalyst, cosmetic kill conditions, or similar." A retry cannot fix structural failures, so skip step 6 entirely and go straight to DLQ. The full challenger verdict (`reasons`, `strongest_counter`, `evidence_citations`) lives in `all_drafts[-1].challenge_verdict` for Pedro's audit. Dashboard surfaces these identically to syntactic DLQs (same "needs manual thesis" banner); the `final_reasons` prefix `challenger_kill` distinguishes the root cause.

### 8f. Dispatch table (routing summary)

After step 6.8 (challenger) and step 7 (syntactic gate), the two verdicts combine as follows:

| Challenger | Syntactic gate | Action |
|---|---|---|
| `confirm` | `pass` | **Promote** (step 8a) |
| `confirm` | `fail`, 1st | Retry syntactic (step 8b) |
| `confirm` | `fail`, 2nd | DLQ `final_reasons=[syntactic_fail, ...]` (step 8c) |
| `challenge`, 1st | — | Retry challenger (step 8d) — skip syntactic gate this turn |
| `challenge`, 2nd | — | DLQ `final_reasons=[challenger_challenge_exhausted, ...]` (step 8c-style) |
| `kill` | — | DLQ `final_reasons=[challenger_kill, ...]` (step 8e) — skip retry, skip syntactic gate |
| — | — (honest decline) | **Promote-flagged** (`extensions.routine_declined=true`, `state='watch'`, gate_reasons=['routine_declined_flagged'], no quota consumed, no email) (step 6.5 → 8c-flagged) — skip both gates |
| — | — (pre-filter decline) | **Promote-flagged** with `gate_reasons=['routine_declined_flagged','prefilter_H1'\|'prefilter_H2'\|'prefilter_H3']`, `extensions.decline_verdict.prefilter_check` set (step 4.5 → 6.5 → 8c-flagged) — skip research, draft, both gates |

The challenger runs BEFORE the syntactic gate (step 6.8 before step 7). A `kill` from the challenger short-circuits the syntactic gate entirely — no point paying for char-count validation on a structurally dead thesis.

### 9. Move to the next job

Loop to step 2 until `thesis_jobs` queue is empty or the 5-row batch is exhausted.

## Reference data

- Syntactic gate rules: `modal_workers/shared/candidate_gate.py` (v2 is `assess_thesis_v2`). Do not inline-reimplement; call via `public.rpc_assess_thesis(thesis) → bigint request_id` + `public.rpc_compute_collect(request_id, 40000) → jsonb` (two separate `execute_sql` statements; the wrapper POSTs to the `assess-thesis` Modal endpoint).
- Semantic gate (challenger): separate Claude app routine with adversarial "skeptical IC reviewer" system prompt. Routine name + endpoint managed in the Anthropic console. The ITRK archetype is explicitly named in the challenger's system prompt as the pattern to catch.
- Dossier renderer: `render_candidate_markdown_v2` from the same module. Call via `public.rpc_render_candidate_markdown(args) → bigint` + `public.rpc_compute_collect`. Pair with `public.rpc_storage_upload(bucket, path, content, content_type) → bigint` + `public.rpc_compute_collect` for the dossier upload.
- Exemplar (good quality): `unified_system/unified_system/candidates/AXSM_ADA_PDUFA.md`. Study it for tone, depth, tag density, and specificity of asymmetry claims.
- Anti-pattern (structurally-complete but no asymmetry): `unified_system/unified_system/candidates/rejected_pending_thesis/ITRK_XLON_eqt-possible-offer.md` — DO NOT produce theses of this shape. This is the challenger's canonical `kill` example.

## Supabase cheatsheet (project_id=xvwvwbnxdsjpnealarkh)

Tables touched:
- `thesis_jobs` — read queued; update through drafting → promoted | dlq.
- `signals` — read only.
- `entities`, `scanners` — read only (context).
- `candidates` — upsert on (ticker, mic).
- `candidate_events` — append-only; `event_type ∈ {created, thesis_drafted_by_claude}` from this skill (the `thesis_updated` value exists in the enum but is not used here — it doesn't trigger the fanout email).
- `thesis_drafting_failures` — insert-only on DLQ path.
- Storage bucket `candidates/` — PUT dossier markdown (signed-URL semantics; service-role bypass).

RLS is enabled on every table; the Supabase MCP talks as service_role so bypass is fine.

## Self-check

Before emitting `{status: 'promoted'}` for a job, verify:

**Clean-promote branch** (challenger=confirm, gates passed):

- [ ] Challenger returned `verdict: "confirm"`.
- [ ] Syntactic gate returned `{ok: true, reasons: []}` from `assess_thesis_v2`.
- [ ] `candidates` row exists with your `candidate_id` AND has non-empty `kill_conditions`, non-NULL `(next_catalyst_date OR next_catalyst_window)`, and `thesis_approved_at = last_aging_evaluated_at = now()` on first creation.
- [ ] `candidates.extensions` does NOT contain `routine_declined` (the §8a UPSERT strips it on UPDATE).
- [ ] `candidate_events` row with `event_type='created'` (first insert) or `'thesis_drafted_by_claude'` (convergence re-draft) exists — NOT `'thesis_updated'`, which doesn't fire the email.
- [ ] `thesis_jobs.status = 'promoted'`, `gate_reasons IS NULL`, `completed_at` is set, `candidate_id` is set, `challenge_count ≥ 1` (exactly 1 on happy path).
- [ ] `dossier_storage_path` points at a Storage object you actually PUT.
- [ ] `all_drafts` in the outgoing `candidate_events.payload.drafts` preserves each attempt as `{draft, gate_verdict, challenge_verdict}` so the adversarial trail is recoverable.

**Flagged-pass branch** (§6.5 short-circuit on `confidence:"low"` / `insufficient_signal`):

- [ ] Both gates SKIPPED (no challenger invocation, no `assess_thesis_v2` call).
- [ ] `candidates` row exists with `state='watch'` AND `extensions->>'routine_declined' = 'true'` AND `extensions ? 'decline_verdict'`.
- [ ] `candidate_events` row event_type is `'created'` or `'thesis_drafted_by_claude'`, payload contains `routine_declined: true` so fanout filters skip it.
- [ ] `thesis_jobs.status = 'promoted'`, `gate_reasons = ARRAY['routine_declined_flagged']`, `completed_at` set.
- [ ] No `thesis_drafting_failures` row was written for this job.
- [ ] `dossier_storage_path` points at a Storage object (operators read the research even when flagged).
- [ ] `all_drafts[last]` contains a `decline_verdict` object (drafter-sourced, no challenger invocation).

**Pre-filter-decline branch** (§4.5 short-circuit on H1/H2/H3):

- [ ] No research, no draft Claude call, no challenger invocation, no `assess_thesis_v2` call. The only Claude work was the pre-filter SQL + verdict synthesis.
- [ ] `candidates` row exists with `state='watch'` AND `extensions->>'routine_declined' = 'true'` AND `extensions->'decline_verdict'->>'prefilter_check' IN ('H1','H2','H3')`.
- [ ] `thesis_jobs.gate_reasons` contains both `'routine_declined_flagged'` AND `'prefilter_H1'|'prefilter_H2'|'prefilter_H3'`.
- [ ] `attempt_count` did NOT increment beyond the step 3 claim (no retry budget consumed).
- [ ] `challenge_count = 0` (challenger never invoked).

Emit a summary line per job: `"<job_id>: promoted ticker.mic (score X→Y, band Y, challenger=confirm)"` for clean promotes, `"<job_id>: flagged ticker.mic (routine_declined: <reason>)"` for §6.5 flagged-passes, `"<job_id>: prefilter_decline ticker.mic (H<n>: <reason_token>)"` for §4.5 pre-filter declines, or `"<job_id>: dlq (<terse reason>, challenge_count=N)"` for gate-failure DLQs.
