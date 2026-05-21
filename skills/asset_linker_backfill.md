---
name: asset_linker_backfill
description: Drain unclassified documents by linking each to relevant FDA asset(s). Replaces the Modal Sonnet pass-1 (`v3-asset-linker-pass1` pg_cron — disabled 2026-05-13). Runs on Pedro's Claude.app subscription, zero API credits. Stamps the `linker_classified_at | result | asset_set_hash` marker triple so docs aren't re-claimed forever and asset-set changes auto-invalidate stale classifications.
trigger: Recurring scheduled task (every 30 min) OR on-demand "drain asset_linker backlog"
quota: 25 documents per run, 300 per UTC day. Soft cap — stop when reached, do NOT enqueue beyond.
---

You are the **asset_linker pass-1** for the Conan v3 pipeline. The Modal worker (`modal_workers/extractor/asset_linker.py`) used the Anthropic API and burned ~$42/week on Sonnet calls — most of it on docs that yield no link. This skill does the same classification on Pedro's Claude.app subscription, so the queue drains at zero API cost. The Modal pg_cron `v3-asset-linker-pass1` was disabled 2026-05-13 in favor of this skill.

**As of 2026-05-13:** initial backlog 98.6% drained; steady-state inflow ~30 new docs/day. The remaining stragglers are stuck behind asset-set-hash cache invalidation (8,659 docs with NULL hash from pre-PR-#46) — those drain naturally as this skill processes them and writes the current hash.

## Invariants

1. **Read-only on `documents.raw_text` + `fda_assets`. INSERT-only on `asset_documents`. UPDATE-only on the `documents.linker_classified_*` marker triple** (`linker_classified_at`, `linker_classified_result`, `linker_classified_asset_set_hash`). Never DELETE.
2. **Always stamp the marker.** Every doc you touch — including prefilter-skipped and zero-link ones — gets `linker_classified_at = now()`, `linker_classified_result` ∈ {`linked`, `no_match`, `parse_error`}, and the current `linker_classified_asset_set_hash`. Without the stamp, the next run re-claims the doc — that's the bug the marker mechanism was added to prevent.
3. **Cite spans from the doc itself.** Every link emitted MUST include 1–3 verbatim `extracted_spans` (≤300 chars each) drawn from `raw_text`. No fabrication. If you can't quote, you can't link.
4. **`is_material` is investor-grade.** A boilerplate 13F mention, market-table row, or competitor-filing name-drop is NOT material. Only set `is_material=true` if a competent biotech investor would treat it as decision-relevant.
5. **No score, no band, no thesis.** This skill links documents to assets. It does NOT touch `thesis_jobs`, `signals`, `convergence_assessments`. Anything beyond `asset_documents` INSERTs + the marker UPDATE is out of scope.
6. **link_type taxonomy is fixed.** Use only: `primary`, `mentions`, `pipeline_context`, `safety_signal`, `literature`. Definitions in step 5 — do not invent variants.
7. **Honest empty.** If a doc mentions an asset only in boilerplate, market-table, or competitive landscape with no investor signal: emit `{"links": []}` and stamp `no_match`. The Modal worker's empty-rate problem came from over-eager linking — don't repeat it.
8. **`extraction_method='cowork_backfill'`.** Always. Distinguishes Cowork-emitted rows from `'agent_pass1'` / `'agent_pass2'` (legacy Modal) and prevents pass-2 Haiku verification from re-running on them.
9. **Yield-first claim order.** Both Modal `v3-asset-linker-pass1` (disabled 2026-05-13) AND `v3-asset-linker-pass2` (Haiku verifier — also removed from cron.job as of 2026-05-21) are gone. No concurrent writers exist. Claim docs in YIELD order so ticks don't burn on 2023 EDGAR noise (a 2026-05-20 manual drain found 0/25 hits oldest-first, then 35/52 hits yield-first):
   - tier 1: `source IN ('conan_signal','press_release')` (~37% / ~82% hit rate)
   - tier 2: `source IN ('clinicaltrials','dailymed','openfda','fda_advisory')`
   - tier 3: everything else (EDGAR / federal_register / etc.), newest-first

   Still leave `verified_by_pass2 = false` on inserts — if pass-2 ever comes back, the FALSE flag means it'll re-verify your rows.

## Run — step by step

### 0. Hard-halt check

```sql
SELECT count(*) AS halt
FROM operator_flags
WHERE source = 'asset_linker_hard_halt'
  AND resolved_at IS NULL
  AND created_at > now() - interval '24 hours';
```

If `halt > 0` — log `"asset_linker 24h hard halt active; skipping"` and exit. Do not proceed.

### 1. Compute the current active asset-set hash

```sql
SELECT md5(string_agg(id::text, E'\n' ORDER BY id::text)) AS asset_set_hash
FROM public.fda_assets WHERE is_active = true;
```

Save this hash. You'll stamp it on every doc you touch this run. The Modal worker uses the same construction (`asset_linker.py:_active_asset_set_hash`); keeping it identical is what makes Cowork-emitted and Modal-emitted stamps interchangeable.

### 2. Find work (yield-first per invariant 9)

```sql
WITH pending AS (
  SELECT d.id, d.source, d.doc_type, d.title, d.url,
         d.raw_text, d.raw_text_tokens, d.storage_path,
         d.published_at, d.fetched_at
  FROM public.documents d
  WHERE (d.linker_classified_at IS NULL
         OR d.linker_classified_asset_set_hash IS NULL
         OR d.linker_classified_asset_set_hash <> '<asset_set_hash from step 1>')
  ORDER BY
    CASE
      WHEN d.source IN ('conan_signal','press_release')                       THEN 1
      WHEN d.source IN ('clinicaltrials','dailymed','openfda','fda_advisory') THEN 2
      ELSE                                                                         3
    END,
    d.published_at DESC NULLS LAST
  LIMIT 25
)
SELECT * FROM pending;
```

**Why yield-first:** the historical queue is dominated by Goldman/JPM/Morgan Stanley 424B2 prospectuses and Federal Register notices that have near-zero biotech hit rate. Tier-1 sources (`conan_signal`, `press_release`) yield links on most rows; tier-3 (EDGAR generic) yields almost nothing. With no concurrent writer (Modal pass-1 + pass-2 are both off), there's no race-safety reason to claim oldest-first. If empty → log `"queue drained"` and exit.

### 3. Daily quota check

```sql
SELECT count(*) AS today_count
FROM public.asset_documents
WHERE extraction_method = 'cowork_backfill'
  AND created_at::date = (now() AT TIME ZONE 'UTC')::date;
```

If `today_count >= 300` → log `"daily quota reached"` and exit.

**Schema note.** The CHECK constraint on `asset_documents.extraction_method` must include `'cowork_backfill'` — the migration `20260522000010_asset_linker_cowork_backfill_extraction_method.sql` adds it. On `check_violation`, the migration hasn't been applied; stop and surface the issue.

### 4. Load the asset directory

Fetch active FDA assets once per run — keyword index is the prefilter input.

```sql
SELECT id, ticker, drug_name, generic_name, sponsor_name, indication, indication_normalized
FROM public.fda_assets
WHERE is_active = true;
```

Build a keyword set per asset using the same logic as `modal_workers/extractor/asset_linker.py:110-147`:
- `drug_name`: as-is, or split `"BRAND (generic)"` into both parts. Min length 4.
- `generic_name`: as-is, min length 4.
- `sponsor_name`: first 2 tokens matching `\b[A-Z][\w-]{3,}\b`.
- `indication`: first 3 words.

### 5. Prefilter each document (no LLM yet)

For each document from step 2, lowercase the text and check substring presence of any asset keyword. Build the per-doc candidate-asset list:

- If a keyword appears in the doc text → add that asset to the doc's candidate list.
- If the candidate list is empty → **skip classification, stamp the marker as `no_match` (step 7), continue**.

Track skipped-by-prefilter count for the run summary. Most docs should skip here.

### 6. Classify each prefilter-passed document

For each doc with ≥1 candidate asset, read the doc text (truncate to 80,000 chars if needed, prioritizing first 30% + windows around keyword matches per `asset_linker.py:215-263`) and emit a JSON link list using this exact schema:

```json
{
  "links": [
    {
      "asset_id": "<uuid>",
      "link_type": "primary | mentions | pipeline_context | safety_signal | literature",
      "extraction_confidence": 0.0,
      "extracted_spans": [
        {"text": "<verbatim quote ≤300 chars>", "context": "<1-line>"}
      ],
      "is_material": true,
      "reasoning": "<1-3 sentences>"
    }
  ]
}
```

**link_type definitions** (identical to Modal worker for parity):

- **primary**: the document is *about* this asset (PDUFA notice, sponsor 8-K announcing FDA correspondence on this drug, 10-K Item 1 sub-section). Confidence ≥0.85.
- **mentions**: the asset is named in passing (competitor 10-K market table, 13F holdings). Typically `is_material=false`.
- **pipeline_context**: the doc discusses the sponsor's pipeline broadly and this asset is one of several covered. `is_material` may be true if the section gives substantive detail on this asset.
- **safety_signal**: FAERS AE, FDA warning letter, 483 inspection, peer-reviewed AE publication relevant to this asset.
- **literature**: peer-reviewed paper or preprint discussing this asset's mechanism, trial data, or comparative evidence.

If the doc references the candidate asset(s) only in boilerplate / non-investor-grade context, emit `{"links": []}` — that's the correct answer, not a failure. Stamp `no_match` (step 7) and move on.

### 7. Insert links + ALWAYS stamp the marker

**(a) Insert links** (skip docs with empty `links` arrays — no INSERT, but still stamp):

```sql
INSERT INTO public.asset_documents (
  asset_id, document_id, link_type,
  extraction_method, extraction_confidence,
  extracted_spans, is_material, verified_by_pass2
)
VALUES (
  '<asset_id>', '<document_id>', '<link_type>',
  'cowork_backfill', <confidence>,
  '<spans_jsonb>'::jsonb, <is_material>, false
)
ON CONFLICT (asset_id, document_id, link_type) DO NOTHING;
```

Idempotent — repeat runs over the same docs cause no duplicates. `created_at` is auto-set by the table default. The `reasoning` text returned by your classification step is not persisted (the schema has no `reasoning` column) — capture it in your run summary only.

**(b) Stamp the marker — every doc you touched, including no_match.**

```sql
UPDATE public.documents
SET linker_classified_at = now(),
    linker_classified_result = '<linked | no_match | parse_error>',
    linker_classified_asset_set_hash = '<asset_set_hash from step 1>'
WHERE id = '<document_id>';
```

If this UPDATE fails (RPC error, network), DO NOT proceed to the next doc — log and abort the run. An unstamped doc gets re-claimed forever; better to stop early than leave debris.

### 8. Emit a run summary

Append one row to the run log so the dashboard / watchdog can see Cowork backfill activity:

```sql
INSERT INTO public.asset_linker_runs (
  pass, model, started_at, completed_at, status,
  docs_seen, prefilter_passed, prefilter_skipped,
  api_calls, errors, links_inserted, links_dedup_skipped,
  input_tokens, output_tokens, cost_usd, notes
)
VALUES (
  'cowork_backfill', 'claude-app-cowork',
  '<run_start>', now(), 'completed',
  <docs_seen>, <prefilter_passed>, <prefilter_skipped>,
  <classifications_done>, <errors>, <links_inserted>, 0,
  0, 0, 0.0,
  'cowork backfill via Claude.app subscription'
);
```

Cost is zero (subscription is flat-rate). Token counts unknown — leave 0.

### 9. Report back to chat

One-line summary: `processed N docs | prefilter_pass=M | links_inserted=K | no_match=Q | parse_error=E | daily=X/300`.

If you hit any anomalies (doc text unreadable, asset directory empty, RPC failure on the marker UPDATE), surface them — do not silently swallow.

## Things you must NOT do

- Don't query `documents` with `LIMIT > 25` (Cowork session memory + reasoning bandwidth).
- Don't classify any doc that the prefilter rejected — that's the point. But DO stamp `no_match` so it's not re-claimed next run.
- Don't write `extraction_method='agent_pass1'` or `'agent_pass2'` — reserved for legacy Modal rows.
- Don't run pass-2 verification logic — that's `v3-asset-linker-pass2` Haiku's job. Leave `verified_by_pass2 = false` on your inserts.
- Don't touch `extracted_facts`, `convergence_assessments`, `orchestrator_runs`, or any downstream table.
- Don't re-enable the disabled `v3-asset-linker-pass1` pg_cron — it's off because this skill replaces it.
- Don't skip the marker UPDATE on an UPDATE error. Abort the run instead.

## Provenance & audit

Every row this skill emits has `extraction_method='cowork_backfill'`. Operators can audit Cowork emissions vs Modal emissions with:

```sql
SELECT extraction_method, count(*), max(linked_at)
FROM public.asset_documents
GROUP BY extraction_method
ORDER BY count(*) DESC;
```

If a Cowork link is later found to be wrong, it can be flipped to `is_material=false` (preserves audit trail) — do not DELETE.
