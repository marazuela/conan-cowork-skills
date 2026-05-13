---
name: harvest-historical-events
description: Generic resumable historical event backfill engine. Parameterized by profile, filing types, date range, and target n. Pulls from SEC EDGAR full-text search, FDA approvals, and CourtListener. Atomic-write, rate-limit respecting, checkpointed every 50 events. OpenFIGI ticker resolution with JP 5-char fix per Q-003.
type: skill
---

# harvest-historical-events

## Purpose

Generic event harvest engine that backfills historical primary-source filings across the five in-scope investment profiles. Replaces and generalizes the one-off `step_p1_03_harvest.py` shape so a single tool can serve every profile in the calibration ledger build, every iteration of feature engineering, and every backfill request the scoring framework requires.

This is the data-supply layer for the Phase-3 methodology stack: M1 produces raw events, M2 (label-outcomes-from-prices) labels them, M3 (extract-event-features) enriches them. Without M1, neither downstream skill has anything to operate on.

The skill is invoked when:
- A profile's bucket in `historical_events_ledger.json` is below its `targets_per_bucket` floor and needs additional events.
- Iteration-N feature engineering wants a fresh harvest scoped to a specific window for replay.
- The `learning_loop` triggers a backfill for a newly added scanner type.
- The user manually requests a parameterized harvest for ad-hoc analysis.

## Inputs

Required (positional or `--key=value`):
- `profile` — one of `merger_arb`, `activist_governance`, `binary_catalyst`, `litigation`, `insider`. (`short_positioning` is explicitly out-of-scope per the plan.)
- `filing_types` — comma-separated list of source-specific filing types, e.g. for `merger_arb`: `DEFM14A,S-4,SC TO-T,SC 13E3`. Profile-default lists are documented in `helpers/profile_defaults.py` and used when this argument is omitted.
- `date_range` — ISO range `YYYY-MM-DD..YYYY-MM-DD` (inclusive on both ends).
- `target_n` — integer, total events to harvest before declaring `completed`.

Optional:
- `resume_from_checkpoint` — path to a prior checkpoint file. If present, the run continues from the last persisted bucket cursor. Default: auto-detect from `outputs/<profile>_<run_id>_checkpoint.json`.
- `run_id` — string, defaults to `<profile>_<date_range_hash8>`.
- `max_events_per_invocation` — soft cap on events written per process invocation. Default 150 (matches the bumped budget in `step_p1_03_harvest.py`).
- `wall_clock_budget_s` — soft cap on wall-clock seconds before checkpoint-and-exit. Default 20.0 (leaves headroom under the 35s scheduler timeout).
- `user_agent` — SEC fair-access User-Agent. Default `Investment-Tool-Skill harvest-historical-events research@local`.

## Outputs

Two files per run, both atomically written:

1. `skills/harvest-historical-events/outputs/<profile>_<run_id>_events.json`
   - Top-level: `{"schema_version": 1, "profile": ..., "run_id": ..., "events": [...]}`
   - Each event includes: `event_id` (sha1 of `accession + cik + filing_type` for SEC; sha1 of `application_number` for FDA; sha1 of `docket_id + court` for litigation), `bucket`, `form_type`, `filed_at`, `cik`, `ticker`, `figi` (or null), `company_name`, `accession_number_or_id`, `primary_source_url`, `features`, `confidence`, `source`, `harvested_at`, `harvester`.

2. `skills/harvest-historical-events/outputs/<profile>_<run_id>_checkpoint.json`
   - Top-level: `{"profile": ..., "run_id": ..., "started_at": ..., "last_updated_at": ..., "buckets_completed": [...], "next_bucket": {...}, "events_written": N, "target_n": M, "status": "in_progress"|"completed"|"error", "errors": [...]}`
   - Bucket cursor format: `{"source": "edgar"|"fda"|"courtlistener", "form_type": ..., "year": YYYY, "month": MM, "page": P}`. SEC EDGAR is paginated by month per form_type. FDA approvals by year. CourtListener by year + case_type.
   - Atomic-write requirement: temp file + `os.replace` per `helpers/atomic_write.py` (D-052).

3. Final stdout line — single-line JSON summary:
   ```json
   {"status": "in_progress|completed|error", "events_written_this_invocation": N, "events_total": K, "target_n": M, "next_bucket": {...}, "duration_s": X, "rate_limit_hits": Y}
   ```

## Methodology

### 1. Profile-keyed source dispatch

```
merger_arb        → SEC EDGAR full-text search (efts.sec.gov)
activist_governance → SEC EDGAR full-text search (13D family)
insider           → SEC EDGAR submissions API (Form 4 cluster detect — see §1.3)
binary_catalyst   → FDA approvals API (api.fda.gov/drug/drugsfda) + AdCom Federal Register
litigation        → CourtListener API (courtlistener.com) — auth-required path; FDA enforcement signals as fallback
```

The orchestrator (`helpers/harvest.py`) dispatches the profile to its source adapter (`helpers/source_<src>.py`). Each adapter is responsible for:
- Building the bucket queue (form_type × year × month, or year × case_type).
- One-bucket-at-a-time querying with rate-limit + retry semantics.
- Returning `[event_dict, ...]` per bucket.

### 1.1 SEC EDGAR full-text search

Endpoint: `https://efts.sec.gov/LATEST/search-index`
Query params: `q` (keyword, blank for form-only), `forms` (CSV), `dateRange=custom`, `startdt`, `enddt`, `from` (pagination offset, page size 10).
User-Agent: required by SEC fair-access policy. Pull from `--user-agent` flag.
Rate limit: SEC publishes 10 req/s as the ceiling; we throttle to 2 req/s (THROTTLE_SLEEP_S=0.5). On HTTP 429 or 403, exponential backoff with jitter (1s, 2s, 4s, then surface error).
Pagination: `from=0,10,20,...` until `hits.total.value` exhausted or our budget hits.

Per hit, we extract: `accession`, `cik`, `form_type`, `filed_at`, `display_names` (company name + ticker tokens). The `display_names` field carries embedded tickers in parens — we parse them but treat as best-effort, not authoritative.

### 1.2 SEC EDGAR submissions API (insider Form 4 path)

Endpoint: `https://data.sec.gov/submissions/CIK<10-digit>.json`
Used when we have a CIK universe in scope (e.g., S&P 500 constituents). Filters `recent.form` for Form 4 entries within the date range, then fetches each Form 4 primary doc to extract transaction codes, $ value, and shares.

Cluster detection (per `profile_insider.md`): a "cluster" is ≥2 Form 4s from distinct insiders of the same issuer within a 14-day window, of the same transaction-code class (P=open-market purchase, A=grant, S=open-market sale). The harvester emits one event per cluster, not per individual Form 4, with `features.cluster_size` and `features.cluster_member_ciks` carrying the underlying detail.

### 1.3 FDA approvals API

Endpoint: `https://api.fda.gov/drug/drugsfda.json`
Query: `search=submissions.submission_status_date:[<start>+TO+<end>]+AND+submissions.submission_type:NDA` (or BLA, sNDA, etc., per `filing_types`).
Per record: `application_number`, `sponsor_name`, `submissions[].submission_status` (AP=approved, TA=tentatively approved, RTF=refused-to-file, etc.), `submissions[].submission_status_date`, `products[].product_number`, `products[].active_ingredients`, `openfda.brand_name`, `openfda.generic_name`, `openfda.pharm_class_epc`/`pharm_class_moa`.

Approval events are mapped to `binary_catalyst` bucket. CRL/RTF/TA are kept as separate `outcome.label` candidates for M2 to resolve.

AdCom Federal Register cross-reference: per `analyze-fda-approval-prospects` (P1) skill, AdCom convened/not data is enriched from a separate Federal Register fetch using docket-number-keyed lookup. M1 emits the application without AdCom data; M2/M3 join later.

### 1.4 CourtListener API

Endpoint: `https://www.courtlistener.com/api/rest/v3/dockets/`
Auth: `Authorization: Token <token>` header from `02_System/engine/config/secrets.env` (read-only — secrets.env is reference only; this skill expects the token to be made available via `COURTLISTENER_TOKEN` env var or via a copy of `secrets.env` in the working folder).
If the token is missing, the adapter returns `{"status": "auth_required", "registration_url": "https://www.courtlistener.com/help/api/rest/", "secrets_env_path": "<working>/secrets.env"}` and the orchestrator records a `recoverable: true` error and skips the litigation adapter for that invocation. Other adapters (EDGAR, FDA) continue.
Per docket: `id`, `court`, `date_filed`, `case_name`, `nature_of_suit`, `cause`, `parties`, `attorneys`, `clusters` (opinions). We map `nature_of_suit` to `case_type` (securities, patent, antitrust, breach-of-contract, ITC-337, PTAB, Delaware Chancery).

### 2. Ticker resolution via OpenFIGI

After raw harvest, for each event with a `cik` we resolve the issuer's primary ticker:
1. EDGAR submissions API gives us the issuer's `tickers[]` array directly — preferred path.
2. If no ticker on EDGAR, query OpenFIGI (`https://api.openfigi.com/v3/mapping`) with `idType=cikId, idValue=<padded-10-digit-cik>`.
3. JP 5-character ticker fix (Q-003 in OPEN_QUESTIONS): for foreign filers where EDGAR tickers are missing, OpenFIGI returns 5-char ticker codes for JP exchanges (e.g., `6027 (Bengo4.com)`). The OpenFIGI response field is `ticker`; we strip exchange suffixes and keep the numeric MIC-disambiguated form. Per CLAUDE.md §1.7, we render numeric tickers with company names downstream — but the raw `events.json` carries the bare ticker; the rendering layer (M3 / dossier authoring) is responsible for the `<ticker> (<company>)` format.
4. Hard-cap of 100 OpenFIGI calls per invocation (their public limit) — once exceeded, remaining tickers are left as null with `features.ticker_resolution_skipped: 1` and resumed in the next invocation via checkpoint.

### 3. Deduplication

Within an invocation: `event_id` set membership. Across invocations: re-read existing `<run_id>_events.json` (if present) and skip already-harvested IDs.

`event_id` derivation:
- SEC: `sha1(accession + cik + form_type)[:24]`
- FDA: `sha1(application_number + submission_type + submission_status_date)[:24]`
- CourtListener: `sha1(docket_id + court_id)[:24]`
- Insider cluster: `sha1(issuer_cik + cluster_window_start + cluster_window_end + cluster_kind)[:24]`

Across-source collision is structurally impossible because the inputs differ.

### 4. Bounded budget + checkpointing

Per CLAUDE.md §3 invariants:
- `WALL_CLOCK_BUDGET_S = 20.0` typical, hard-cap 60s. The orchestrator checks elapsed before each bucket query. If elapsed > budget, write checkpoint and exit `in_progress`.
- `MAX_EVENTS_PER_INVOCATION = 150`. Once reached, write checkpoint and exit.
- Checkpoint frequency: every 50 events OR every bucket boundary OR before any error-exit.
- Atomic write for both events.json and checkpoint.json (temp file → fsync → os.replace).
- Idempotent re-run: invoking with the same `run_id` reads existing events.json + checkpoint.json and continues.

### 5. Confidence scoring

Per-event `confidence` scoring rules:
- **0.95** — Filing has CIK + ticker + filed_at + form_type, all from primary source. (e.g., EDGAR DEFM14A with confirmed ticker via submissions API.)
- **0.85** — Filing has CIK + form_type + filed_at but ticker resolution failed or returned null. (Most international filers, SPAC pre-merger, etc.)
- **0.75** — FDA approval with sponsor + application + decision, no CRL grounds detail.
- **0.70** — CourtListener docket with case_type + parties resolved + nature_of_suit, but no opinion text.
- **0.50** — Bucket queried but partial data (e.g., display_name parse failed). Marked for review.
- **<0.50** — Discard; do not write to events.json.

Every row also has `source` = primary_source_url. Per CLAUDE.md §1.6, no row is unlabeled.

### 6. Error handling

Three error classes:
- **Recoverable HTTP error** (429, 503, network blip) — backoff + retry up to 3 times, then surface as `errors[]` entry on the checkpoint and continue with next bucket.
- **Auth-required** (CourtListener token missing) — return adapter `auth_required` status, log to checkpoint, skip litigation for this invocation, continue with EDGAR/FDA. `recoverable=true`.
- **Schema-breaking** (response shape changed, missing required fields) — abort that bucket, log full error context to checkpoint, return exit code 1 with `recoverable=false` so the meta-scheduler can disable the task after 3 strikes (per CLAUDE.md §5.1).

HALT_FLAG check at orchestrator entry — if `02_System/engine/health/HALT_FLAG` is present in the **reference folder** (read-only), exit immediately with `{"status": "halted", "halt_reason": ...}`. Note: this skill's mirror under the working folder must consult the reference flag because that is the authoritative source.

### 7. Atomic write (D-052)

```python
def atomic_write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
```

Same pattern as `helpers/atomic_write.py` reused in U4/P3/P4/P5. The orchestrator imports from sibling skill if available; otherwise carries its own copy.

### 8. Profile-specific filing-type defaults

Documented in `helpers/profile_defaults.py`:
- `merger_arb`: `DEFM14A`, `S-4`, `S-4/A`, `SC TO-T`, `SC TO-I`, `SC 13E3`, `8-K M&A item`
- `activist_governance`: `SC 13D`, `SC 13D/A`, `DEF 14A` (proxy with activist nominations), `PRRN14A`
- `insider`: `Form 4` (with cluster detection per §1.3)
- `binary_catalyst`: FDA `NDA`, `BLA`, `sNDA`, `sBLA`, `RTF`, `CRL` (where public)
- `litigation`: CourtListener `case_types` = `securities`, `patent`, `antitrust`, `breach`, `337`, `ptab`, `delaware-chancery`

## Profile-specific application

Different profiles emit different `features` dicts on each event so M3 (extract-event-features) can compute iter-4 features deterministically. Minimum required features per profile:

**merger_arb**: `is_definitive_form` (bool), `is_amendment_form` (bool), `target_cik`, `acquirer_cik` (if parseable from filing), `deal_value_usd` (if available), `primary_sic_2dig`.

**activist_governance**: `is_initial_13d` (bool), `is_amendment` (bool), `position_size_pct` (parsed from filing), `position_size_shares`, `is_group_filing` (bool, true if multiple reporting persons).

**insider**: `cluster_size` (int), `cluster_member_ciks` (list), `cluster_kind` ('P', 'A', 'S'), `cluster_total_shares`, `cluster_total_dollars`.

**binary_catalyst**: `submission_type`, `submission_status`, `application_number`, `sponsor_cik`, `pharm_class_epc`, `pharm_class_moa`, `is_priority_review` (bool), `is_breakthrough` (bool).

**litigation**: `nature_of_suit`, `case_type`, `is_district` (bool), `is_appellate` (bool), `n_parties`, `n_attorneys`, `is_class_action` (bool), `is_securities_act_cause` (bool), `is_exchange_act_cause` (bool), `is_antitrust_cause` (bool).

## Output schema

```json
{
  "schema_version": 1,
  "profile": "merger_arb",
  "run_id": "merger_arb_2020-2024_a1b2c3d4",
  "started_at": "2026-04-29T02:35:00Z",
  "last_updated_at": "2026-04-29T02:39:12Z",
  "events_total": 20,
  "target_n": 20,
  "status": "completed",
  "events": [
    {
      "event_id": "60a8e8f60f3d2ab13c03a62a",
      "bucket": "ma",
      "form_type": "S-4/A",
      "filed_at": "2024-01-09",
      "cik": "0001870404",
      "ticker": "CERO",
      "figi": "BBG01HXXXXXX",
      "company_name": "PHOENIX BIOTECH ACQUISITION CORP.",
      "accession_number_or_id": "0001213900-24-002005",
      "primary_source_url": "https://www.sec.gov/Archives/edgar/data/1870404/000121390024002005/0001213900-24-002005-index.htm",
      "features": {
        "form": "S-4/A",
        "is_definitive_form": 0,
        "is_amendment_form": 1,
        "target_cik": "0001870404",
        "acquirer_cik": null,
        "deal_value_usd": null,
        "primary_sic_2dig": "28"
      },
      "confidence": 0.85,
      "source": "https://www.sec.gov/Archives/edgar/data/1870404/000121390024002005/0001213900-24-002005-index.htm",
      "harvested_at": "2026-04-29T02:36:18Z",
      "harvester": "harvest-historical-events.v1"
    }
  ]
}
```

## Worked example

Test candidate per the plan: `profile=merger_arb, filing_types=[DEFM14A, S-4], date_range=2020-01-01..2024-12-31, target_n=20`.

Expected behavior:
1. Bucket queue built: 5 years × 12 months × 2 form_types = 120 buckets.
2. First invocation: harvests up to MAX_EVENTS_PER_INVOCATION (150) — but target_n=20 caps earlier.
3. EDGAR full-text returns DEFM14A hits for 2020-01: ~5 events. S-4 hits for 2020-01: ~10 events. Continue until 20 events written.
4. OpenFIGI ticker resolution per event (capped at 100 calls).
5. Atomic write events.json with 20 events; checkpoint.json with `status=completed, events_total=20, target_n=20`.
6. stdout: `{"status": "completed", "events_written_this_invocation": 20, "events_total": 20, ...}`

For the smoke test we'll run an offline-illustrative variant (since the SEC EDGAR endpoint may be rate-limited or unreachable from the sandbox): the orchestrator's `--mode=offline` flag injects a fixture of 20 hand-curated events from the existing `iteration_4_merger_arb_features.json` (which contains real harvested events from prior runs in the reference repo). The offline mode validates the orchestrator's structure, dedup, atomic-write, and checkpoint logic without depending on network.

## Failure modes and recovery

| Failure | Detection | Response |
|---|---|---|
| SEC 429 rate limit | HTTP status | Backoff 1s/2s/4s, then surface as recoverable error, checkpoint, continue with next bucket |
| SEC 403 (UA missing) | HTTP status | Hard error; user-agent must be configured. Exit code 1, recoverable=true (config fix) |
| Network DNS/timeout | URLError exception | Same as 429 path — backoff + retry |
| OpenFIGI 429 | HTTP status | Skip ticker resolution for remaining events this invocation, set `features.ticker_resolution_skipped=1`, continue |
| CourtListener token missing | env var absent | Adapter returns `auth_required`, orchestrator skips litigation adapter, continues with EDGAR/FDA |
| EDGAR response schema change | KeyError on `hits` | Hard error with full payload logged. recoverable=false. After 3 strikes, meta-scheduler disables the task |
| Disk full | OSError on atomic_write | Write HALT_FLAG, exit code 1 |
| Partial write | Process killed mid-write | os.replace is atomic — either tmp file remains (orphaned, cleaned on next invoke) or final file is the new content. No partial state |
| Resume after crash | next invocation | Reads checkpoint, continues from `next_bucket` cursor with no duplicate events |

No silent failures. Every failure produces either a row with lowered `confidence` and a `features.partial=1` flag, or a `errors[]` entry on the checkpoint with full context.

## Compliance with system invariants

- **Atomic writes (D-052)**: events.json and checkpoint.json both via temp + fsync + os.replace.
- **Confidence + source on every row (CLAUDE.md §1.6)**: every event in events.json has both fields. No exceptions.
- **Append-only ledger behavior**: this skill writes a per-run events.json (not a global ledger), so append-only doesn't apply at the skill level. Downstream `learning_loop` merges run outputs into `historical_events_ledger.json` with append-only discipline.
- **Never modifies the reference folder**: all writes under `<working>/skills/harvest-historical-events/outputs/`. Reference folder is read-only (only consulted for `historical_events_ledger.json` and `iteration_4_*_features.json` context, not modified).
- **HALT_FLAG aware**: orchestrator checks `<reference>/02_System/engine/health/HALT_FLAG` at startup.
- **Bounded runtime + idempotent + resumable**: all per CLAUDE.md §3.
- **Numeric tickers rendered with company names (CLAUDE.md §1.7)**: M1 emits the bare ticker; the rendering layer (in dossier writers / M3 outputs) is responsible for the `<ticker> (<company>)` form. M1 events.json carries both `ticker` and `company_name` so the rendering layer always has both.
- **No credentials in events.json**: CourtListener token used at request time only, never persisted to outputs. `secrets.env` consulted via env var.
