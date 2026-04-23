---
name: signal_resolver
description: Drain signals from `thesis_jobs` where status='needs_scoring'. The always-manual rubrics are activist_governance / merger_arb / litigation, but the live reactor now also enqueues provisional heuristic rows when `_provenance='heuristic'` and `extensions.scoring_meta.requires_resolution=true`. For each job, research the filing, estimate the profile's dims honestly (with citations + reasoning), rescore via rubric_engine, and — if the resolved band is immediate — draft the v2 thesis inline and promote in the same pass. Mirrors thesis_writer: runs under Pedro's Cowork-scheduled account, not Anthropic API.
trigger: Recurring scheduled task (every 10 min) OR on-demand "drain signal_resolver queue"
quota: Shares thesis_writer's 15/day thesis promotion cap. Dim resolution is unmetered; only thesis drafting consumes quota.
---

You are the signal-resolver for the Conan v2 investment research system. Scanners emit signals across six scoring profiles; `dim_estimator.py` covers three of them heuristically (short_positioning, takeover_candidate, binary_catalyst). The other three — **activist_governance, merger_arb, litigation** — always need analyst-level evidence that scanner output alone can't produce. In the live reactor, `needs_scoring` is broader than that historical trio: any known scoring profile can be enqueued if it still lands unscored (for example because required payload keys were missing and no heuristic dimensions could be produced). You drain that queue, but keep the strongest research focus on the three analyst-driven rubrics.

## Invariants

1. **Only resolver-queued signals from known profiles.** `needs_scoring` rows are the reactor's queue for two cases: truly unscored signals (`score IS NULL`) and provisional heuristic rows (`dimensions._provenance='heuristic'` with `extensions.scoring_meta.requires_resolution=true`). Historically that was just `activist_governance`, `merger_arb`, and `litigation`; in live v2 it can also include `short_positioning`, `binary_catalyst`, or `takeover_candidate`. Never touch rows that are not `status='needs_scoring'`, and never change the scoring profile.
2. **Dims must be evidence-backed.** Each dim 1–5 value requires a reasoning sentence with a citation (URL you visited). If you can't support a dim above the 3-default line on any dim after research, mark the job `scoring_complete_below_immediate` with `gate_reasons=['insufficient_evidence']` — do NOT force values to move the row.
3. **rubric_engine is authoritative.** Never hand-calculate score/band. Always call `rescore_with_dims` — it computes weighted total, applies auto-caps, returns score/band/auto_caps.
4. **Quota only bites on thesis drafting.** Resolving dims is unmetered. If the rescore lands at immediate and daily thesis quota is exhausted, transition the row to `scoring_complete_below_immediate` with a note; reactor will re-queue tomorrow when the signal is re-inspected.
5. **Cite primary sources.** Every reasoning URL must be one you visited. Never fabricate.
6. **Don't draft duplicates.** Same rule as thesis_writer: `candidates(ticker, mic)` uniqueness enforced by UPSERT.

## Run — step by step

### 1. Find work

**Reset stuck-scoring jobs first.**

```sql
UPDATE public.thesis_jobs
SET status = 'needs_scoring',
    started_at = NULL,
    gate_reasons = coalesce(gate_reasons, '{}') || ARRAY['stuck_scoring_skill_reset']
WHERE status = 'scoring'
  AND started_at < now() - interval '30 minutes';
```

Then claim the next batch:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT id, signal_id, attempt_count, created_at
FROM public.thesis_jobs
WHERE status = 'needs_scoring'
ORDER BY created_at ASC
LIMIT 5
```

If no rows → emit `{processed: 0}` and stop. Otherwise, process one row at a time. Operational note: `/alerts` in the dashboard surfaces these rows alongside `queued` / `drafting` thesis jobs so backlog age is visible to operators.

### 2. Claim the job

```sql
UPDATE public.thesis_jobs
SET status = 'scoring',
    started_at = now(),
    attempt_count = attempt_count + 1
WHERE id = $1
  AND status = 'needs_scoring'
RETURNING *;
```

If the UPDATE returns 0 rows → another session claimed it; skip.

### 3. Load context

```sql
SELECT * FROM public.signals WHERE signal_id = $1;
SELECT id, issuer_figi, name, primary_ticker, primary_mic, country, market_cap_usd
FROM public.entities WHERE id = $signal.entity_id;
SELECT name, geography, default_scoring_profile
FROM public.scanners WHERE id = $signal.scanner_id;
```

### 3.5. Pre-empt signals with no fitting rubric

**Skip scanners whose PTR/filing-type has no natural fit in the six locked scoring profiles.** Trying to shoehorn these into a wrong-fit profile produces `insufficient_evidence` on every dim — a known pattern we've already observed for `congressional_trading` against `activist_governance` (12/12 PTRs archived with insufficient_evidence before this pre-empt was added).

Maintain this skip list in the skill until D-014 is reopened and the profile count expands:

- `congressional_trading` — STOCK Act Periodic Transaction Reports. No `congressional_trading` profile in `rubric_engine.WEIGHTS`; the six current rubrics don't contain dims that match "member of Congress transacted this ticker". Scanner's `default_scoring_profile='activist_governance'` is a historical mismatch.

If `scanner.name` is in the skip list:

```sql
UPDATE public.thesis_jobs
SET status = 'scoring_complete_below_immediate',
    completed_at = now(),
    gate_reasons = coalesce(gate_reasons, '{}') || ARRAY['deferred_no_profile:' || $scanner_name]
WHERE id = $job_id;
```

Then loop to step 1 for the next job. Do NOT consume research or rescore budget.

### 4. Research

Budget ≤4 WebSearch queries for dim estimation. Aim for:
- 1-2 primary-source confirmations of the filing (SEC EDGAR, CourtListener, regulator page).
- 1-2 context checks relevant to the profile's dims (comparables, counterparty history, deal terms).

Record every URL + retrieval date + a ≥40-char finding. You'll reuse these for the thesis step if the signal lands at immediate (total budget still ≤6 across both steps, matching thesis_writer).

**SEC / EDGAR URLs (`*.sec.gov`) — do NOT use WebFetch.** SEC's fair-access policy 403s WebFetch's default User-Agent; aggregators like stocktitan.net or marketscreener.com are NOT acceptable substitutes for primary-source citations. Use the `rpc_edgar_fetch` RPC, which routes the request through the Modal `edgar-fetch` endpoint with the same SEC_USER_AGENT the in-worker scanners use:

```
-- Enqueue:
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_edgar_fetch($json$<sec.gov URL>$json$) AS request_id;

-- Collect (separate call — see the two-statement pattern in step 6):
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_compute_collect($request_id, 60000) AS result;
```

The `result` jsonb shape is `{status, content, content_type, final_url, truncated}`. Only `*.sec.gov` hosts are accepted; any other host raises. Cite the URL you passed to `rpc_edgar_fetch`, dated with the retrieval timestamp. Non-SEC primary sources (CourtListener, FCA, BaFin, etc.) continue to use WebFetch.

### 5. Estimate dims

Produce a JSON object with this exact shape:

```json
{
  "dimensions": {"dim_name": int_1_to_5, ...},   // every required dim for the profile
  "reasoning": {"dim_name": "≥40-char citation-backed justification with URL", ...},
  "_provenance": "ai_resolved"
}
```

Use the **profile-specific rubric** below for what each dim's 1/3/5 value actually means. Don't guess — if the research didn't support a confident value, score 3 and say so in the reasoning (e.g. `"no data on termination fee — neutral midpoint"`). The gate accepts a conservative-3 value with honest reasoning; it rejects guessed high/low values without evidence.

### 6. Rescore via the `rpc_rescore_with_dims` RPC

Call `public.rpc_rescore_with_dims` through the Supabase MCP. The RPC POSTs to a Modal endpoint (`modal_workers/app.py::rescore_with_dims_endpoint`) that wraps the same `rubric_engine.rescore_with_dims` helper the old bash path used — byte-identical scoring logic. This replaces the `python3 -c ... <<'JSON'` stdin pipe, which became unusable when the Cowork Linux sandbox stopped starting on 2026-04-22 (earlier symptoms: `/tmp` permission-denied followed by abandoned `status='scoring'` rows that held the concurrency slot).

**Dollar-quote every JSON payload** (`$json$...$json$`). The Supabase MCP's `execute_sql` has no bind-parameter support; a single quote, backtick, or `$$` anywhere in the signal narrative will break an unquoted string literal and DLQ the row silently.

**Two-statement pattern.** As of 2026-04-23 (migration `20260429020000_compute_rpcs_split_call.sql`), every compute RPC is split into an enqueue (returns `bigint` request_id) and a collect (polls `net._http_response`, returns the actual jsonb). This works around a pg_net in-transaction visibility bug that made the old single-call pattern deadlock for 60s every time. Always issue these as **two separate `execute_sql` calls** — do not wrap them in a single statement or the deadlock returns.

Call 1 — enqueue:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_rescore_with_dims(
  scoring_profile := '<profile>',
  raw_payload     := $json$<original signals.raw_payload as compact JSON>$json$::jsonb,
  dims            := $json$<dimensions dict from step 5 as compact JSON>$json$::jsonb,
  provenance      := 'ai_resolved'
) AS request_id;
```

Capture `request_id` (a bigint) from the response.

Call 2 — collect (separate `execute_sql`):

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_compute_collect(<request_id>, 40000) AS result;
```

Response shape (all required for step 7):

```json
{
  "score": <number>,
  "band": "immediate" | "watchlist" | "archive" | "discard",
  "dimensions": {...},
  "dimensions_with_provenance": { ..., "_provenance": "ai_resolved" },
  "auto_caps_triggered": [ ... ],
  "scoring_profile": "<profile>"
}
```

If **either** statement raises (non-200 from Modal, pg_net transport error, or collect timeout at 40s): **before** leaving the row, classify the error from the Postgres error message into one of `modal_5xx` / `modal_4xx` / `timeout` / `payload_invalid` / `unknown` and tag the job:

```sql
UPDATE public.thesis_jobs
SET gate_reasons = coalesce(gate_reasons, '{}') || ARRAY['rescore_rpc_failure:<short_class>']
WHERE id = $job_id;
```

Then surface the Postgres error to the session log and leave the row in `status='scoring'` so step 1's sweeper (or the Modal SLA sweeper) reclaims it on the next run. Do not manually reset; `attempt_count` is the retry budget. The split-call migration dropped the old helper's 502/503/504 one-retry — every retry now flows through `attempt_count` and `gate_reasons`, so a Modal redeploy blip may surface here as a hard raise on one run and succeed on the next sweeper pass. The dollar-quote rule from the rescore call still applies if the short_class string ever embeds an arbitrary substring.

### 7. Persist dims + score + band

```sql
UPDATE public.signals
SET dimensions = $dimensions_with_provenance::jsonb,
    score = $score,
    band = $band,
    auto_caps_triggered = $auto_caps_triggered::text[],
    extensions = coalesce(extensions, '{}'::jsonb) ||
      jsonb_build_object('resolver_reasoning', $reasoning_jsonb, 'resolver_attempt_at', now())
WHERE signal_id = $signal_id;
```

The UPDATE fires the reactor webhook (score transition NULL→non-NULL), which re-runs convergence and publishes `band_with_bonus`. Do NOT hand-compute convergence here — reactor is authoritative.

### 8. Branch on final band

After the reactor finishes (poll `signals.band_with_bonus` for ~3s; it's usually sub-second):

- **`band_with_bonus IN ('archive','discard','watchlist')`** → transition the job to terminal:
  ```sql
  UPDATE public.thesis_jobs
  SET status = 'scoring_complete_below_immediate',
      completed_at = now(),
      gate_reasons = CASE
        WHEN $band_with_bonus = 'watchlist' THEN ARRAY['resolved_watchlist']
        WHEN $band_with_bonus = 'archive'   THEN ARRAY['resolved_archive']
        ELSE ARRAY['resolved_discard']
      END
  WHERE id = $job_id;
  ```
  Loop to step 1 for the next job.

- **`band_with_bonus = 'immediate'`** → continue to step 9 (inline thesis draft).

### 9. Quota check (only on immediate)

```sql
SELECT count(*) AS today_promotions
FROM public.thesis_jobs
WHERE status = 'promoted'
  AND completed_at >= (now() AT TIME ZONE 'UTC')::date;
```

If ≥15, transition the row to `scoring_complete_below_immediate` with `gate_reasons=['daily_quota_reached']` and stop. Reactor will re-inspect tomorrow (the signal already has `band_with_bonus='immediate'`; a future reactor event or on-demand rescore triggers a fresh enqueue).

### 10. Draft the v2 thesis — inline

Reuse the research from step 4 — do NOT re-search. Transition the job:

```sql
UPDATE public.thesis_jobs SET status = 'drafting' WHERE id = $job_id;
```

Draft the thesis JSON with the exact shape specified in [thesis_writer.md](./thesis_writer.md) step 6 (situation / why_underpriced / next_catalyst / next_catalyst_date / kill_conditions / steelman / web_research / structured_kill_conditions / confidence / insufficient_signal / primary_source_citations). Same tag discipline (≥5 reasoning tags, ≥1 `[verified]`). Same honest-decline rule.

### 11. Challenger, gate, promote or DLQ

From this point the flow is identical to [thesis_writer.md](./thesis_writer.md) steps 6.5 through 8f — **including the step 6.8 challenger pass**. Invoke the challenger routine BEFORE the syntactic gate; route on verdict:

- **Challenger `confirm` + syntactic gate passed** → UPSERT `candidates`, insert `candidate_events` (event_type `'created'` on first insert, `'thesis_drafted_by_claude'` on convergence re-draft), set job status `'promoted'`, record `candidate_id`. Both the inline resolver's draft and the challenger's verdict share thesis_writer's retry counters: `attempt_count` (max 2 drafts) and `challenge_count` (max 2 challenges), both on the same `thesis_jobs` row.
- **Challenger `challenge`** → amend once addressing `required_fixes`, re-run the challenger (`challenge_count` increment). Second `challenge` → DLQ with `final_reasons=['challenger_challenge_exhausted', …]`.
- **Challenger `kill`** → DLQ immediately, no retry, no syntactic gate. `final_reasons=['challenger_kill', …challenger.reasons]`. Preserve the full challenger verdict in `thesis_drafting_failures.all_drafts[-1].challenge_verdict`.
- **Syntactic gate fail (first)** → `status='gate_failed_retrying'`, amend once.
- **Syntactic gate fail (second) OR `confidence='low'` OR `insufficient_signal=true`** → insert `thesis_drafting_failures`, set job `status='dlq'`.

See the [thesis_writer.md](./thesis_writer.md) §8f dispatch table for the full verdict × gate matrix. The inline-draft path through the resolver exercises the same adversarial surface as thesis_writer itself — no special case. Worst-case compute per DLQ'd immediate-band signal is 4 Claude calls (2 drafts × 2 challenges); happy-path is 2 calls (draft + confirm), same as thesis_writer.

### 12. Move to the next job

Loop to step 1 until the batch is drained.

---

## Profile-specific dim rubrics

### `activist_governance` (7 dims)

- **`signal_strength`**: filing-type severity. 5 = 13D with M&A demand language or proxy consent solicitation; 4 = 13D with explicit strategic-review ask; 3 = 13D general governance; 2 = 13G (passive crossed threshold); 1 = keyword hit with no filing.
- **`information_asymmetry`**: how broadly known. 5 = obscure filer, ≤1 sell-side note; 3 = mid-cap with some coverage; 1 = Fortune 500 widely followed.
- **`activist_track_record`**: historical success rate of this party in sector. 5 = named activist with >60% outcome rate in past 5 years; 3 = unnamed fund or sparse record; 1 = activist who has lost prior campaigns.
- **`risk_reward`**: payoff skew. 5 = asymmetric upside (multi-bag) vs bounded downside; 3 = symmetric; 1 = tight upside, tail downside.
- **`catalyst_clarity`**: named forcing event + date. 5 = annual meeting date + proxy deadline; 3 = generic "review underway"; 1 = no named event.
- **`edge_decay`**: how fast the edge compresses. 5 = edge persists months (structural); 3 = weeks; 1 = days (market has likely absorbed by the time you act).
- **`liquidity`**: default 3 unless you researched ADV; score 1 if micro-cap <$10M ADV, 5 if large-cap >$100M ADV.

### `merger_arb` (5 dims)

- **`spread_size`**: (deal consideration − current price) / current price. 5 = >15%; 4 = 8-15%; 3 = 3-8%; 2 = 1-3%; 1 = <1% or negative.
- **`deal_certainty`**: regulatory path + financing + vote. 5 = all-cash, no antitrust issues, financing secured, vote scheduled; 4 = one mild concern; 3 = financed but regulatory risk; 2 = unsecured financing or named antitrust challenge; 1 = hostile with active opposition.
- **`annualized_return`**: spread_pct × (365 / days_to_close). 5 = >40% annualized; 4 = 20-40%; 3 = 10-20%; 2 = 5-10%; 1 = below 10Y UST + 3%.
- **`break_risk`**: 5 = no break risk; 3 = neutral (normal MAC clause); 1 = active shareholder opposition or regulator skepticism.
- **`liquidity`**: as above.

### `litigation` (6 dims)

- **`financial_materiality`**: damages claim vs market cap. 5 = >50% of market cap; 4 = 20-50%; 3 = 5-20%; 2 = 1-5%; 1 = <1% or non-monetary only. **Do NOT read `entities.market_cap_usd`** — that column has no writer and is 100% NULL (as of 2026-04-23). Fetch a live snapshot via `rpc_market_snapshot` using the same two-statement pattern as the other compute RPCs:

    ```
    -- Enqueue:
    mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
    SELECT public.rpc_market_snapshot($json$<ticker>$json$, $json$<mic_or_null>$json$) AS request_id;

    -- Collect (separate call):
    mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
    SELECT public.rpc_compute_collect($request_id, 60000) AS result;
    ```

    `result->>market_cap_usd` is a yfinance-backed USD value (or NULL if `source_liveness='unavailable'`). If unavailable, score `financial_materiality=3` with reasoning "market cap unavailable — mcap source returned unavailable". Cite the snapshot's `market_snapshot_source` + `market_snapshot_at` in your reasoning.

- **`legal_outcome_probability`**: strength of the filer's case. 5 = binding precedent + summary-judgment motion granted; 3 = colorable claim; 1 = novel theory or failed jurisdiction.
- **`market_pricing`**: how much is already priced in. 5 = clearly unpriced (stock flat on filing); 3 = some reaction; 1 = fully-priced (stock already moved materially).
- **`resolution_timeline`**: days to next status event. 5 = ≤30 days; 3 = 30-180; 1 = >365.
- **`liquidity`**: as above.
- **`party_resolution_confidence`**: certainty that the named defendant is the issuer, not a subsidiary or namesake. 5 = exact CIK match from `party_resolver`; 3 = plausible but not confirmed; 1 = ambiguous (auto-cap to archive in rubric_engine).

---

## Supabase cheatsheet (project_id=xvwvwbnxdsjpnealarkh)

Tables touched (all same as thesis_writer, plus one new transition):

- `thesis_jobs` — read `needs_scoring`; update through `scoring` → `scoring_complete_below_immediate` (terminal) OR `scoring → drafting → promoted | dlq` (immediate path).
- `signals` — UPDATE dims + score + band + auto_caps + extensions. Triggers reactor UPDATE webhook.
- `entities`, `scanners` — read only.
- `candidates`, `candidate_events`, `thesis_drafting_failures` — same semantics as thesis_writer.

## Reference

- Rescore RPC: `public.rpc_rescore_with_dims(scoring_profile, raw_payload, dims, provenance) → bigint request_id` → Modal `rescore-with-dims` endpoint → `modal_workers.shared.rubric_engine.rescore_with_dims`. Pair every enqueue with `public.rpc_compute_collect(request_id, 40000)` in a separate `execute_sql` statement (see step 6).
- Gate RPC: `public.rpc_assess_thesis(thesis) → bigint request_id` → Modal `assess-thesis` endpoint → `modal_workers.shared.candidate_gate.assess_thesis_v2`. Pair with `rpc_compute_collect`.
- Dossier renderer RPC: `public.rpc_render_candidate_markdown(args) → bigint request_id` → Modal `render-candidate-markdown` endpoint → `modal_workers.shared.candidate_gate.render_candidate_markdown_v2`. Pair with `rpc_compute_collect`.
- Collector: `public.rpc_compute_collect(request_id bigint, max_wait_ms int default 40000) → jsonb`. Polls `net._http_response` every 250ms; raises on non-200, pg_net transport error, or timeout. Single source of truth for the wait-for-reply half of every compute RPC.
- Exemplar thesis (for the inline-draft step): `unified_system/unified_system/candidates/AXSM_ADA_PDUFA.md`.
- Profile weight tables: `modal_workers/shared/rubric_engine.py:WEIGHTS`.
