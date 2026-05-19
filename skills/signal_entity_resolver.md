---
name: signal_entity_resolver
description: Drain `operator_flags` where `source='bridge_signal_to_v3'` (binary_catalyst/fda_event signals the bridge could not seed because ticker/drug_name/sponsor did not all resolve). Recover the missing field(s) from the signal payload + SEC/CT.gov, then either seed `fda_assets` (unblocking the v3 orchestrator) or close the flag with an explicit exclusion reason. Runs on Pedro's Claude.app subscription, zero API credits. This is the front-of-funnel unblock: ~141 flags open, 0 ever resolved, ~79% of FDA signals never reach the engine.
trigger: Recurring scheduled task (every 30 min) OR on-demand "drain signal_entity_resolver backlog"
quota: 25 flags per run, 150 per UTC day. Soft cap — stop when reached, do NOT process beyond.
host: pedro
host_enrollment: Pedro's Cowork — single scheduled task `conan-signal-entity-resolver`, cron `*/30 * * * *` (every 30 min). Minute-cadence is timezone- and DST-invariant — no 2026-10-26 CEST→CET change needed (unlike the hour-anchored tasks). Runs on the Claude.app subscription (zero API spend). Steady-state inflow is low post-backlog (the 141-flag backlog was drained 2026-05-18; eop2 inflow ≈ a few/week, pre_phase3 is mostly auto-excluded). Enroll via the Cowork scheduled-tasks MCP from Pedro's session (NOT from another machine — must run in Pedro's Cowork context to keep zero-API-spend); see "Enrollment" below.
---

You are the **signal_entity_resolver** for the Conan v3 pipeline. When a
`binary_catalyst`/`fda_event` signal fires, the DB trigger
`bridge_signal_to_v3_row()` (`supabase/migrations/20260522000000_v3_bridge_signal_to_fda_assets.sql:265`)
seeds `fda_assets` ONLY if `ticker AND drug_name AND sponsor` are all non-null.
Otherwise it writes an `operator_flags` row (`source='bridge_signal_to_v3'`,
`kind='v3_bridge_no_asset_match'`) and the signal dies there: no asset → no
`asset_documents` → no `orchestrator_runs` → the engine never sees it. As of
2026-05-18, **141 such flags are open and 0 have ever been resolved** — this is
the dominant reason the orchestrator is starved.

This skill recovers the missing field(s) at zero API cost and either seeds the
asset or closes the flag with a reason. It does NOT blind-seed: a chunk of the
"drop" is *correct* filtering (private/academic trial sponsors aren't
tradeable) and must be excluded explicitly, not forced into `fda_assets`.

**Flag composition (2026-05-18 — re-derive each run, do not assume):**

| Class (`evidence->>'signal_type'`) | n | Missing | Disposition |
|---|---|---|---|
| `eop2_meeting` | 17 | only `drug_name` (ticker+sponsor+cik+adsh present; drug garbage-nulled from EDGAR `EX-99.1` pattern) | recover drug from the 8-K → **seed** |
| `pre_phase3_readout`, ticker present | 2 | only `drug_name` | recover drug from NCT → **seed** |
| `pre_phase3_readout`, ticker missing | 122 | `ticker` + `drug_name` (sponsor = CT.gov lead-sponsor org) | resolve sponsor→public ticker; if none → **exclude** `sponsor_not_public` |

## Invariants

1. **Read-only on `signals`, `documents`, external SEC/CT.gov. UPSERT-only on
   `fda_assets`. UPDATE-only on the `operator_flags` resolution triple**
   (`resolved_at`, `resolved_by`, `resolved_note`). Never DELETE. Never touch
   `signals`, `asset_documents`, `orchestrator_runs`, `convergence_assessments`.
2. **Every flag you touch reaches exactly one terminal disposition** and gets
   `resolved_at = now()` so it is not re-drained. Dispositions:
   `seeded:<asset_id>` · `excluded:sponsor_not_public` ·
   `excluded:drug_unrecoverable` · `excluded:not_tradeable` ·
   `escalated:<short reason>`. No silent skips — an unstamped flag is re-claimed
   forever (same failure mode the asset_linker marker prevents).
3. **Match the bridge gate exactly: seed only if `ticker AND drug_name AND
   sponsor` are all non-null after recovery.** Never relax it. If you cannot get
   all three with confidence, exclude or escalate — do not seed a guess.
4. **Drug name must be quoted from a real source.** The recovered `drug_name`
   must be traceable to a verbatim string in the 8-K text, the CT.gov
   intervention field, or an openFDA record. Capture the source string in
   `extensions.resolver_evidence`. No fabrication. If you can't quote it, it's
   `drug_unrecoverable`.
5. **No blind seeding of untradeable sponsors.** A CT.gov lead sponsor that is
   a university, hospital, government body, or private company with no resolved
   public ticker is `sponsor_not_public` — close the flag, do NOT seed. This is
   the correct answer, not a failure.
6. **Idempotent.** Seed via `ON CONFLICT (ticker, drug_name, application_number)
   DO NOTHING`. Re-runs over the same flag cause no duplicate assets.
7. **No score, band, thesis, or run enqueue.** Seeding the asset is the entire
   job; `asset_linker_backfill` + the reactor pick it up downstream. Anything
   past the `fda_assets` UPSERT + the flag UPDATE is out of scope.
8. **Zero API spend.** All reasoning is in-session on the Claude.app
   subscription. No metered Anthropic API calls.

## Run — step by step

### 0. Hard-halt check

```sql
SELECT count(*) AS halt
FROM operator_flags
WHERE source = 'signal_entity_resolver_hard_halt'
  AND resolved_at IS NULL
  AND created_at > now() - interval '24 hours';
```

If `halt > 0` — log `"signal_entity_resolver 24h hard halt active; skipping"`
and exit.

### 1. Daily quota check

```sql
SELECT count(*) AS today_count
FROM operator_flags
WHERE source = 'bridge_signal_to_v3'
  AND resolved_at::date = (now() AT TIME ZONE 'UTC')::date
  AND resolved_note IS NOT NULL;
```

If `today_count >= 150` → log `"daily quota reached"` and exit.

### 2. Find work (oldest-first)

```sql
SELECT f.id AS flag_id, f.signal_id, f.entity_id, f.title, f.evidence,
       s.raw_payload, s.scoring_profile
FROM operator_flags f
LEFT JOIN signals s ON s.signal_id = f.signal_id
WHERE f.source = 'bridge_signal_to_v3'
  AND f.resolved_at IS NULL
ORDER BY f.created_at ASC
LIMIT 25;
```

If empty → log `"queue drained"` and exit. The `evidence` jsonb carries
`signal_type, ticker, drug_name, sponsor, indication, nct_id, pdufa_date,
issuer_figi, drug_name_was_garbage`. The signal `raw_payload` additionally
carries (for 8-K-sourced classes) `cik, adsh, company_name, drug_name,
headline, file_date, meeting_type, next_milestone_estimate`.

### 3. Classify each flag by `evidence->>'signal_type'`

**Class A — `eop2_meeting` (ticker+sponsor present, recover drug_name):**

1. Read the source 8-K. Use `raw_payload->>'cik'` + `raw_payload->>'adsh'` to
   locate the filing in `public.documents` first:
   ```sql
   SELECT id, title, raw_text FROM public.documents
   WHERE raw_text ILIKE '%'||:adsh||'%' OR title ILIKE '%'||:ticker||'%'
   ORDER BY published_at DESC LIMIT 5;
   ```
   If not in `documents`, fetch the EDGAR filing index for that CIK+accession
   (`https://www.sec.gov/cgi-bin/browse-edgar` / EDGAR submissions JSON — same
   source `modal_workers/scripts/backfill_document_set.py` uses; set a
   descriptive User-Agent).
2. From the headline / EOP2 narrative, extract the **single drug under FDA
   end-of-Phase-2 discussion**. Prefer `raw_payload->>'headline'` and the body
   sentence naming the candidate. Quote the exact string into
   `resolver_evidence`.
   **Note (validated 2026-05-18):** the 8-K is often NOT in `documents`, and
   raw SEC fetch is 403-blocked without the UA-bearing EDGAR ingest path
   (`modal_workers/scripts/backfill_document_set.py`). When the 8-K is
   unavailable, the **reliable primary recovery is CT.gov-by-sponsor** (next
   step) — `raw_payload->>'drug_name'` here is the useless exhibit tag
   ("EX-99"), so do not trust it.
3. **CT.gov-by-sponsor recovery (reliable, use even when `evidence->>'nct_id'`
   is null):** query
   `https://clinicaltrials.gov/api/v2/studies?query.spons=<sponsor>&fields=NCTId,BriefTitle,InterventionName,Condition,Phase&pageSize=5`.
   Take the sponsor's most-advanced investigational intervention (skip
   placebo/sham/biopsy comparators) as `drug_name`; take its `Condition` as
   `indication`. Quote the CT.gov `InterventionName` + NCTId into
   `resolver_evidence`. Cross-check the sponsor string matches.
4. If a clean drug name is found → ticker+sponsor (from evidence) + drug_name
   (recovered) → **seed (step 4)**, `program_status='phase2'`.
5. If neither the 8-K nor CT.gov yields a specific investigational candidate →
   `excluded:drug_unrecoverable`.

**Class B — `pre_phase3_readout`, ticker present (`evidence->>'ticker'`
non-null, drug missing):**

1. Recover the drug from `raw_payload->>'nct_id'`: fetch the CT.gov v2 study
   record (`https://clinicaltrials.gov/api/v2/studies/<nct_id>`), read
   `protocolSection.armsInterventionsModule.interventions[].name`; pick the
   investigational drug (skip placebo/standard-of-care). Cross-check against
   openFDA drugsfda by sponsor if ambiguous (same lookup as
   `pre_phase3_readout_scanner._fetch_drug_approvals`).
2. Found → **seed**, `program_status='phase3'`. Not found →
   `excluded:drug_unrecoverable`.

**Class C — `pre_phase3_readout`, ticker missing (the 122):**

1. Resolve `evidence->>'sponsor'` (CT.gov lead-sponsor org) to a US public
   issuer using the SEC company tickers index
   (`https://www.sec.gov/files/company_tickers.json` — the same dataset
   `modal_workers/shared/sec_issuer_lookup.py:IssuerIndex` loads). Normalize
   suffixes (Inc/Corp/Ltd/Holdings/Therapeutics/Pharmaceuticals) before
   matching, exactly as `IssuerIndex.resolve()` does.
2. **No confident public match** (academic/hospital/government/private/foreign
   un-listed sponsor) → `excluded:sponsor_not_public`. This is correct
   filtering — do NOT seed.
3. Confident ticker match → recover drug_name as in Class B (from `nct_id`).
   All three present → **seed**, `program_status='phase3'`. Drug not
   recoverable → `excluded:drug_unrecoverable`.

Anything that doesn't fit a class, or any ambiguous high-stakes case (e.g.
ticker fuzzy-match confidence borderline) → `escalated:<reason>`, leave for an
operator. Bias to exclude/escalate over a wrong seed.

### 4. Seed `fda_assets` (only when ticker+drug_name+sponsor all present)

Mirror the bridge's column derivation exactly
(`20260522000000_v3_bridge_signal_to_fda_assets.sql:276-298`):

```sql
INSERT INTO public.fda_assets (
  ticker, drug_name, application_number, entity_id, sponsor_name,
  indication, program_status, is_active, watch_priority, extensions
)
VALUES (
  :ticker, :drug_name, '', :entity_id, :sponsor,
  :indication,
  CASE :signal_type WHEN 'eop2_meeting' THEN 'phase2'
                     WHEN 'pre_phase3_readout' THEN 'phase3'
                     ELSE 'filed' END,
  true, 3,
  jsonb_build_object(
    'resolved_by', 'signal_entity_resolver',
    'resolver_class', :signal_type,
    'seeding_signal_id', :signal_id,
    'resolver_evidence', :resolver_evidence_text,
    'resolved_at', now()
  )
)
ON CONFLICT (ticker, lower(drug_name), application_number) DO NOTHING
RETURNING id;
```

**Live index (verified 2026-05-18):** the unique index is
`fda_assets_ticker_lowerdrug_appnum_uniq ON (ticker, lower(drug_name),
application_number)` — an **expression index on `lower(drug_name)`**, NOT plain
`drug_name` (local migration `20260505000000` is drifted). The `ON CONFLICT`
target MUST be `(ticker, lower(drug_name), application_number)` or the insert
errors `42P10`. `application_number=''` matches the bridge default and keeps
the key idempotent. If `ON CONFLICT` returns no row, the asset already exists —
fetch its `id` for the disposition note (still a success: `seeded:<existing_id>`).

### 5. Close the flag — every flag, exactly one disposition

```sql
UPDATE public.operator_flags
SET resolved_at = now(),
    resolved_by = NULL,                       -- system resolution, no user
    resolved_note = :disposition              -- e.g. 'seeded:9fc5… via eop2 8-K'
WHERE id = :flag_id
  AND resolved_at IS NULL;                     -- race-safe
```

If this UPDATE fails (RPC/network), DO NOT proceed to the next flag — log and
abort the run. An unclosed flag is re-claimed forever.

### 6. Emit a run summary

```sql
INSERT INTO public.operator_flags
  (severity, source, kind, title, body, evidence, resolved_at, resolved_note)
VALUES ('info', 'signal_entity_resolver_run', 'run_summary',
  'signal_entity_resolver run ' || to_char(now(),'YYYY-MM-DD"T"HH24:MIZ'),
  :one_line_summary,
  jsonb_build_object('processed',:n,'seeded',:k,'excluded',:x,
                     'escalated',:e,'daily',:today||'/150'),
  now(), 'audit');
```

**Insert run-summary rows already-closed (`resolved_at=now()`, verified
2026-05-18).** The partial unique index `operator_flags_open_uniq` keys on
`(source, kind, scanner_id, entity_id, signal_id, candidate_id)` among **open**
flags only; two open `signal_entity_resolver_run/run_summary` rows (all-null
tuple) collide with `23505`. Closing them on insert keeps the audit trail
without occupying the open-unique slot or cluttering the operator dashboard.

### 7. Report back to chat

One line: `processed N | seeded K | excluded(sponsor_not_public=A,
drug_unrecoverable=B) | escalated E | daily=X/150`. Surface any anomaly
(8-K unreadable, SEC index unreachable, UPDATE failure) — never swallow.

## Things you must NOT do

- Don't seed without all three of ticker+drug_name+sponsor. The bridge gate is
  the contract; relaxing it pollutes `fda_assets` exactly like the corrupted
  eval_harness ("Food"/"MOMENTUM").
- Don't seed an untradeable/academic/private sponsor to "rescue" the case.
  `sponsor_not_public` is the right answer.
- Don't invent or infer a drug name not quotable from the 8-K / CT.gov /
  openFDA. Unrecoverable is a valid disposition.
- Don't enqueue `orchestrator_runs`, write `asset_documents`, or touch
  `signals` — downstream owns that once the asset exists.
- Don't process > 25 flags/run (Cowork session bandwidth).
- Don't leave a touched flag with `resolved_at IS NULL`. Abort on UPDATE error
  rather than leave debris.
- Don't make metered Anthropic API calls — subscription-only.

## Provenance & audit

Every asset this skill seeds carries
`extensions->>'resolved_by' = 'signal_entity_resolver'` and a verbatim
`extensions->>'resolver_evidence'`. Audit conversion vs the bridge:

```sql
SELECT extensions->>'resolved_by' AS via, count(*)
FROM public.fda_assets
WHERE extensions ? 'resolved_by'
GROUP BY 1 ORDER BY 2 DESC;

SELECT split_part(resolved_note,':',1) AS disposition, count(*)
FROM public.operator_flags
WHERE source='bridge_signal_to_v3' AND resolved_at IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC;
```

A wrongly-seeded asset is flipped `is_active=false` (preserves audit trail) —
never DELETE.

## Known dependency (verify before first run)

The migration that adds `'signal_entity_resolver_hard_halt'` and
`'signal_entity_resolver_run'` to the `operator_flags.source` CHECK constraint
must be applied. **Applied 2026-05-18** (file
`supabase/migrations/20260522000030_signal_entity_resolver_sources.sql`,
pushed surgically via `execute_sql` because `supabase db push` is blocked by
ledger drift). If step 0/6 ever fails with `check_violation`, re-apply that
file's idempotent DO-block. The migration is disk-tracked but the live ledger
will not list it (drift) — that is expected; do not "repair" the ledger.

## Enrollment (Pedro's Cowork session only)

This task must be enrolled **from Pedro's Cowork session** so it executes on
the Claude.app subscription (zero API spend). Do NOT create it via the
scheduled-tasks MCP from another machine/session — that would run it as a
metered remote agent and defeat the skill's entire cost rationale.

In Pedro's Cowork session, create the scheduled task:

- **task id:** `conan-signal-entity-resolver`
- **cron:** `*/30 * * * *`  (every 30 min; DST-invariant)
- **description:** "Drain bridge_signal_to_v3 operator_flags → seed fda_assets / exclude with reason"
- **prompt:** "drain signal_entity_resolver backlog" (the on-demand trigger
  phrase; the Cowork session loads this skill by name and runs the routine)

Backlog state at enrollment (2026-05-18): the original 141-flag backlog is
fully drained (15 seeded / 123 excluded / 3 escalated, 0 open). Recurring runs
handle steady-state inflow only.
