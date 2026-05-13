---
name: extract-event-features
description: Generalized iter-4 feature engineering — produce a profile-specific prospective feature vector for every labeled event in an M2 outcomes ledger. Mirrors iteration_4_<profile>_features.py exactly, applies the D-097 RESOLVER_LEAKAGE_FEATURES detector, and emits a features matrix plus a feature dictionary.
type: skill
---

# extract-event-features

## Purpose
Generalized feature engineering layer for the calibration/learning pipeline. Given an outcomes ledger (M2 output) and a profile, produce a JSON features matrix (one row per event, columns per feature) plus a feature-dictionary markdown that documents every column. The feature schemas mirror the iter-4 sidecars on disk (`02_System/engine/training/iteration_4_<profile>_features.json`) so this skill's output is drop-in compatible with `learning_loop.augmented_features` and `calibrate_profile`.

Invoked when:
- A new outcomes-labeled batch ships from M2 and feature extraction is the next pipeline step
- A user wants a snapshot feature matrix for a profile to inspect, plot, or hand to K-NN (U3 dependency)
- Iter-4 sidecar regeneration is needed for a re-harvested ledger

## Inputs

| Input | Type | Example |
|-------|------|---------|
| `outcomes_ledger_path` | absolute path to M2 outcomes JSON | `skills/label-outcomes-from-prices/outputs/merger_arb_<run_id>_outcomes.json` |
| `profile` | string in {`merger_arb`, `activist_governance`, `insider`, `binary_catalyst`, `litigation`} | `merger_arb` |
| `enrichment_sidecar` (optional) | absolute path to iter-4 fixture for cross-profile reuse of pre-computed rich_features | `Investment tool backup/02_System/engine/training/iteration_4_merger_arb_features.json` |
| `mode` (optional) | `online` (default) or `offline` (skip network, use sidecar/fixture only) | `offline` |

## Outputs

1. `skills/extract-event-features/outputs/<profile>_<run_id>_features.json` — features matrix
2. `skills/extract-event-features/outputs/<profile>_feature_dictionary.md` — column documentation

Both written atomically (temp file + rename) per CLAUDE.md §1.6 and feedback_edit_tool_null_padding.md.

## Methodology

### 1. Profile dispatch

Each profile has its own feature extractor module under `helpers/feature_extractors_<profile>.py`. The orchestrator reads the outcomes ledger, resolves the matching extractor, and calls `extract(event, sidecar_row)` for each event.

| Profile | Feature schema source | Key features |
|---------|----------------------|--------------|
| merger_arb | `iteration_4_merger_arb_features.json` schema | target_market_cap_log10, price_runup_30d_to_5d, price_5d_post_filed, is_definitive_form, sector one-hots |
| activist_governance | `iteration_4_activist_features.json` | target_market_cap_log10, price_60d_pre_event, price_252d_pre_event, is_underperformer, form_is_initial_13d, sector one-hots (+consumer) |
| insider | `iteration_4_insider_features.json` | is_purchase/sale/award/exercise, role one-hots, value_usd_log10, trade_pct_log10, is_large_trade, price_30d_pre_event, price_5d_pre_event, is_buy_after_dip, avg_volume_log10, market_cap_log10 |
| binary_catalyst | `iteration_4_biotech_prospective_features.json` (Option-B sidecar) | sponsor_p3_track_record, sponsor_p3_prior_count_log, indication_p3_success_rate, indication_p3_pool_size_log, enrollment_zscore_vs_indication, phase2_readout_strength, phase2_prior_count, sponsor_biorxiv_volume_log |
| litigation | derived from profile_litigation.md scoring dims (no on-disk iter-4 sidecar yet) | case_type_token, jurisdiction_token, claim_amount_log10, motion_stage_ord, party_resolution_confidence, ev_pct_claim, days_since_complaint |

### 2. Source priority for each event

For each labeled event in the M2 ledger:
1. **Sidecar lookup**: if `enrichment_sidecar` is provided, find the row whose `accession` matches the event's `accession_number_or_id` (or `cik`+`filed_at` fallback). Use its `rich_features` as the authoritative source of truth.
2. **Inline features**: if no sidecar match, compute the subset that the M2 ledger already carries inline under `event["features"]`.
3. **Default-zero with _imputed=1 flag**: any feature still missing is filled with the schema default (0 for numeric, "" for tokens) and the per-row meta field `_imputed_features` accumulates the missing keys. Confidence on those rows is downgraded (see §6).

### 3. Output row schema

Every output row has:
```json
{
  "event_id": "...",                  // copied from M2
  "profile": "merger_arb",
  "accession": "0001213900-24-002005",
  "cik": "0001870404",
  "filed_at": "2024-01-09",
  "company_name": "PHOENIX BIOTECH ACQUISITION CORP. (CERO, CEROW) (CIK 0001870404)",
  "outcome_label": "HIT",             // copied from M2 outcome.label
  "rich_features": { /* profile-specific feature dict, all numeric except *_token */ },
  "source": {
    "primary_source_url": "...",      // from M2 event
    "enrichment_source": "iter4_sidecar:<path>" | "m2_inline" | "default_zero"
  },
  "confidence": 0.85,                  // see §6
  "_imputed_features": [],             // empty when fully sourced from sidecar
  "harvested_at": "<iso>",
  "harvester": "extract-event-features.v1"
}
```

The full output JSON wraps these in:
```json
{
  "schema_version": 1,
  "profile": "...",
  "run_id": "...",
  "extracted_at": "<iso>",
  "extractor": "extract-event-features.v1",
  "events_total": N,
  "leakage_check": { "verdict": "no_overlap" | "leakage_features_present", "fields": [...] },
  "events": [ ... ]
}
```

### 4. Leakage check (D-097)

After extraction but before write, run `leakage_check.py` to verify no resolver-criterion features have leaked into the feature set.

`RESOLVER_LEAKAGE_FEATURES` (sourced from `02_System/engine/tools/learning_loop.py:819`):
- `binary_catalyst`: `is_completed`, `is_terminated`, `has_results`, `why_stopped_present`
- `merger_arb`, `activist_governance`, `litigation`, `insider`, `short_positioning`: empty sets (resolver criteria are forward returns or future filings, not event-time features)

The check reports either `verdict: no_overlap` (clean) or `verdict: leakage_features_present` with the offending columns. Per D-097, if leakage is present in `binary_catalyst`, the orchestrator emits a confidence downgrade to ≤0.30 and surfaces a top-level warning. The features themselves are NOT auto-stripped here — that is `learning_loop.augmented_features`'s job. This skill's role is to detect and surface.

### 5. Atomic write (D-052)

Output JSON and dictionary markdown are both:
1. Written to `<final_path>.tmp.<pid>`
2. `os.replace` to `<final_path>` — atomic on POSIX, near-atomic on NTFS

### 6. Confidence scoring rules

Per-row confidence:
- `0.85` — all features sourced from iter-4 sidecar (matches sidecar own confidence)
- `0.65` — features partially from sidecar, partially from M2 inline
- `0.45` — features only from M2 inline (no sidecar match)
- `0.25` — `_imputed_features` non-empty (zero-fills used)
- `0.10` — leakage detected for this profile (binary_catalyst with any leakage feature non-zero)

Top-level `leakage_check.confidence_floor` reflects the worst per-row penalty so downstream consumers can filter.

### 7. Feature dictionary

`feature_dictionary_writer.py` emits a markdown doc with one row per feature column:
- Name
- Type (binary, count, log10, ratio, token)
- Range (observed min/max in this run)
- Description
- Source (sidecar field, computed inline, or external API)
- Resolver-leakage flag (true if in RESOLVER_LEAKAGE_FEATURES for this profile)

The dictionary is profile-scoped and re-generated on each run so observed ranges reflect the current sample.

### 8. HALT_FLAG

Orchestrator checks `Investment tool backup/02_System/engine/health/HALT_FLAG` at entry. If present and `mode != offline`, exits with structured `{status: halted, ...}`. Offline mode bypasses for smoke tests.

## Profile-specific application

### merger_arb (15 numeric features + sector token)
| Feature | Type | Source | Notes |
|---------|------|--------|-------|
| ticker | str | sidecar | yfinance symbol |
| no_price_data | bin | sidecar | 1 if yfinance failed |
| target_market_cap_usd | float | sidecar | raw market cap |
| target_market_cap_log10 | log10 | sidecar | log scale |
| has_market_cap | bin | sidecar | |
| price_runup_30d_to_5d | ratio | sidecar | t-30 to t-5 daily return |
| has_runup | bin | sidecar | |
| price_5d_post_filed | ratio | sidecar | t to t+5 daily return |
| has_post5 | bin | sidecar | |
| is_definitive_form | bin | M2 inline OR sidecar | DEFM14A, S-4, SC TO-T |
| is_amendment_form | bin | M2 inline OR sidecar | S-4/A, SC 13D/A |
| target_sector_token | token | sidecar | SIC-derived |
| sector_is_tech | bin | sidecar | one-hot |
| sector_is_healthcare | bin | sidecar | one-hot |
| sector_is_financial | bin | sidecar | one-hot |
| sector_is_industrial | bin | sidecar | one-hot |

### activist_governance (16 numeric + sector token)
Same shape as merger_arb but with 60d/252d underperformance returns instead of runup/post-filing returns, plus `is_underperformer` and `sector_is_consumer`.

### insider (24 numeric features)
Insider-specific transaction features parsed from Form-4 XML; price-based pre-event return windows; volume z-scores.

### binary_catalyst (8 prospective biotech features per Option-B schema)
All sourced from `iteration_4_biotech_prospective_features.json` only.

### litigation (7 numeric + 2 tokens)
Case-stage features per profile_litigation.md. No iter-4 sidecar exists yet — features are computed from M2 inline + dossier metadata only. Sparse-feature flag set per row.

## Output schema

```json
{
  "schema_version": 1,
  "profile": "merger_arb",
  "run_id": "merger_arb_2020-01-01_2024-12-31_51947cf8",
  "source_outcomes_ledger": "<path>",
  "source_enrichment_sidecar": "<path or null>",
  "extracted_at": "2026-04-29T03:35:00+00:00",
  "extractor": "extract-event-features.v1",
  "events_total": 20,
  "label_distribution": {"HIT": 11, "MISS": 9},
  "feature_keys_numeric": ["target_market_cap_log10", "price_runup_30d_to_5d", ...],
  "feature_keys_token": ["target_sector_token"],
  "leakage_check": {
    "verdict": "no_overlap",
    "checked_features": [],
    "resolver_leakage_set": [],
    "confidence_floor": 0.85
  },
  "events": [
    { /* per spec in §3 */ }
  ]
}
```

## Worked example

Inputs:
- `outcomes_ledger_path = skills/label-outcomes-from-prices/outputs/merger_arb_merger_arb_2020-01-01_2024-12-31_51947cf8_outcomes.json` (20 events from M2)
- `profile = merger_arb`
- `enrichment_sidecar = Investment tool backup/02_System/engine/training/iteration_4_merger_arb_features.json` (31 events)
- `mode = offline`

Expected:
- 20 output rows (one per M2 event), 19 sidecar-matched (1 unmatched if accession differs)
- `label_distribution = {HIT: 11, MISS: 9}` mirroring M2 summary
- `leakage_check.verdict = no_overlap` (merger_arb has empty resolver-leakage set)
- Feature dictionary has rows for all 15 numeric features + `target_sector_token`
- Confidence per-row mostly 0.85; `0.45` for any unmatched rows; `confidence_floor = 0.45`

## Failure modes and recovery

| Failure | Detection | Response |
|---------|-----------|----------|
| Outcomes ledger missing | `Path.exists() == False` | exit 1, `recoverable: true`, `error_class: input_missing` |
| Profile out of scope | profile not in {5 in-scope} | exit 1, `recoverable: false`, `error_class: unknown_profile` |
| Sidecar JSON malformed | `json.JSONDecodeError` | warn, fall back to M2 inline; rows mark `enrichment_source: m2_inline` |
| Per-event extractor raises | try/except per event | emit row with `_extractor_error: <msg>`, confidence 0.10, continue |
| Leakage detected (binary_catalyst) | non-zero leakage features in any row | `leakage_check.verdict = leakage_features_present`, write anyway, confidence floor 0.10 |
| HALT_FLAG present + online mode | startup check | exit 0, `status: halted` |
| Output write atomic-rename fails | OSError on `os.replace` | retry once, then exit 1 |
| Empty ledger | `events_total == 0` | exit 0, `status: ok`, write empty matrix + dictionary |

No silent failures — every degradation produces a status row and a confidence drop.

## Compliance with system invariants

- Atomic writes (temp file + `os.replace`) per D-052
- Every output row carries `confidence` (0.0–1.0) and `source` (URL or sidecar path) per CLAUDE.md §1.6
- Numeric tickers preserved with `company_name` field for downstream §1.7 rendering
- Append-only behavior — ledger writes are full overwrites of the dedicated output file, never mutate M2 outcomes
- Never modifies the reference folder — sidecars in `Investment tool backup/` are read-only inputs
- HALT_FLAG honored in online mode
- py_compile clean (`python -m py_compile helpers/*.py` passes)
- D-097 RESOLVER_LEAKAGE_FEATURES detector wired in directly from learning_loop.py:819
- Iter-4 schema mirrored exactly so output is consumable by `learning_loop.augmented_features`
