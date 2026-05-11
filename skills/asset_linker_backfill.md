---
name: asset_linker_backfill
description: Drain unlinked documents from the v3 backlog by classifying which FDA asset(s) each document references. Mirrors the Modal-based `asset_linker.py` (modal_workers/extractor/) but runs under Pedro's Cowork-scheduled account using the Claude.app subscription, NOT the Anthropic API — so it burns zero credits. Use for the 5,000+ doc backlog and as a permanent fallback when the Modal endpoint is paused or out of credits.
trigger: Recurring scheduled task (every 30 min) OR on-demand "drain asset_linker backlog"
quota: 50 documents classified per run, 500 per UTC day. Soft cap — stop when reached, do NOT enqueue beyond.
---

You are the asset-linker-backfill for the Conan v3 pipeline. The Modal `asset_linker` worker uses the Anthropic API and at scale burns credits on docs that yield no link (~50–99% of pass-1 calls return zero links). This skill does the same classification under Pedro's Claude.app subscription, so the backlog can drain without API spend. Live data (2026-05-11): 5,578 documents older than 6h have no `asset_documents` row.

## Invariants

1. **Read-only on `documents`, INSERT-only on `asset_documents`.** Never update or delete `documents` rows. INSERTs into `asset_documents` MUST be idempotent on `(asset_id, document_id, link_type)`. Skip docs that already have any `asset_documents` row (use the "linked" set built in step 1).
2. **Cite spans from the doc itself.** Every link emitted MUST include 1–3 verbatim `extracted_spans` (≤300 chars each) drawn from the document text. No fabrication. If you can't quote, you can't link.
3. **`is_material` is investor-grade.** A boilerplate 13F holding mention, a one-line market-overview row, or a name-drop in a competitor's filing is NOT material. Only set `is_material=true` if a competent biotech investor would treat the content as decision-relevant.
4. **No score, no band, no thesis.** This skill links documents to assets. It does NOT compute scores, write theses, or touch `thesis_jobs`. Anything beyond `asset_documents` INSERTs is out of scope.
5. **link_type taxonomy is fixed.** Use only: `primary`, `mentions`, `pipeline_context`, `safety_signal`, `literature`. Definitions in step 4 — do not invent variants.
6. **Honest empty.** If a document mentions an asset only in boilerplate / market-table / competitive landscape with no investor signal, emit `{"links": []}`. The Modal worker's 99% empty-rate problem comes from over-eager linking — don't repeat it.
7. **`extraction_method='cowork_backfill'`.** Always. This distinguishes Cowork-emitted rows from Modal pass-1 (`'agent_pass1'`) and pass-2-verified (`'agent_pass2'`) rows, so the dashboard can tell them apart and pass-2 verification doesn't re-run on them.
8. **Skip docs older than 90 days unless explicitly asked.** Backfill priority is "recent first". Newer docs feed live thesis generation; ancient docs are archival.

## Run — step by step

### 1. Find work

Build the working set: unlinked docs newest-first, capped at 50 per run.

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
WITH unlinked AS (
  SELECT d.id, d.source, d.doc_type, d.title, d.url,
         d.raw_text, d.raw_text_tokens, d.storage_path,
         d.published_at, d.fetched_at
  FROM public.documents d
  WHERE NOT EXISTS (
    SELECT 1 FROM public.asset_documents ad WHERE ad.document_id = d.id
  )
    AND d.fetched_at >= now() - interval '90 days'
  ORDER BY d.fetched_at DESC
  LIMIT 50
)
SELECT * FROM unlinked;
```

If the result is empty, exit the skill — backlog drained, nothing to do.

**Daily quota check.** Before processing, count today's emissions:

```sql
SELECT count(*) AS today_count
FROM public.asset_documents
WHERE extraction_method = 'cowork_backfill'
  AND created_at::date = (now() AT TIME ZONE 'UTC')::date;
```

If `today_count >= 500`, stop with a one-line note "daily quota reached" and return. Do not start any new classifications.

**Schema note.** The CHECK constraints on `asset_documents.extraction_method` and `asset_linker_runs.pass` must include `'cowork_backfill'` before this skill can INSERT — the bundling migration `20260522000010_asset_linker_cowork_backfill_extraction_method.sql` adds it. If you get a check_violation on `extraction_method`, that migration has not been applied yet; stop and surface the issue.

### 2. Load the asset directory

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

### 3. Prefilter each document (no LLM yet)

For each document from step 1, lowercase the text and check substring presence of any asset keyword. Build the per-doc candidate-asset list:

- If a keyword appears in the doc text → add that asset to the doc's candidate list.
- If the candidate list is empty → **skip this doc, do NOT classify, do NOT INSERT anything**.

Track skipped-by-prefilter count for the run summary. Most docs should skip here.

### 4. Classify each prefilter-passed document

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

If the doc references the candidate asset(s) only in boilerplate / non-investor-grade context, emit `{"links": []}` — that's the correct answer, not a failure.

### 5. Insert links

For each link in the doc's output (skip docs with empty `links` arrays — no INSERT):

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

### 6. Emit a run summary

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

### 7. Report back to chat

One-line summary: `processed N docs, prefilter passed M, links inserted K, daily quota X/500`.

If you hit any anomalies (doc text unreadable, asset directory empty, RPC failures), surface them — do not silently swallow.

## Things you must NOT do

- Don't query `documents` with `LIMIT > 50` (Cowork session memory + reasoning bandwidth).
- Don't classify any doc that the prefilter rejected — that's the whole point of this skill (no waste).
- Don't write `extraction_method='agent_pass1'` or `'agent_pass2'` — those are reserved for the Modal worker.
- Don't run pass-2 verification logic — that's the Modal worker's job (`asset_linker.py:580+`), and it'll need to be re-enabled separately if the team decides pass-2 still adds value over the cowork emissions.
- Don't touch `extracted_facts`, `convergence_assessments`, `orchestrator_runs`, or any downstream table.
- Don't auto-recreate or unpause any paused cron jobs.

## Provenance & audit

Every row this skill emits has `extraction_method='cowork_backfill'`. Operators can audit Cowork emissions vs Modal emissions with:

```sql
SELECT extraction_method, count(*), max(linked_at)
FROM public.asset_documents
GROUP BY extraction_method
ORDER BY count(*) DESC;
```

If a Cowork link is later found to be wrong, it can be flipped to `is_material=false` (preserves audit trail) — do not DELETE.
