---
name: label-outcomes-from-prices
description: Forward-return resolution via yfinance with profile-specific HIT/MISS/PARTIAL thresholds. Reads an events ledger (M1 output), fetches per-event price series, computes forward returns at multiple windows, applies profile-specific outcome rules, and atomic-writes a labeled outcomes ledger. Handles delistings, spin-offs, ticker changes, corporate actions, and CIK-based ticker fallback. Returns recoverable=true with structured status on partial completion so a future invocation can resume.
type: skill
---

# label-outcomes-from-prices

## Purpose

Closes the labeling loop in the M1→M2→M3 methodology pipeline. Given an events ledger produced by `harvest-historical-events` (M1), this skill fetches per-event price history from Yahoo Finance, computes forward returns at the canonical return-window set, and applies a profile-specific HIT/MISS/PARTIAL classification. Output is the canonical input to `extract-event-features` (M3), `compare-to-historical-precedents` (U3), and the broader iter-4 calibration pipeline.

The skill is invoked when (a) M1 has just produced a fresh events ledger that needs labels, (b) an existing labeled ledger needs to be re-resolved at a new return window, or (c) the meta-scheduler triggers periodic outcome re-labeling on aged events whose forward windows have just closed.

## Inputs

- `events_ledger_path` (str, absolute or repo-relative) — path to a JSON file produced by M1 with shape `{"schema_version": 1, "profile": ..., "run_id": ..., "events": [...]}`. Each event must have `event_id`, `filed_at`, and (for tickered profiles) `ticker` or `cik`. Example: `skills/harvest-historical-events/outputs/merger_arb_<run_id>_events.json`.
- `profile` (str) — one of `merger_arb`, `activist_governance`, `binary_catalyst`, `litigation`, `insider`. Must match the profile recorded inside the events ledger; mismatch returns `error_class=profile_mismatch`, `recoverable=false`.
- `return_windows_days` (list[int], optional) — windows in calendar days at which to compute forward returns. Default by profile (see Methodology §3). Must include the profile's canonical window or the rules in §4 cannot fire.
- `mode` (str, optional, default `"online"`) — `"online"` fetches yfinance live; `"offline"` reuses prior `forward_returns` if present and degrades cleanly when none is found (used for smoke tests and HALT_FLAG environments).
- `resume_from_checkpoint` (bool, optional, default `true`) — if true and a checkpoint exists for this run_id, resume from the last successful event.

## Outputs

- `skills/label-outcomes-from-prices/outputs/<profile>_<run_id>_outcomes.json` — labeled events ledger. Same envelope as input plus per-event `forward_returns`, `outcome.label`, `outcome.return_pct`, `outcome.return_pct_annualized`, `outcome.window_days`, `outcome.confidence`, `outcome.criterion`, `outcome.resolved_at`, `outcome.source`.
- `skills/label-outcomes-from-prices/outputs/<profile>_<run_id>_outcomes_checkpoint.json` — checkpoint with `last_processed_index`, `n_resolved`, `n_unresolvable`, `n_pending`. Idempotent — re-running with the same `run_id` resumes from the checkpoint.
- `skills/label-outcomes-from-prices/outputs/<profile>_<run_id>_outcomes_summary.md` — human-readable label distribution + unresolvable diagnostic.

Every output row carries `confidence ∈ [0.0, 1.0]` and `source` (yfinance URL or fallback URL) per CLAUDE.md §1.6. Numeric tickers preserved with `company_name` field for §1.7 rendering downstream.

## Methodology

### 1. Setup and HALT-flag check

1. Resolve repo root: working folder `Investment tool backup skills` (writes), reference folder `Investment tool backup` (read-only).
2. If `mode="online"`, check `02_System/engine/health/HALT_FLAG` in **the working-folder mirror** (or fall back to the reference folder if no mirror). If present, exit `recoverable=true, status="halted"` without fetching anything.
3. Read the events ledger; validate schema (must have `schema_version`, `profile`, `events`); reject `profile_mismatch` if `events_ledger.profile != input.profile`.
4. Read or initialize the run-specific checkpoint at `outputs/<profile>_<run_id>_outcomes_checkpoint.json`. If the checkpoint's `events_total` ≠ ledger's `events_total`, treat as a new run (do not blindly resume — events count drift implies the upstream ledger changed).

### 2. Per-event ticker resolution

For each unprocessed event (skip those already past `last_processed_index` if resuming):

1. **Direct ticker present** — use `event.ticker` directly. Normalize: uppercase, strip leading/trailing whitespace, strip exchange suffixes that yfinance handles internally (e.g., keep `.L`, `.AX`, `.HK`, `.T` — these are required for non-US tickers).
2. **CIK-only event** — call `helpers/yfinance_fetch.py::lookup_ticker_from_cik(cik)` which calls `https://data.sec.gov/submissions/CIK<10>.json` and returns the first ticker in the JSON. Cache CIK→ticker in-memory for the run.
3. **Numeric tickers (Japan, China, Korea, India)** — preserve as-is with the proper suffix. JP 4-digit → `<n>.T`, JP 5-digit (preferred companies) → `<n>.T` (per Q-003 the resolver in M1 already adds it; do not strip). HK `<n>.HK`, KR `<n>.KS` / `<n>.KQ`, IN `<n>.NS` / `<n>.BO`. If suffix missing on a non-US event, attempt the most likely suffix from the event's exchange/scanner provenance.
4. **Spin-off / name change / delisting fallback** — if yfinance returns empty history:
   1. Re-query via `helpers/corporate_actions.py::resolve_corporate_action(cik, filed_at, ticker)` which inspects EDGAR submissions JSON for: NT 10-K (delisting tells), 8-K Item 3.01/5.03 (delisting/name change), 8-K Item 1.02/2.01 (spin-off completion).
   2. If a spin-off is detected, follow `parent_ticker_at_filing → child_ticker` substitution; record `corporate_action="spinoff"` in the outcome row.
   3. If a name-change is detected, retry yfinance with the new ticker; record `corporate_action="name_change"`.
   4. If a delisting is detected and the OTC pink-sheet ticker is referenced in the 8-K, retry yfinance with the OTC ticker; record `corporate_action="delisted_to_otc"`.
   5. If none resolves, mark `outcome.label="UNRESOLVABLE_PRICE"` with `corporate_action="unresolvable"`.

### 3. Per-event forward-return computation

1. **Anchor**: filed_at parsed as UTC midnight. For non-US events use the event's local market timezone if encoded in the harvest provenance; otherwise default UTC midnight.
2. **Window**: fetch yfinance daily history from `filed_at - 5 days` to `filed_at + max(return_windows_days) + 14 days`. The +14 cushion handles weekends, holidays, and the next-trading-day anchor lookup.
3. **Anchor close**: the first available adjusted close on or after `filed_at`. If `filed_at` is in the future or the window has not closed (e.g., a 180-day window for an event 90 days ago), the corresponding `ret_<w>d` is `null` and `outcome.label="PENDING_WINDOW"` (with `pending_until=<anchor + max_window>`).
4. **Forward closes**: for each window `w` in `return_windows_days`, take the first adjusted close on or after `anchor_ts + w * 86400`. Compute `ret_w = forward_close / anchor_close - 1`. Round to 6 decimals.
5. **Annualized**: `ret_annualized_w = (1 + ret_w) ** (365 / w) - 1`. Used by merger_arb HIT rule.
6. **Corporate-action adjustment**: yfinance returns split- and dividend-adjusted closes by default (`adjclose`). Do not re-adjust. If `adjclose` is missing (rare API shape), fall back to `quote.close` and flag `confidence -= 0.10`.

Default `return_windows_days` per profile (used if input omits the parameter):

| Profile             | Default windows (days)        | Canonical window |
|--------------------|-------------------------------|------------------|
| merger_arb         | [30, 60, 90, 180, 365]        | 60               |
| activist_governance| [30, 90, 180, 365, 730]       | 365              |
| binary_catalyst    | [1, 7, 30, 90]                | 1 (event-day)    |
| litigation         | [30, 180, 365, 730]           | 365              |
| insider            | [30, 60, 90, 180]             | 90               |

The canonical window is the one referenced by the profile's primary HIT rule (§4); other windows are recorded for downstream feature engineering and U3 K-NN.

### 4. Profile-specific HIT/MISS/PARTIAL rules

The label-rule logic lives in `helpers/profile_thresholds.py::classify(profile, event, forward_returns)` and returns `(label, criterion_str, confidence_delta)`.

#### 4.1 merger_arb

Anchor return is `ret_60d` (canonical). Rule:

- `HIT`: `ret_60d >= 0` (D-002 baseline + iteration_4 spec). Magnitude preserved in `outcome.return_pct`.
- `MISS`: `ret_60d < 0`.
- `PARTIAL`: reserved for `ret_60d ∈ (-0.02, 0.02)` AND deal status indeterminate (reserved for follow-up integration; default not used in v1 unless caller passes `--enable-partial`).
- `criterion`: `"merger_arb_60d_forward_return_signed"`.

Optional richer rule (when `event.features.deal_spread_pct` is present):
- `HIT_VS_SPREAD`: `ret_annualized_60d > deal_spread_pct * (365/60)` — annualized forward return beats the implied annualized deal spread. Used as a confidence booster (`+0.05`) but does not flip HIT/MISS — D-002's signed-return rule is the primary classifier.

#### 4.2 activist_governance

Anchor return is `ret_365d`. Rule combines forward return + governance-change verification:

- `HIT`: `ret_365d > 0` AND a follow-up filing exists matching one of `(13D/A escalation, DEF 14A board change, DEFA14A settlement, 8-K Item 5.07 vote outcome consistent with activist demand, 8-K Item 1.01/2.01 for sale of company)` within 365 days of filed_at. Verified via `helpers/governance_followup.py`.
- `PARTIAL`: `ret_365d > 0` but no governance follow-up filing detected (price moved without verifiable governance change), OR governance change occurred but `ret_365d <= 0`.
- `MISS`: `ret_365d <= 0` AND no governance follow-up.
- `criterion`: `"activist_365d_return_AND_governance_followup"`.

In v1 the governance follow-up check is best-effort; if the EDGAR submissions API is unreachable, fall back to "return-only" classification with `confidence -= 0.10` and `outcome.criterion="activist_365d_return_only_no_followup_verified"`.

#### 4.3 binary_catalyst

Anchor return is `ret_1d` (event-day) for FDA approval/CRL events. Different rule structure — outcome is binary based on FDA action, not return-only:

- `HIT`: FDA action is "Approved" OR "Approved with REMS" (verified via FDA approvals API, indication match, NDA/BLA number match).
- `MISS`: FDA action is "Complete Response Letter" (CRL) OR "Withdrawn" OR "Refusal-to-File".
- `PARTIAL`: FDA extended PDUFA date (no decision in window) OR conditional approval with significant label restriction (boxed warning unexpected, indication narrowed materially).
- `UNRESOLVABLE`: FDA decision pending past window.
- `criterion`: `"binary_catalyst_FDA_action_class"`.

`ret_1d`, `ret_7d`, `ret_30d`, `ret_90d` are still computed and stored as magnitude evidence even when the label is FDA-driven.

#### 4.4 litigation

Anchor return is `ret_365d`. Rule:

- `HIT`: `ret_365d > 0` AND case has been resolved (motion-to-dismiss granted, settlement filed, judgment for defendant — i.e., outcome favorable to the **defendant** when the thesis is short-the-defendant; or judgment for plaintiff when long-the-plaintiff). Direction inferred from event metadata: `event.features.thesis_direction ∈ {"long_defendant", "short_defendant", "long_plaintiff"}`.
- `PARTIAL`: case resolved but return contradicts thesis direction by < 5%; or case still pending but interim ruling matches thesis.
- `MISS`: case resolved AND return contradicts thesis direction by >= 5%.
- `UNRESOLVABLE`: case still pending past window.
- `criterion`: `"litigation_365d_return_AND_resolution_status"`.

If `thesis_direction` is missing from event features, default to `signed_return_only` with `confidence -= 0.15` and `criterion="litigation_365d_signed_return_only"`.

#### 4.5 insider

Anchor return is `ret_90d`. Rule (D-067 insider profile spec, sector-relative):

- `HIT`: `ret_90d > 0.05` AND `ret_90d - sector_etf_ret_90d >= 0.02` (i.e., absolute >+5% AND beats sector by ≥2pp).
- `PARTIAL`: `ret_90d > 0.05` but underperforms sector by 0–2pp (absolute win, relative miss); OR `ret_90d ∈ (0, 0.05]` AND beats sector by ≥2pp.
- `MISS`: `ret_90d <= 0` OR underperforms sector by >2pp.
- `criterion`: `"insider_90d_abs_5pct_AND_sector_relative_2pp"`.

Sector ETF map (in `helpers/profile_thresholds.py::SECTOR_ETF_MAP`):

| Sector (SIC 2-digit prefix) | ETF |
|----------------------------|-----|
| 10–14 (mining/oil-gas)     | XLE |
| 20–39 (manufacturing)      | XLI |
| 40–49 (transport/util)     | XLU |
| 50–59 (retail/wholesale)   | XLY |
| 60–67 (finance)            | XLF |
| 70–79 (services)           | XLC |
| 80–89 (health)             | XLV |
| 28 (chemicals)             | XLB |
| 35 (industrial machinery)  | XLI |
| 36 (electronic equipment)  | XLK |
| 73 (business services)     | XLK |
| (unmapped)                 | SPY |

If sector ETF data is unreachable, fall back to `signed_return_only` with `confidence -= 0.15`.

### 5. Confidence scoring

Base confidence per outcome label:

- yfinance returned full series with adjclose: 0.85
- yfinance returned series via fallback `quote.close`: 0.75
- CIK→ticker fallback resolution: 0.80
- Corporate-action-resolved (spin-off / name-change / OTC): 0.70
- Sector ETF data successfully fetched (insider only): no change
- Sector ETF data unreachable (insider): -0.15
- Governance follow-up verified (activist): +0.05
- Governance follow-up unverified (activist, return-only fallback): -0.10
- Thesis direction missing (litigation): -0.15
- HALT_FLAG present (online mode): N/A — exit before computing
- Pending window: 0.50 (the window itself is not yet closed; placeholder)

`confidence = max(0.0, min(1.0, base + adjustments))`.

### 6. Atomic write + checkpoint

After processing a batch of events (default batch_size=10, controlled via `--batch-size`):

1. Update each event's `outcome` block in the in-memory ledger.
2. Atomic-write the full outcomes ledger to `outputs/<profile>_<run_id>_outcomes.json` (temp file + os.replace per `helpers/atomic_write.py`).
3. Atomic-write checkpoint to `outputs/<profile>_<run_id>_outcomes_checkpoint.json`.
4. Stdout-emit a structured summary line: `{"status": "in_progress|completed", "n_resolved": N, "n_pending_window": N, "n_unresolvable": N, "elapsed_s": F}`.

Wall-clock budget: 25 seconds per invocation (per CLAUDE.md §3.1). On budget exhaustion exit `status="in_progress"` with `recoverable=true` so the meta-scheduler can re-dispatch.

### 7. Error handling and graceful degradation

| Failure mode | Skill response | Confidence impact |
|--------------|----------------|-------------------|
| events_ledger missing | `error_class=missing_ledger`, `recoverable=true`, exit | n/a |
| events_ledger profile mismatch | `error_class=profile_mismatch`, `recoverable=false`, exit | n/a |
| HALT_FLAG present (online mode) | `status=halted`, `recoverable=true`, exit | n/a |
| yfinance 404 / empty response | UNRESOLVABLE_PRICE, attempt corporate-action fallback | 0.70 if recovered, 0.0 if not |
| yfinance rate-limited | sleep 2s, retry once; if still failing, mark UNRESOLVABLE_PRICE | -0.10 |
| CIK→ticker lookup fails | UNRESOLVABLE_TICKER, mark and continue | 0.0 |
| filed_at parse error | UNRESOLVABLE_DATE, mark and continue | 0.0 |
| Window open (filed_at + max_window > now) | PENDING_WINDOW, leave for re-resolution | 0.50 |
| Sector ETF fetch fails (insider) | fallback to signed_return_only | -0.15 |
| Governance follow-up API unreachable (activist) | fallback to return_only | -0.10 |
| Profile not in scope (e.g., short_positioning) | `error_class=unknown_profile`, `recoverable=false`, exit | n/a |

No silent failures. Every event ends with an `outcome.label` and an `outcome.confidence`, even if both are zeroed for unresolvable cases.

## Output schema

```json
{
  "schema_version": 1,
  "profile": "merger_arb",
  "run_id": "merger_arb_2020-01-01_2024-12-31_51947cf8",
  "labeled_at": "2026-04-29T03:09:00Z",
  "labeler": "label-outcomes-from-prices.v1",
  "events_total": 20,
  "n_hit": 11,
  "n_miss": 7,
  "n_partial": 0,
  "n_unresolvable": 1,
  "n_pending_window": 1,
  "return_windows_days": [30, 60, 90, 180, 365],
  "canonical_window_days": 60,
  "events": [
    {
      "event_id": "08bf77ab49c07fdec7998358",
      "filed_at": "2024-01-09",
      "ticker": "CERO",
      "company_name": "PHOENIX BIOTECH ACQUISITION CORP. (CERO, CEROW) (CIK 0001870404)",
      "cik": "0001870404",
      "form_type": "S-4/A",
      "forward_returns": {
        "anchor_close": 10.32,
        "ret_30d": -0.041,
        "ret_60d": 0.082,
        "ret_90d": 0.144,
        "ret_180d": 0.218,
        "ret_365d": -0.061,
        "ret_60d_annualized": 0.617
      },
      "outcome": {
        "label": "HIT",
        "return_pct": 8.2,
        "return_pct_annualized": 61.7,
        "window_days": 60,
        "criterion": "merger_arb_60d_forward_return_signed",
        "corporate_action": null,
        "confidence": 0.85,
        "resolved_at": "2026-04-29T03:09:00Z",
        "source": "https://query1.finance.yahoo.com/v8/finance/chart/CERO"
      },
      "confidence": 0.85,
      "source": "https://query1.finance.yahoo.com/v8/finance/chart/CERO"
    }
  ]
}
```

## Worked example

Input: M1 output `skills/harvest-historical-events/outputs/merger_arb_merger_arb_2020-01-01_2024-12-31_51947cf8_events.json` (20 events).

Invocation:
```
python skills/label-outcomes-from-prices/helpers/label.py \
  --events-ledger skills/harvest-historical-events/outputs/merger_arb_merger_arb_2020-01-01_2024-12-31_51947cf8_events.json \
  --profile merger_arb \
  --return-windows 30,60,90,180,365 \
  --mode offline
```

Expected (offline / no live yfinance) output highlights:
- All 20 events processed.
- Each event has `forward_returns` populated (synthetic in offline mode, sourced from yfinance in online mode).
- Each event has `outcome.label ∈ {HIT, MISS, PENDING_WINDOW, UNRESOLVABLE_PRICE}`.
- `outcome.confidence ∈ [0.0, 0.85]`.
- Summary file `merger_arb_<run_id>_outcomes_summary.md` shows label distribution.
- Atomic-write verified (no `*.tmp` files left behind).
- Re-running with same args produces identical output (idempotent).

## Failure modes and recovery

1. **HALT_FLAG present**: Skill exits cleanly. Re-dispatch when HALT_FLAG cleared.
2. **events_ledger schema drift** (missing `events_total` or `events`): error_class=invalid_ledger_schema, recoverable=false. Operator must regenerate via M1.
3. **yfinance API down**: skill processes whatever events succeed, marks the rest UNRESOLVABLE_PRICE with note. Re-dispatch retries unresolved.
4. **PENDING_WINDOW on most events** (e.g., recent harvest): expected and recoverable. Re-dispatch when forward window has closed.
5. **Profile mismatch** (input profile ≠ ledger profile): error_class=profile_mismatch, recoverable=false. Fix caller.
6. **Out-of-scope profile** (e.g., short_positioning): error_class=unknown_profile, recoverable=false.

All failure modes preserve `outcome.confidence` semantics — no row is "silently failed".

## Compliance with system invariants

- Atomic writes (temp file + os.replace) per D-052 — every output write goes through `helpers/atomic_write.py`.
- Every output row carries `confidence` (0.0–1.0) and `source` (URL or path) per CLAUDE.md §1.6.
- Append-only: re-runs add new rows or update existing rows by event_id; never delete.
- Never modifies the reference folder.
- Numeric tickers preserved with `company_name` for §1.7 rendering.
- HALT_FLAG honored on online path; offline mode bypasses for smoke tests only.
- Bounded runtime: 25s wall-clock per invocation, batch-size checkpointing for resumability per CLAUDE.md §3.1.
- Idempotent: same `run_id` + same events_ledger → same outcome ledger.
- py_compile clean (validated by pipeline_runner per D-070).
