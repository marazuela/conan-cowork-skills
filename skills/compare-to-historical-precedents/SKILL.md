---
name: compare-to-historical-precedents
description: K-NN reference-class lookup against the historical events ledger. Anchors a candidate's thesis to base rates derived from real precedents — outcome distribution, median return, similarity-weighted aggregates — using profile-weighted feature distance over iter-4 features. Sparse reference classes are surfaced explicitly with a low-density warning rather than silently extrapolated.
type: skill
---

# compare-to-historical-precedents

## Purpose
Given a candidate (ticker/CIK + profile), retrieve the K=5 most-similar historical events from `02_System/engine/training/historical_events_ledger.json` (988+ events across 8 buckets, expanding via PLAN_OF_RECORD), augmented with iter-4 prospective deal-economics features, and report the outcome distribution + median forward return + similarity-weighted base rate. Output is a precedent reference-class document plus a JSON sidecar consumable by U2 (compose-thesis-with-discipline) for sizing inputs and probability anchoring.

This skill is the empirical-anchor layer of the analytical stack: P1–P5 produce profile-specific deep analysis (FDA / activist filer / acquirer history / litigation EV); U3 grounds the thesis in **what actually happened** to comparable events. Without U3, U2 thesis probabilities are anchored only on per-candidate analytical reasoning. With U3, every probability has a precedent base rate to defend or override.

Invoked when:
- A candidate is being added to a dossier and U2 needs precedent base rates for its scenario tree
- A user wants to interrogate "show me the 5 most similar historical events to RPAY's activist setup"
- A pre-mortem on a thesis: how did neighbors with this same feature pattern actually resolve?

## Inputs

| Input | Type | Required | Example |
|-------|------|----------|---------|
| `candidate_id` | string (ticker, CIK, or dossier folder name) | yes | `RPAY` |
| `profile` | one of {`merger_arb`, `activist_governance`, `insider`, `binary_catalyst`, `litigation`} | yes | `activist_governance` |
| `candidate_features` | dict of `{feature_name: value}` matching the iter-4 schema for the profile | no — computed inline if absent | `{"target_market_cap_log10": 8.42, "price_60d_pre_event": -0.12, ...}` |
| `candidate_features_path` | absolute path to a single-row M3 features JSON | no — alternative to `candidate_features` | `skills/extract-event-features/outputs/RPAY_features.json` |
| `k` | integer K-NN parameter | no — default 5 | `5` |
| `mode` | `online` or `offline` | no — default `online` | `offline` |

Exactly one of `candidate_features`, `candidate_features_path`, or **inline computation** must produce features. If none provided and inline computation fails (e.g., yfinance unreachable in offline mode and no dossier feature snapshot), the skill emits `error_class=insufficient_features, recoverable=true` and exits without writing partial outputs.

## Outputs

1. `skills/compare-to-historical-precedents/outputs/<candidate_id>_<profile>_precedents.md` — human-readable reference-class report
2. `skills/compare-to-historical-precedents/outputs/<candidate_id>_<profile>_knn.json` — machine-readable sidecar with K neighbors, similarity scores, aggregate base rates

Both written atomically (temp file + rename) per CLAUDE.md §1.6 and `feedback_edit_tool_null_padding.md`.

## Methodology

### 1. Profile → bucket + sidecar resolution

| profile | ledger bucket | iter-4 sidecar (relative to reference folder) | feature schema source of truth |
|---------|--------------|-----------------------------------------------|-------------------------------|
| `merger_arb` | `ma` | `02_System/engine/training/iteration_4_merger_arb_features.json` | iter-4 merger_arb |
| `activist_governance` | `activist` | `02_System/engine/training/iteration_4_activist_features.json` | iter-4 activist |
| `insider` | `insider` | `02_System/engine/training/iteration_4_insider_features.json` | iter-4 insider |
| `binary_catalyst` | `biotech` | `02_System/engine/training/iteration_4_biotech_prospective_features.json` (Schema A: `by_event_id`) | iter-4 biotech (Option B) |
| `litigation` | `litigation` | (none on disk yet) | base ledger `features` only |

`short_positioning` is **out of scope** per `skill_build_plan.json:scope.out_of_scope`. The orchestrator rejects it cleanly with `error_class=unknown_profile, recoverable=false`.

### 2. Build reference universe

Reference universe = events from `historical_events_ledger.json` matching the resolved bucket, **filtered to those with a non-PENDING `outcome.label`** (HIT, MISS, or PARTIAL). PENDING events are excluded because they have no resolution data — they cannot anchor a base rate.

For each kept event:
1. Look up `event_id` in the iter-4 sidecar (if available for the profile). Two schema variants supported:
   - **Schema A** (`by_event_id`): `sidecar["by_event_id"][event_id]` → flat dict of features
   - **Schema B** (`events`): linear scan, take `events[i]["rich_features"]` where `events[i]["event_id"] == event_id`
2. Merge: start with `event["features"]` (base v1), overlay sidecar `rich_features`. Sidecar overrides on conflict (sidecar is the iter-4 truth).
3. Drop sidecar-internal keys prefixed with `_` and any `RAW_SCALE_DROP_FROM_DESIGN` columns (per `learning_loop.augmented_features` convention).
4. If sidecar lookup misses for an event with `bucket==profile_bucket`, retain the event with **base features only** and flag it with `_sidecar_missing=true`. K-NN can still match on shared base features but the row's similarity weight is downgraded (see §6).

### 3. Compute candidate features

Three input paths in priority order:

1. **`candidate_features` dict provided**: use directly. Skip computation. Confidence baseline 0.95 (caller-supplied).
2. **`candidate_features_path` provided**: read the JSON, extract the first event's `rich_features`. Confidence baseline 0.85 (M3-extracted).
3. **Inline computation**: requires `online` mode + ticker resolved. Compute the iter-4 schema features for the candidate using the same logic as `iteration_4_<profile>_features.py`:
   - `target_market_cap_log10`: from yfinance `fast_info.market_cap` or `info.marketCap`
   - Price-window features (`price_60d_pre_event` for activist, `price_runup_30d_to_5d` + `price_5d_post_filed` for merger_arb, `price_30d_pre_event` + `price_5d_pre_event` for insider, etc.): yfinance `history(period='1y')` with `pd.Timestamp(filed_at)` anchor
   - Sector one-hots: from yfinance `info.sector` mapped to {Technology, Healthcare, Financial Services, Industrials, Consumer Cyclical/Defensive}
   - Form-type one-hots: derived from candidate's primary signal type (e.g., `form_is_13d_amendment=1` if dossier says `13D/A`; `is_definitive_form=1` for DEFM14A/S-4/SC TO-T)
   - Confidence baseline 0.75 (inline-computed).

Inline computation **always logs which features it imputed to default-zero** so confidence can be downgraded per missing feature.

### 4. Numeric standardization

Before distance computation, standardize numeric features using historical mean/std computed across the reference universe (excluding the candidate). For each numeric feature `f`:

```
mean_f, std_f = mean/std over reference universe
candidate_z = (candidate[f] - mean_f) / max(std_f, 1e-6)
each_neighbor_z = (neighbor[f] - mean_f) / max(std_f, 1e-6)
```

This prevents `target_market_cap_usd` (raw scale) from saturating distance vs. binary one-hots — a reproduction of the `RAW_SCALE_DROP_FROM_DESIGN` lesson from iter-4. Token features are not standardized (handled separately in §5).

### 5. Profile-weighted feature distance

Distance between candidate and a single neighbor is a **weighted Euclidean** over standardized features:

```
d(candidate, neighbor) = sqrt(  Σ w_f · (candidate_z[f] − neighbor_z[f])²  )
                         + Σ_{token f} w_f · 1[candidate[f] ≠ neighbor[f]]
```

`w_f` (feature weight) source priority:

1. **Learned coefficients from `scorecard_iteration_4.md`** for the profile (where present). Use **|coefficient|** as the weight to encode predictive importance. Currently documented:
   - `activist_governance`: `sector_is_financial 0.150`, `sector_is_healthcare 0.068`, `form_is_13d_amendment 0.062` (top-3; remaining features get the median of these = 0.068)
   - `insider`: `is_role_multi 0.150`, `is_role_director 0.145`, `is_officer 0.128`
   - `short_positioning`: `short_vol_to_avg_ratio 0.150`, `avg_volume_log10 0.091`, `sector_is_consumer 0.089` (out of scope but documented for completeness)
   - `merger_arb`, `binary_catalyst`, `litigation`: no top-3 published in iter-4 doc; use uniform weight = 1.0 / |feature_set|
2. **Uniform**: 1.0 / |feature_set| if no learned weights available.

Token-feature weights default to 0.5 unless documented otherwise (sector_is_* one-hots already capture this for activist, so the `target_sector_token` itself gets weight 0.0 to avoid double-counting).

Profiles missing iter-4 sidecar (litigation today) fall back to uniform weights over the base ledger feature set: `is_securities`, `is_patent`, `is_antitrust`, `is_class_action`, `is_district`, `is_appellate`, `n_parties`, `n_attorneys`, plus the case-cause one-hots.

### 6. K-NN selection

Sort the reference universe by `d(candidate, neighbor)` ascending. Take the first K (default 5). For each kept neighbor compute:

```
similarity_score = 1.0 / (1.0 + distance)
```

(maps distance ∈ [0, ∞) to similarity ∈ (0, 1]; identical events score 1.0).

If a neighbor has `_sidecar_missing=true`, multiply its similarity score by 0.7 (downgrade, but keep in candidate set).

### 7. Aggregate base rates

From the K neighbors compute:

| Aggregate | Definition |
|-----------|------------|
| `outcome_distribution` | `{HIT: count, MISS: count, PARTIAL: count}` over K neighbors |
| `hit_rate` | `count(HIT) / K` |
| `hit_rate_similarity_weighted` | `Σ_neighbors similarity[i] · 1[label==HIT]  /  Σ similarity[i]` |
| `median_return` | median of `outcome.return_pct` (or label-mapped synthetic return for cases without `return_pct`) |
| `mean_return` | similarity-weighted mean of `outcome.return_pct` |
| `return_pct_p10` / `_p90` | empirical 10th/90th percentile of returns |
| `n_with_return` | count of neighbors with non-null `return_pct` |

Synthetic-return mapping (when `outcome.return_pct` is null but label is set):
- HIT → +20% (rough activist/governance HIT magnitude prior, conservative)
- MISS → −10%
- PARTIAL → +5%

These synthetic returns are **only used if at least 60% of neighbors have null `return_pct`**. Otherwise they bias the empirical distribution; the report flags this with `synthetic_return_imputation=true`.

### 8. Sparse handling

K=5 is not always achievable. Decision tree:

| Available HIT-or-MISS neighbors with sidecar overlay | Action | Confidence |
|------|------|------|
| ≥ K | Standard path. Use top-K. | 0.85 (high) |
| K-1 down to 3 | Use what's available. Flag `low_density_reference_class=true`. | 0.65 |
| 2 | Use both, but report explicitly: `n_neighbors=2, base_rate_unstable=true`. | 0.45 |
| 1 | Single-precedent — useful as anecdote, not as base rate. Confidence floor. | 0.25 |
| 0 | Refuse to output. Emit `error_class=no_neighbors, recoverable=true`. Suggest expanding scope (drop sidecar requirement; broaden bucket; cross-profile lookup if user opts in). | n/a |

The `sparse_handling.py` helper centralizes this logic so the orchestrator just calls `evaluate_density(neighbors)` and gets back `(use, confidence, warnings)`.

## Output schema

### `<candidate_id>_<profile>_knn.json`

```json
{
  "schema_version": 1,
  "skill": "compare-to-historical-precedents",
  "skill_version": "v1",
  "candidate_id": "RPAY",
  "profile": "activist_governance",
  "k_requested": 5,
  "k_returned": 5,
  "reference_universe": {
    "bucket": "activist",
    "n_events_total": 100,
    "n_events_resolved": 50,
    "n_events_with_sidecar": 44,
    "ledger_path": "<absolute path>",
    "sidecar_path": "<absolute path or null>"
  },
  "candidate_features": { /* the dict used for distance */ },
  "candidate_features_source": "inline_computed | caller_provided | m3_extracted",
  "candidate_features_imputed": ["..."],
  "feature_weights": { /* feature -> weight used in distance */ },
  "neighbors": [
    {
      "rank": 1,
      "event_id": "...",
      "company_name": "...",
      "ticker": "...",
      "filed_at": "YYYY-MM-DD",
      "form_type": "SC 13D/A",
      "outcome_label": "HIT | MISS | PARTIAL",
      "return_pct": 0.18 | null,
      "distance": 1.42,
      "similarity_score": 0.413,
      "sidecar_present": true,
      "primary_source_url": "https://www.sec.gov/...",
      "delta_features": [
        {"feature": "target_market_cap_log10", "candidate": 8.43, "neighbor": 7.91, "delta_z": 0.39}
      ]
    }
  ],
  "aggregates": {
    "outcome_distribution": {"HIT": 3, "MISS": 1, "PARTIAL": 1},
    "hit_rate": 0.6,
    "hit_rate_similarity_weighted": 0.638,
    "median_return": 0.18,
    "mean_return_similarity_weighted": 0.124,
    "return_pct_p10": -0.12,
    "return_pct_p90": 0.41,
    "n_with_return": 4,
    "synthetic_return_imputation": false
  },
  "warnings": [],
  "confidence": 0.85,
  "harvested_at": "2026-04-29T04:05:00+00:00",
  "harvester": "compare-to-historical-precedents.v1"
}
```

Every neighbor row carries `primary_source_url` per CLAUDE.md §1.6. Every aggregate is auditable from the per-neighbor list.

### `<candidate_id>_<profile>_precedents.md`

Markdown report, fixed structure:

```markdown
# Reference class for <candidate_id> (<profile>)

Generated: <iso>
Skill: compare-to-historical-precedents v1
Confidence: 0.85

## Candidate features
| Feature | Value | Source |
|---------|-------|--------|
| ...

## K=5 nearest neighbors
| Rank | Event | Filed | Outcome | Return | Similarity |
|---|---|---|---|---|---|
| 1 | <company_name> (<ticker>) | YYYY-MM-DD | HIT | +18% | 0.41 |
| ... |

For each neighbor: a one-paragraph delta block stating which features differ most from the candidate.

## Aggregate base rates
- Hit rate: 60% (3 of 5)
- Similarity-weighted hit rate: 63.8%
- Median return: +18%
- Return distribution: p10 = -12%, p90 = +41%
- n with concrete return: 4 of 5

## Caveats / warnings
- (any sparse-density flags, synthetic-return flags, sidecar-missing flags)

## Primary sources
- <url> for each neighbor
```

## Failure modes and recovery

| Failure mode | Detection | Skill response |
|---|---|---|
| `historical_events_ledger.json` unreachable | file not found / parse error | `error_class=ledger_unavailable, recoverable=false`, exit. The ledger is bedrock; this is HALT-flag territory. |
| Iter-4 sidecar missing for in-scope profile | file not found | Continue with base features only; `_sidecar_missing` flag on every neighbor; confidence ceiling 0.65 |
| Inline candidate-features computation fails (yfinance unreachable, ticker unresolved) | exception in `compute_candidate_features` | If `mode=online`: try once more then fall through to `caller_provided` requirement. If `mode=offline`: `error_class=offline_no_features, recoverable=true` |
| K=0 (no neighbors after filters) | empty kept list | Per §8 sparse handling. `error_class=no_neighbors, recoverable=true` |
| Out-of-scope profile (`short_positioning`) | profile validation | `error_class=unknown_profile, recoverable=false` |
| HALT_FLAG present | `02_System/engine/health/HALT_FLAG` exists | Online path: log + exit per CLAUDE.md §3.11. Offline mode (smoke tests) bypasses. |
| Atomic-write race | temp file mismatch on rename | retry once with new temp suffix; on second failure `error_class=write_failure, recoverable=true` |

Every error path returns a structured JSON status to stdout (per §3.4):

```json
{"status": "error", "error_class": "...", "error_msg": "...", "recoverable": true|false, "skill": "compare-to-historical-precedents"}
```

Successful path:

```json
{"status": "ok", "skill": "compare-to-historical-precedents", "candidate_id": "RPAY", "profile": "activist_governance", "k_returned": 5, "confidence": 0.85, "outputs": ["...md", "...json"]}
```

## Compliance with system invariants

- **Atomic writes** (temp file + rename) per D-052
- Every output row carries `confidence` (0.0–1.0) and `source` (URL or file path) per CLAUDE.md §1.6
- **Append-only**: this skill never mutates the ledger. The ledger is read-only input.
- **Reference folder is read-only** per `feedback_folder_write_scope.md` — all writes go to the working folder
- **Numeric tickers rendered with company name** (`6027 (Bengo4.com)`) in the markdown report per CLAUDE.md §1.7
- **HALT_FLAG honored** at orchestrator entry on online path; offline mode bypasses for smoke tests
- **No silent failures** — every failure produces a status entry with confidence lowered or an error_class
- **No leakage features**: this skill consumes iter-4 sidecars **post**-D-097 leakage decoupling. The merger_arb/activist/insider sidecars on disk are already leakage-clean. The binary_catalyst sidecar is the D-098 Option B clean version. No additional leakage check needed at the U3 layer.

## Worked example — RPAY (activist_governance)

**Input**: `candidate_id=RPAY`, `profile=activist_governance`, mode=offline (smoke test).

**Candidate features** (computed inline from RPAY dossier, since offline mode + no caller features):
- `target_market_cap_log10` ≈ 8.43 (log10($268M))
- `price_60d_pre_event` = -0.05 (computed approximation; flagged imputed if yfinance unavailable)
- `price_252d_pre_event` = -0.42
- `is_underperformer` = 0 (60d > -10%)
- `form_is_initial_13d` = 0
- `form_is_13d_amendment` = 1 (RPAY signal is 13D/A)
- `target_sector_token` = "Technology"
- `sector_is_tech` = 1, others 0

**Reference universe**: bucket=activist, 100 events, 50 resolved (HIT/MISS), 44 with iter-4 sidecar overlay.

**Top-5 neighbors** (illustrative — exact ranks depend on standardization mean/std at runtime):
1. Avalon GloboCare (ALBT) 13D/A 2024-01-25, HIT — sector mismatch (Real Estate vs Tech), but form match + similar small-cap profile. Distance 1.42, similarity 0.41.
2. Saba Capital BRW 13D/A 2024-01-11, HIT — closed-end fund activist, form match, sector mismatch (Financial). Distance 1.55, similarity 0.39.
3. ... (etc.)

**Aggregate base rate**:
- Hit rate: 3/5 = 60%
- Similarity-weighted hit rate: ~64%
- Median return: not all neighbors carry concrete return_pct; report flags `synthetic_return_imputation` if >60% imputed.

**Confidence**: 0.85 if K=5 from sidecar-covered events; 0.65 if any neighbors fell back to base-features-only.

**Output paths**:
- `skills/compare-to-historical-precedents/outputs/RPAY_activist_governance_precedents.md`
- `skills/compare-to-historical-precedents/outputs/RPAY_activist_governance_knn.json`

This base rate then feeds U2's scenario tree: the ~60% hit rate becomes the prior for the activist-success branch, with the candidate-specific dimensions from P3 (Forager track record) and U1 (RPAY balance sheet) modifying it up or down.

## Helper modules

| File | Purpose |
|---|---|
| `helpers/atomic_write.py` | temp-file + rename utility (reused pattern from U4/M1/M2/M3) |
| `helpers/knn_distance.py` | profile-weighted distance + standardization |
| `helpers/reference_class_aggregator.py` | aggregate computations (hit rate, weighted return, percentiles) |
| `helpers/sparse_handling.py` | density evaluation + confidence floor logic |
| `helpers/analyze.py` | orchestrator: load ledger + sidecar, compute candidate features, run K-NN, write outputs |

All helpers `py_compile`-clean, atomic writes, structured-status stdout, no side effects on the reference folder.
