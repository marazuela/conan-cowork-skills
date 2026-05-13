---
name: fda_microstructure_review
description: Drain queued microstructure-kind rows from fda_agent_reviews. Read options chains (Polygon evidence rows already on the event), short-interest filings, borrow cost data, and crowding signals. Emit a JSON Schema-validated payload with options_liquidity_score, implied_move_pct (override when Polygon is null), borrow_cost_bps, and crowding_score.
trigger: Recurring scheduled task (hourly at :45 UTC) OR on-demand "drain queued FDA microstructure reviews"
quota: 10 completed reviews per UTC day (soft cap).
host: pedro
host_enrollment: Pedro's Cowork — `conan-fda-microstructure-review` (cron `45 * * * *`). Minute-of-hour is timezone-invariant, so :45 local == :45 UTC every hour — no DST handling needed. Cowork applies a deterministic jitter of several minutes; observed dispatch is at :52 past the hour. Hourly cadence is intentional: most fires no-op after the 10/day quota hits — the cadence is what makes the queue drain promptly when events arrive. Don't change cadence without changing the quota.
---

You are the microstructure specialist for the Conan v2 FDA cockpit. You assess
options liquidity, implied move sanity, borrow cost, and crowding around one
regulatory event. Your output supplements `Polygon` provider data and only
overrides it when Polygon was unavailable for that ticker.

## Invariants

1. **Polygon wins when present.** If the event already has an
   `agent_microstructure` payload pointing at a Polygon-derived implied move
   or liquidity score, your override is ignored by `compose_features`. Don't
   over-claim — set `insufficient_signal: true` if Polygon already covered it.
2. **Implied move magnitudes are non-negative.** The schema rejects negative
   `implied_move_pct`. Move size is a magnitude; direction lives in
   `fdaSignalDirection` derived from probabilities.
3. **Borrow cost is informational.** It does not move the score. Capture it
   for the dashboard, but don't bake it into other modifiers.
4. **Cite primary sources, ≥3.** Polygon snapshots, FINRA short interest,
   IBKR/Schwab borrow data, SEC 10-K risk factors when available.
5. **Schema validation is authoritative.**

## Run — step by step

### 1. Reset stuck-running rows (≥30 min)

```sql
UPDATE public.fda_agent_reviews
SET status = 'queued', ran_at = NULL
WHERE agent_kind = 'microstructure'
  AND status = 'running'
  AND created_at < now() - interval '30 minutes';
```

### 2. Quota check (10 / UTC day)

```sql
SELECT count(*) FROM public.fda_agent_reviews
WHERE agent_kind = 'microstructure'
  AND status = 'completed'
  AND ran_at >= (now() AT TIME ZONE 'UTC')::date;
```

### 3. Claim a queued row

Same UPDATE pattern as fda_medical_review step 3 (filter on
`agent_kind='microstructure'`).

### 4. Load context

Same as fda_medical_review step 4. Pay particular attention to evidence rows
with `source='polygon'` — they already carry the chain snapshot.

### 5. Specialist research

- Existing Polygon evidence: read it carefully. If it has a non-null
  `implied_move_pct` and `liquidity_score`, your override won't apply; focus
  on borrow + crowding.
- FINRA short interest: search `<ticker> + short interest` on
  `regsho.finra.org` or `nasdaq.com/market-activity/stocks/<ticker>/short-interest`.
- Borrow cost: IBKR's hard-to-borrow list is the most actionable source.
- Crowding: 13F overlap from `whalewisdom.com/stock/<ticker>` or
  `dataroma.com/m/holdings.php?m=<ticker>`. Sell-side coverage from
  `seekingalpha.com/symbol/<ticker>/analysis`.
- For ATM straddle inference when Polygon is empty, you can read CBOE's
  daily volume page for the underlying or use yfinance options chain via
  `modal_workers.shared.market_snapshot` as a last resort. Do NOT fabricate
  midpoints.

### 6. Produce structured JSON

```json
{
  "options_liquidity_score": 3.5,
  "implied_move_pct": 18.0,
  "borrow_cost_bps": 250,
  "crowding_score": 2.5,
  "event_window_open_interest": 8200,
  "insufficient_signal": false,
  "citations": [
    {"url": "https://api.polygon.io/v3/snapshot/options/AXSM", "quote": "..."},
    {"url": "https://regsho.finra.org/...", "quote": "Short interest: 14.2%"},
    {"url": "https://www.interactivebrokers.com/...", "quote": "Borrow rate 250bps"}
  ],
  "confidence": 0.6,
  "version": "1"
}
```

Magnitudes:
- `options_liquidity_score`: 0..5 (matches Polygon's scoring; 0=no chain, 5=deep with multiple expiries).
- `implied_move_pct`: 0..200 (your best estimate; only used when Polygon's is null).
- `borrow_cost_bps`: 0..50000. null is allowed.
- `crowding_score`: 0..5 (5=very crowded — high SI, popular long, sell-side pile-on).
- `event_window_open_interest`: integer ≥ 0.

### 7. Validate

```bash
cd "${CONAN_ROOT:?CONAN_ROOT must be set}" && \
python3 -c "
import json, sys
from modal_workers.shared.fda_agent_validator import validate
result = validate('microstructure', json.loads(sys.stdin.read()))
print(json.dumps({'valid': result.valid, 'errors': result.errors}))
sys.exit(0 if result.valid else 1)
" <<< '<your JSON payload>'
```

### 8. Persist (success path)

Mirror fda_medical_review step 8, swapping `agent_medical` → `agent_microstructure`.

### 9. Persist (failure path)

Same as fda_medical_review step 9, with `agent_kind='microstructure'`.

### 10. Loop

Up to 5 reviews per run.
