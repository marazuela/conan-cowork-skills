---
name: coverage_auditor
description: Weekly recall audit. For every material catalyst in catalyst_universe whose catalyst_date fell in the past week (with 180d drill-back for monthly trend), join against emissions_ledger to determine if we caught it pre-edge, caught it post-edge, emitted without promoting, or never emitted at all. Writes top-10 "should have caught" cases to operator_flags and a summary markdown to reports/coverage/YYYY-WW.md. Runs mechanically (pure SQL + bucketing) — NO Claude reasoning, NO quota. Primary consumer of the emissions_ledger view foundation shipped 2026-04-21.
trigger: **Executed as code, NOT a Cowork scheduled task.** Runs as the first step of the existing `reporting_weekly` Modal function (cron `0 12 * * 0` UTC) — flags land before the weekly PDF renders in the same invocation. On-demand re-run via `modal run conan-v2::reporting_weekly_once` or Cowork phrase "run coverage audit" / "weekly recall audit" (skill body is the on-demand spec).
quota: No Claude quota consumed. All computation is SQL-level. Budget: typically <60s wall-clock, one Supabase round-trip per query. Does NOT occupy a Cowork run slot.
---

You are the coverage auditor for the Conan v2 accuracy feedback loop. Once per UTC week you reconcile every material catalyst that actually happened in the world (`catalyst_universe`) against every signal the pipeline emitted (`emissions_ledger` view), surface the ones we missed, and produce a report that drives threshold tuning and scanner-gap prioritization.

You are the first thing the feedback loop does with ground truth. Pedro built you to answer one question: "of the material catalysts that happened this week, which did we catch pre-edge, and for the ones we didn't, why?"

## Deployment (2026-04-23)

**Primary execution surface: Modal**, folded into `reporting_weekly` as its first step. No Cowork scheduled task, no Claude runtime session on the hot path. The skill body below remains the authoritative spec — both the Modal implementation and any on-demand Cowork re-run follow these steps. The on-demand Cowork path exists only for manual audits; it must not be wired to a schedule.

## Invariants

1. **Mechanical only — no Claude reasoning in the hot path.** The bucketing logic is pure SQL over the `emissions_ledger` view + `catalyst_universe`. Do not enlist the Anthropic app routines API. Every run must be reproducible from the same DB state.
2. **One report per ISO week (UTC).** Key by `EXTRACT(isoyear FROM now())`-`EXTRACT(week FROM now())`. If a report file already exists in Storage for this week, overwrite it (re-running is intentional — catches newly-landed catalyst_universe rows).
3. **Idempotent operator_flags.** The top-10 "should have caught" cases write to `operator_flags` with `(source='reporting_weekly', kind='coverage_miss', candidate_id=NULL, entity_id=<matched or NULL>)`. The existing partial unique index dedupes open flags — re-running same-week updates evidence rather than inserting duplicates.
4. **Material-only for coverage metrics.** `material_outcome = 'yes'` is the denominator. Non-material catalysts (`no` / `unclear`) are excluded from recall rates but kept in the raw ledger for false-positive analysis later.
5. **Fail loud on empty windows.** If `catalyst_universe` has zero material rows in the window, emit `operator_flags(severity='warn', source='reporting_weekly', kind='coverage_empty_window')` — means the fetchers are broken, not that the world was quiet.
6. **Never write to signals / thesis_jobs / candidates / outcomes.** The auditor is read-heavy and only writes to `operator_flags` + Storage `reports/coverage/*.md`.

## Run — step by step

### 1. Establish the window

```sql
-- Primary window: most recent complete UTC week (Mon 00:00 UTC .. Sun 23:59:59 UTC).
-- Use ISO week so year rollover is clean.
SELECT
  date_trunc('week', (now() AT TIME ZONE 'UTC') - INTERVAL '7 days')::date AS window_start,
  date_trunc('week', (now() AT TIME ZONE 'UTC'))::date - 1                  AS window_end,
  to_char((now() AT TIME ZONE 'UTC') - INTERVAL '7 days', 'IYYY-"W"IW')     AS iso_week;
```

Also establish the trend window (last 180d) for the executive-summary section:

```
trend_start = window_end - 180 days
trend_end   = window_end
```

### 2. Gather material catalysts in window (and trend)

```sql
SELECT cu.id, cu.profile, cu.catalyst_type, cu.ticker, cu.mic, cu.entity_id,
       cu.catalyst_date, cu.realized_price_move, cu.source_feed, cu.source_url,
       cu.raw_payload
FROM catalyst_universe cu
WHERE cu.material_outcome = 'yes'
  AND cu.catalyst_date BETWEEN $window_start AND $window_end
ORDER BY cu.catalyst_date;
```

And for the trend:

```sql
SELECT cu.profile, cu.catalyst_date
FROM catalyst_universe cu
WHERE cu.material_outcome = 'yes'
  AND cu.catalyst_date BETWEEN $trend_start AND $trend_end;
```

If the primary-window result is empty → emit the `coverage_empty_window` flag (invariant 5) and stop.

### 3. For each catalyst, bucket coverage

For every material catalyst in the window, look for emissions in the 90-day pre-catalyst window via the `emissions_ledger` view. Join on `ticker` first (primary key of overlap); fall back to `entity_id` when `ticker IS NULL`.

```sql
-- Per-catalyst coverage query. Run once per material catalyst row.
SELECT
  cu.id AS catalyst_id,
  cu.ticker, cu.catalyst_date, cu.profile, cu.catalyst_type,
  el.signal_id,
  el.scored_at,
  el.gate_decision,
  el.thesis_job_status,
  el.promoted_at,
  el.score_total,
  el.band,
  el.auto_caps_triggered
FROM catalyst_universe cu
LEFT JOIN emissions_ledger el
       ON (
         (cu.ticker IS NOT NULL AND el.ticker = cu.ticker)
         OR (cu.ticker IS NULL AND cu.entity_id IS NOT NULL AND el.entity_id = cu.entity_id)
       )
      AND el.scored_at::date BETWEEN cu.catalyst_date - INTERVAL '90 days' AND cu.catalyst_date
WHERE cu.id = $catalyst_id
ORDER BY el.scored_at DESC;
```

Bucket logic (Python or SQL CASE — both work; SQL is preferred for the weekly rollup query below):

| Condition                                                            | coverage_bucket             |
|----------------------------------------------------------------------|-----------------------------|
| ≥1 emission with `gate_decision='promoted'` AND `promoted_at <= catalyst_date` | `caught_pre_edge` ✓   |
| ≥1 emission with `gate_decision='promoted'` AND `promoted_at >  catalyst_date` | `caught_post_edge`    |
| ≥1 emission, all non-promoted                                       | `emitted_but_<first_blocking_decision>` — pick the EARLIEST emission's gate_decision (that's the first-blocking one). Possible suffixes: `auto_capped`, `rejected_thesis`, `below_band`, `resolved_below_immediate`, `pending`. |
| Zero emissions in window                                            | `never_emitted`             |

### 4. Compute the weekly rollup

One denormalized query producing per-profile + per-bucket counts:

```sql
WITH catalyst_coverage AS (
  SELECT
    cu.id, cu.profile, cu.catalyst_type, cu.ticker, cu.catalyst_date,
    cu.realized_price_move,
    -- Earliest emission in the pre-catalyst window (if any)
    MIN(el.scored_at) FILTER (WHERE el.signal_id IS NOT NULL) AS first_emission_at,
    -- Did any promoted emission exist BEFORE the catalyst?
    BOOL_OR(el.gate_decision = 'promoted' AND el.promoted_at <= cu.catalyst_date) AS had_pre_edge_promotion,
    -- Did any promoted emission exist AFTER the catalyst?
    BOOL_OR(el.gate_decision = 'promoted' AND el.promoted_at >  cu.catalyst_date) AS had_post_edge_promotion,
    -- First-blocking decision (earliest emission's gate_decision)
    (array_agg(el.gate_decision ORDER BY el.scored_at) FILTER (WHERE el.signal_id IS NOT NULL))[1]
      AS first_blocking_decision
  FROM catalyst_universe cu
  LEFT JOIN emissions_ledger el
         ON (
           (cu.ticker IS NOT NULL AND el.ticker = cu.ticker)
           OR (cu.ticker IS NULL AND cu.entity_id IS NOT NULL AND el.entity_id = cu.entity_id)
         )
        AND el.scored_at::date BETWEEN cu.catalyst_date - INTERVAL '90 days' AND cu.catalyst_date
  WHERE cu.material_outcome = 'yes'
    AND cu.catalyst_date BETWEEN $window_start AND $window_end
  GROUP BY cu.id, cu.profile, cu.catalyst_type, cu.ticker, cu.catalyst_date, cu.realized_price_move
),
bucketed AS (
  SELECT *,
    CASE
      WHEN had_pre_edge_promotion        THEN 'caught_pre_edge'
      WHEN had_post_edge_promotion       THEN 'caught_post_edge'
      WHEN first_emission_at IS NOT NULL THEN 'emitted_but_' || COALESCE(first_blocking_decision, 'unknown')
      ELSE 'never_emitted'
    END AS coverage_bucket
  FROM catalyst_coverage
)
SELECT profile, coverage_bucket, COUNT(*) AS n
FROM bucketed
GROUP BY profile, coverage_bucket
ORDER BY profile, coverage_bucket;
```

### 5. Surface top-10 "should have caught" as operator_flags

Rank by `realized_price_move` DESC (largest misses first). Exclude `caught_pre_edge` — those aren't misses. Include the catalyst_date, the coverage bucket, and the first-blocking decision in evidence.

```sql
WITH misses AS (
  -- same CTE as step 4, but filter to non-caught
  SELECT * FROM bucketed WHERE coverage_bucket != 'caught_pre_edge'
  ORDER BY realized_price_move DESC NULLS LAST, catalyst_date DESC
  LIMIT 10
)
INSERT INTO operator_flags (severity, source, kind, entity_id, title, body, evidence)
SELECT
  CASE WHEN coverage_bucket = 'never_emitted' THEN 'warn'
       WHEN coverage_bucket = 'caught_post_edge' THEN 'warn'
       ELSE 'info' END                                                                AS severity,
  'reporting_weekly'                                                                  AS source,
  'coverage_miss'                                                                     AS kind,
  (SELECT id FROM entities WHERE primary_ticker = misses.ticker LIMIT 1)              AS entity_id,
  format('Coverage miss: %s (%s) %s%% move on %s — %s',
         ticker, profile, COALESCE(realized_price_move::text, '?'),
         catalyst_date::text, coverage_bucket)                                        AS title,
  format('catalyst_type=%s first_blocking=%s first_emission_at=%s',
         catalyst_type, COALESCE(first_blocking_decision, 'n/a'),
         COALESCE(first_emission_at::text, 'n/a'))                                    AS body,
  jsonb_build_object(
    'catalyst_id',              id,
    'profile',                  profile,
    'catalyst_type',            catalyst_type,
    'ticker',                   ticker,
    'catalyst_date',            catalyst_date,
    'realized_price_move',      realized_price_move,
    'coverage_bucket',          coverage_bucket,
    'first_blocking_decision',  first_blocking_decision,
    'iso_week',                 $iso_week
  )                                                                                   AS evidence
FROM misses
ON CONFLICT (source, kind,
             coalesce(scanner_id::text, ''), coalesce(entity_id::text, ''),
             coalesce(signal_id, ''),         coalesce(candidate_id::text, ''))
WHERE resolved_at IS NULL
DO UPDATE SET title = EXCLUDED.title,
              body = EXCLUDED.body,
              evidence = EXCLUDED.evidence,
              updated_at = now();
```

Note: the ON CONFLICT target must match the existing `operator_flags_open_uniq` partial unique index (see [initial_schema.sql:284-293](https://github.com/marazuela/conan/blob/main/supabase/migrations/20260420200000_initial_schema.sql)). Re-running the same week updates open flags; a user resolving a flag means next week's run creates a fresh one if the same miss recurs.

### 6. Render the weekly markdown report

Build a plain-text summary and upload to Supabase Storage at `reports/coverage/<iso_week>.md`. Use the MCP's `execute_sql` for the queries, then call `public.rpc_storage_upload` via the same MCP to PUT the markdown (replaces the old bash `curl`, which stopped working when the Cowork Linux sandbox failed to start on 2026-04-22; the RPC POSTs to a Modal endpoint that wraps the same `PUT /storage/v1/object/...` call with service-role auth).

Report skeleton:

```markdown
# Coverage Report: {iso_week} ({window_start} → {window_end})

## Headline

- Material catalysts in universe (this week): {N}
- Caught pre-edge: {caught_pre_edge} ({pct}%)
- Caught post-edge: {caught_post_edge}
- Emitted but not promoted: {emitted_but_*}
- Never emitted: {never_emitted}

## Coverage by profile (this week)

| profile | catalysts | caught_pre_edge | caught_post_edge | emitted_not_promoted | never_emitted |
|---|---|---|---|---|---|

## Trend (180d)

| profile | catalysts_180d | caught_pre_edge_180d | pre_edge_rate_180d |
|---|---|---|---|

## Top 10 "should have caught" this week

| ticker | profile | catalyst_type | date | realized_move | first_blocking |
|---|---|---|---|---|---|

## Scanner efficiency (this week)

For each scanner that emitted anything in the window, count:
- emissions_total
- emissions_to_promoted (fraction that reached candidate)
- emissions_to_material_catalyst (fraction whose ticker matched a catalyst_universe row)
```

Storage upload (Supabase RPC).

**Two-statement pattern.** As of 2026-04-23 every `rpc_*` compute call is split across two `execute_sql` statements — enqueue (returns `bigint` request_id), then collect. The single-call form deadlocks for 60s because of a pg_net in-transaction visibility bug. Never collapse the pair into one statement.

Call 1 — enqueue:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_storage_upload(
  'reports',
  'coverage/' || '<iso_week>' || '.md',
  $md$<full rendered markdown report from the skeleton above>$md$,
  'text/markdown'
) AS request_id;
```

Call 2 — collect (separate `execute_sql`):

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_compute_collect(<request_id>, 40000) AS result;
```

Response: `{"uploaded": true, "bucket": "reports", "path": "coverage/<iso_week>.md", "size_bytes": <n>}`. Use `$md$...$md$` dollar quoting so single quotes, backticks, and table pipes in the rendered report survive SQL literal parsing. Re-running overwrites the prior week's file (the Modal endpoint sets `x-upsert: true`).

Note: the `reports` bucket previously disallowed `text/markdown` MIME uploads — a config bug silently broke the bash curl path. Fixed 2026-04-22 via `storage.buckets.allowed_mime_types += 'text/markdown'`.

### 7. Emit a skill-output summary line per bucket

At the end of the run, print:

```
coverage_auditor iso_week={W} material={N} pre_edge={X} post_edge={Y} emitted_not_promoted={Z} never_emitted={M} top_miss_flags={K} report_url={reports/coverage/{W}.md}
```

This line is what the Cowork session surfaces to Pedro. One row, machine-parseable.

## Reference data

- `emissions_ledger` view: defined in [20260424000000_emissions_ledger_foundation.sql](https://github.com/marazuela/conan/blob/main/supabase/migrations/20260424000000_emissions_ledger_foundation.sql). Joins signals → thesis_jobs → candidates → outcomes with derived `gate_decision`.
- `catalyst_universe` table: same migration. Populated by `modal_workers/fetchers/universe/*.py` via `dispatch_daily` (09:00 UTC).
- Current universe feeds (2026-04-21): `fda_adcomm_pdufa` (openFDA drugsfda AP submissions), `sec_8k_mna` (EDGAR 8-K items 1.01/2.01). Future: 13D, ESMA resolved, litigation, take_private intersect. Check `catalyst_universe.source_feed DISTINCT` for the live feed list.
- `gate_decision` vocabulary: `promoted`, `rejected_thesis`, `resolved_below_immediate`, `pending`, `auto_capped`, `below_band`, `immediate_no_thesis_job`, `unknown`.
- `operator_flags_open_uniq` partial unique index governs dedup for the top-10 flag writes.

## Supabase cheatsheet (project_id=xvwvwbnxdsjpnealarkh)

Tables read:
- `catalyst_universe` — material catalysts in the world.
- `emissions_ledger` (view) — everything we emitted + gate decision + candidate state + outcome.
- `entities` — for entity_id lookup on flag writes.

Tables written:
- `operator_flags` — top-10 misses, idempotent per week.

Storage written:
- `reports/coverage/<iso_week>.md` — human-readable summary.

RLS is on; Supabase MCP talks as service_role so writes bypass.

## Self-check

Before closing out a run, verify:

- [ ] Primary window is the most recent complete UTC week (not the partial current week).
- [ ] Only `material_outcome='yes'` catalysts are counted in coverage rates.
- [ ] `caught_pre_edge` requires `promoted_at <= catalyst_date` — post-edge promotions don't count.
- [ ] At most 10 `operator_flags` rows were upserted for coverage_miss (top-10 limit).
- [ ] Markdown report landed in Storage at the correct path.
- [ ] Summary line printed; `material={N}` equals the step 2 query's row count.

## Known limitations (as of 2026-04-21)

- Only 2 universe feeds live. The coverage picture is US-heavy (FDA + SEC) until the remaining 5 fetchers ship (13D, ESMA resolved, litigation, take_private, phase3).
- Entity-id resolution for EDGAR 8-K rows is ~15% (28/188 in the first week); ticker resolution is ~100% via display-name parsing. The join in step 3 uses ticker first so the 85% with no `entity_id` still match. A future `entity_linker` pass will close the gap.
- `realized_price_move` is only populated for v1 backfill anchors (TVTX/AVNS/GSAT/SEM). Auto-computation from pricing data is deferred — the "top-10 by realized move" ranking will fall back to `catalyst_date DESC` when moves are NULL. Rank by recency when moves aren't known.
- The `first_blocking_decision` reflects the EARLIEST emission. If a ticker had three emissions over 90 days at different gate_decisions, only the first one counts. That's the correct framing for recall ("why didn't the pipeline move when it first saw the ticker"), but operators analyzing specific cases should drill into the full per-catalyst query in step 3.
