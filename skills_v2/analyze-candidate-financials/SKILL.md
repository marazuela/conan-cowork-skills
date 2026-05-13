---
name: analyze-candidate-financials
description: Comprehensive financial assessment of an investment candidate. Universal layer (balance sheet, cash flow, accruals, capital allocation, hidden value) plus profile-specific application (merger_arb, activist_governance, binary_catalyst, insider, litigation). Triggers when a candidate dossier is being built or refreshed and a structured financial picture is needed before composing a thesis.
type: skill
---

# analyze-candidate-financials

## Purpose

Produce a structured, primary-source-anchored financial assessment of any investment candidate. The skill applies a universal financial layer (balance sheet quality, cash flow forensics, Sloan accruals, hidden value, capital allocation) and then layers profile-specific lenses (merger_arb survivability, activist breakup math, catalyst runway, insider alignment, litigation balance-sheet capacity). It is intended to be called by `compose-thesis-with-discipline` (U2) as the supporting financial input, but it can also be invoked standalone when a quick financial pulse is needed.

Invoke this skill when:
- A new candidate has graduated to Watchlist or Immediate band and needs financial deep-dive.
- An existing dossier is being refreshed (quarterly cadence or after a material filing).
- A profile change is being considered and the financial picture needs re-application under a different lens.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `ticker` | string | `RPAY` | yes |
| `cik` | string (10-digit zero-padded) | `0001720592` | yes |
| `profile` | enum: merger_arb \| activist_governance \| binary_catalyst \| insider \| litigation | `activist_governance` | yes |
| `lookback_years` | int (default 5) | `10` | no |
| `include_peer_comps` | bool (default true) | `true` | no |
| `output_dir` | path | `skills/analyze-candidate-financials/outputs/` | no |

## Outputs

Two files, atomic-written (temp file → rename) per D-052:

1. `skills/analyze-candidate-financials/outputs/<TICKER>_financial_assessment.md` — human-readable assessment, Markdown.
2. `skills/analyze-candidate-financials/outputs/<TICKER>_metrics.json` — machine-readable structured metrics for downstream consumers (U2, U3, M3).

The JSON file MUST follow the schema in §"Output schema" below. Every field carries a `confidence` (0.0–1.0) and a `source` (URL or file path) per CLAUDE.md §1.6.

## Methodology

### Step 1 — Resolve the entity

1. Validate `cik` against SEC EDGAR submissions API: `https://data.sec.gov/submissions/CIK<10-digit>.json`.
2. Confirm `ticker` matches the company's reported tickers in that response. If mismatch, log warning and proceed with the CIK-resolved company name.
3. Resolve FIGI via `02_System/engine/tools/openfigi_resolver.py` style call (cached if available); if unavailable, leave `figi` field null with `confidence = 0.0` and `source = "unavailable"`.
4. Pull the most recent 10-K and the most recent 10-Q from EDGAR. Capture accession numbers and filing dates.

### Step 2 — Universal layer

#### 2.1 Balance sheet quality

Pull from latest 10-Q balance sheet (or 10-K if newer). Compute:

- **Working capital** = current assets − current liabilities. Flag if negative.
- **Cash + short-term investments** ($ and as % of total assets).
- **Total debt** (short-term + long-term + current portion of long-term).
- **Net debt** = total debt − cash & equivalents.
- **Net debt / EBITDA** (LTM EBITDA from most recent 4 quarters).
- **Net debt / equity**.
- **Interest coverage** = EBIT / interest expense.
- **Debt maturity wall** — maturity schedule from most recent 10-K Note "Debt" or "Long-term Debt" exhibit. Identify next 3 years of maturities.
- **Off-balance-sheet exposure** — operating lease commitments (post-ASC 842 these are on balance sheet, but parse for purchase obligations and JV/VIE guarantees from the commitments and contingencies note).

Confidence rules:
- 1.0 if pulled directly from filing tables and date matches latest filing.
- 0.7 if derived from cached/aggregator value (e.g., yfinance) and reconciled to filing within ±2%.
- 0.4 if derived but not reconciled.
- 0.0 if not found — record as null.

#### 2.2 Cash flow quality

From the cash flow statement of the latest 10-K and trailing 4 quarters:

- **Operating cash flow (OCF)** — LTM and 5-year trend.
- **Net income (NI)** — LTM and 5-year trend.
- **OCF / NI ratio** — flag if persistently < 0.7 (low quality earnings) or > 1.3 (likely conservative accounting).
- **Free cash flow (FCF)** = OCF − capex. LTM and 5-year trend.
- **Cash conversion** = FCF / NI. 5-year median.
- **Stock-based compensation as % of OCF** — flag if > 25% (low underlying cash earnings).
- **Working capital change** as a drag/contribution to OCF over 5 years.

#### 2.3 Sloan accruals

Per Sloan (1996), operating accruals predict future returns negatively. Compute:

```
Accruals = (ΔCA − ΔCash) − (ΔCL − ΔSTD − ΔTaxes_payable) − Depreciation_amort
Accruals_ratio = Accruals / Avg_total_assets
```

Where:
- ΔCA = change in current assets.
- ΔCash = change in cash.
- ΔCL = change in current liabilities.
- ΔSTD = change in short-term debt.
- ΔTaxes_payable = change in taxes payable.
- Avg_total_assets = (TA_t + TA_t-1) / 2.

Flag thresholds:
- Top decile (~> 0.10): red flag — high accruals predict underperformance.
- Bottom decile (< -0.10): green flag — low accruals predict outperformance.
- Middle: neutral.

Helper: `helpers/sloan_accruals.py`.

#### 2.4 Revenue recognition tells

- **DSO** (days sales outstanding) = AR / (Revenue / 365). 5-year trend. Flag rising DSO.
- **Deferred revenue** balance and 5-year trend. Compare to revenue growth.
- **Segment consistency** — pull segment table from 10-K Note "Segment Reporting"; flag if material reclassifications in last 3 years (revenue recognition red flag).

#### 2.5 Capital allocation track record (5–10 year)

Helper: `helpers/capital_allocation_scorecard.py`.

Compute on `lookback_years` window:

- **Buyback ROIC** — for each year's buybacks, IRR computed against current share price (or last close). Total $ deployed, weighted-average price paid vs subsequent 1Y/3Y prices.
- **M&A returns** — list of acquisitions over period from 8-Ks (Items 1.01, 2.01) and 10-K Note "Acquisitions". For each: deal price, year, post-deal segment performance (where disclosed). Goodwill impairments since deal date are red flags.
- **Dividend coverage** — dividends paid / FCF, 5-year average. Flag if > 100%.
- **Net issuance** — buybacks − stock issuance (excluding SBC). Flag persistent net dilution.

Output: composite capital allocation grade (A/B/C/D/F) with rationale.

#### 2.6 Hidden value scan

Helper: `helpers/hidden_value_scanner.py`.

Sweep the latest 10-K for items where carrying value may differ materially from market value:

- **Real estate** — owned properties (Note "Property, Plant and Equipment" with breakdown of land vs buildings; Properties section Item 2). Compare carrying value to estimated market value where city/state level location is disclosed. Mark as "estimate, requires appraisal" with confidence 0.3 if no comp.
- **Intellectual property / intangibles** — patents, trademarks, customer lists. Carrying value (often amortized to near zero) vs licensing revenue or comparable transaction multiples.
- **Deferred tax assets (DTAs)** — gross DTAs, valuation allowance, expiration schedule. Material DTAs with full valuation allowance can become live if profitability returns.
- **Unconsolidated affiliates / equity-method investments** — Note "Equity Method Investments". Carrying value vs share-of-earnings or recent transactions in the underlying entity.
- **Variable interest entities (VIEs)** — disclosure in Note "Consolidation". Note the maximum exposure to loss vs carrying.
- **Operating lease right-of-use assets** — not "hidden" but quantify the off-balance-sheet equivalent (capitalized lease value).
- **Pension over/underfunding** — Note "Retirement Plans". Flag funding ratio < 80% (underfunded liability) or > 110% (excess assets).

### Step 3 — Profile-specific application

Reapply the universal numbers under one of five lenses:

#### 3.1 merger_arb

Per `framework/profile_merger_arb.md`, the financial questions are:

- **Target balance-sheet survivability if deal breaks (MAC vulnerability)**: what is the unaffected pre-deal-rumor share price implied valuation? Can the target survive on its own without the deal? Compute net debt / EBITDA and interest coverage at unaffected price levels. Flag if interest coverage < 2× standalone — MAC invocation more credible.
- **Acquirer financing capacity**: if deal is debt-funded, estimate acquirer's pro forma leverage post-close (acquirer EBITDA + target EBITDA vs deal-financing debt). Flag if pro forma > 6× — financing condition is real risk.
- **Transaction multiples vs peers**: deal EV/EBITDA, EV/Revenue compared to last 5y sector M&A precedents and current peer trading multiples. Identifies mispricing risk.
- **Definitive merger agreement parsing** — call out the specific MAC clause language (broad vs narrow), reverse termination fee, financing-out, antitrust-out.

#### 3.2 activist_governance

Per `framework/profile_activist_governance.md`:

- **Capital allocation forensics** — emphasized. The activist thesis often rests on poor capital allocation that the board can be pushed to reverse. Document specific instances (overpriced acquisitions, dilutive buybacks at peaks, dividend cut at trough).
- **Margin profile vs peers** — 3-year operating margin vs sector median. Material gap supports activist "margin recovery" thesis.
- **Hidden assets supporting breakup thesis** — sum-of-the-parts. Real estate, non-core segments, IP licenses. Compute SOTP value vs current EV.
- **Defensive measures** — poison pill terms (trigger %, sunset), staggered board, dual-class structure. Note from Articles of Incorporation and most recent DEF 14A.
- **Insider alignment** — top-5 executive ownership as % of shares, recent buying/selling. Section 16 filings for last 24 months.

#### 3.3 binary_catalyst

Per `framework/profile_binary_catalyst.md`:

- **Cash runway through catalyst date**: cash + ST investments + (FCF projection through catalyst date) − required spend. Months of runway. Flag if < 18 months at PDUFA — dilution risk.
- **Dilution risk if catalyst slips**: if extension/CRL forces additional Phase 3 or label-expansion trial, estimated additional cost vs cash. Flag at-the-market shelf programs (Form S-3 ATM).
- **Partnership economics**: existing licensing/royalty/milestone agreements. Net economics retained by company.
- **Burn rate vs guidance**: actual quarterly OCF vs management guidance. Trend.
- **Working capital dynamics**: receivables/inventory if revenue product is approaching launch.

#### 3.4 insider

Per Profile 6 (insider) handling:

- **Executive comp alignment**: stock-based comp as % of total comp. Performance-based vs time-based vesting split. Long-term hold requirements.
- **Buyback discipline at insider purchase prices**: did the company buy back stock at the same price the CEO is now buying? Or buying back at peaks while insiders sold?
- **10b5-1 plan adoption discipline**: are insider sales clustered around windows that suggest plan-driven (low signal) vs discretionary (higher signal)?
- **Cluster patterns**: dollar value, role (CEO/CFO/independent director), relationship to fundamentals.

#### 3.5 litigation

Per `framework/profile_litigation.md`:

- **Balance sheet capacity to absorb damages**: unrestricted cash + revolver availability vs claim amount. Net debt headroom before covenant breach.
- **Insurance coverage**: D&O coverage limits (from DEF 14A executive compensation discussion), litigation reserves (footnote disclosure), self-insured retention.
- **Settlement reserve precedent**: have they settled prior cases? Settlement amounts vs claim amounts disclosed in 10-K Note "Commitments and Contingencies".
- **Ratings impact threat**: investment-grade rating maintenance — would judgment trigger downgrade?

### Step 4 — Peer comparable lookup (if `include_peer_comps = true`)

Pull GICS sub-industry from 10-K (or use a static map file). Pull 5–8 closest peers by market cap and revenue. Compare on EV/EBITDA, EV/Revenue, FCF yield, ROIC, leverage. Note where candidate is at extremes (top/bottom quintile) and flag the implied thesis.

### Step 5 — Synthesize narrative + write outputs

Compose the Markdown assessment with sections matching this skill's output schema below. Atomic-write JSON metrics file with all numerical fields, confidence, and sources. Append a one-line scheduler summary to stdout: `{"status": "ok", "ticker": "...", "rows_written": N, "source": "10-K accession ..."}`.

### Step 6 — Confidence gates

If any of the following hold, set top-level `confidence` to ≤ 0.5 and surface the issue in a "Data quality concerns" section:

- 10-K is more than 14 months old (delinquent filer).
- 10-Q is more than 5 months old.
- Going-concern qualification in auditor's report.
- Restatements in last 3 years.
- Non-standard fiscal year and the latest 10-Q is from prior fiscal year.

## Profile-specific application

The Methodology §3 section contains the per-profile application. For convenience, here is a quick lookup of which §2 outputs feed which §3 lens:

| Universal output | merger_arb | activist | binary_catalyst | insider | litigation |
|---|---|---|---|---|---|
| Balance sheet quality | MAC survivability | Stub equity capacity | Runway base | Buyback discipline | Damages capacity |
| Cash flow quality | Acquirer financing | Margin recovery thesis | Burn rate | Cash conversion as quality signal | Cash for settlement |
| Sloan accruals | Earnings quality flag | Earnings quality flag | Earnings quality flag | Earnings quality flag | Earnings quality flag |
| Capital allocation | Multiple precedent | Core thesis driver | M&A risk to runway | Insider buying at trough? | Settlement track record |
| Hidden value | Floor on break price | Sum-of-the-parts target | N/A | N/A | Asset shielding via subsidiaries |

If `profile == "universal"` (no specific lens), output all five lenses with a synthesis paragraph at the top.

## Output schema

`<TICKER>_metrics.json`:

```json
{
  "schema_version": "1.0",
  "ticker": "RPAY",
  "cik": "0001720592",
  "company_name": "Repay Holdings Corporation",
  "figi": "BBG00MJ1...",
  "profile": "activist_governance",
  "as_of_date": "2026-04-29",
  "filings": {
    "latest_10K": { "accession": "0001193125-26-098518", "filing_date": "2026-03-09", "fiscal_year_end": "2025-12-31", "source": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001720592&type=10-K", "confidence": 1.0 },
    "latest_10Q": { "accession": "...", "filing_date": "...", "period_end": "...", "source": "...", "confidence": 1.0 }
  },
  "balance_sheet": {
    "as_of_period": "2025-12-31",
    "working_capital": { "value_usd_mm": 47.2, "confidence": 1.0, "source": "10-K accession 0001193125-26-098518, Consolidated Balance Sheets" },
    "cash_and_st_investments": { "value_usd_mm": 60.1, "pct_total_assets": 0.066, "confidence": 1.0, "source": "..." },
    "total_debt": { "value_usd_mm": 220.0, "confidence": 1.0, "source": "..." },
    "net_debt": { "value_usd_mm": 159.9, "confidence": 1.0, "source": "..." },
    "net_debt_to_ebitda": { "value": 2.4, "confidence": 0.9, "source": "derived" },
    "interest_coverage": { "value": 4.1, "confidence": 0.9, "source": "derived" },
    "debt_maturity_wall": { "next_3y_usd_mm": [0.0, 0.0, 220.0], "confidence": 0.95, "source": "10-K Note 7" }
  },
  "cash_flow": {
    "ocf_lttm_usd_mm": { "value": 75.0, "confidence": 1.0, "source": "..." },
    "ni_lttm_usd_mm": { "value": -12.0, "confidence": 1.0, "source": "..." },
    "ocf_to_ni": { "value": -6.25, "confidence": 1.0, "source": "derived", "interpretation": "Negative NI with positive OCF — common in tech/payments with high D&A; quality signal moderate" },
    "fcf_lttm_usd_mm": { "value": 45.0, "confidence": 0.95, "source": "derived" },
    "sbc_pct_ocf": { "value": 0.18, "confidence": 0.95, "source": "..." }
  },
  "sloan_accruals": {
    "accruals_ratio": { "value": -0.04, "confidence": 0.85, "source": "computed via sloan_accruals.py", "interpretation": "neutral — within middle 8 deciles" }
  },
  "revenue_recognition": {
    "dso_days": { "value": 38.5, "trend_5y_slope": 1.2, "confidence": 0.9, "source": "..." },
    "deferred_revenue_growth_5y_cagr": { "value": 0.04, "confidence": 0.9, "source": "..." },
    "segment_reclassifications_3y": { "count": 0, "confidence": 1.0, "source": "10-K Note Segment Reporting" }
  },
  "capital_allocation": {
    "lookback_years": 5,
    "buyback_summary": { "total_usd_mm": 35.0, "weighted_avg_price": 8.45, "current_price": 4.05, "buyback_irr_pct": -27.0, "confidence": 0.8, "source": "..." },
    "ma_summary": { "deal_count": 3, "total_consideration_usd_mm": 220.0, "goodwill_impairment_post_deal_usd_mm": 0.0, "confidence": 0.85, "source": "..." },
    "dividend_coverage": { "value_5y_avg": null, "confidence": 1.0, "source": "no dividend" },
    "net_issuance_5y_usd_mm": { "value": -10.0, "confidence": 0.9, "source": "..." },
    "grade": "C-",
    "rationale": "BillingTree and KUBRA acquisitions executed near peak multiples; buybacks at $8+ now look ill-timed at $4. No outright impairment yet."
  },
  "hidden_value": {
    "real_estate": { "carrying_usd_mm": 4.5, "estimated_market_usd_mm": 6.0, "confidence": 0.3, "source": "10-K Item 2 — small offices in Atlanta GA" },
    "intangibles": { "carrying_usd_mm": 180.0, "amortization_schedule_avg_life_yrs": 8.0, "confidence": 0.95, "source": "10-K Note Goodwill and Intangibles" },
    "deferred_tax_assets": { "gross_usd_mm": 35.0, "valuation_allowance_usd_mm": 28.0, "confidence": 1.0, "source": "..." },
    "unconsolidated_affiliates": { "carrying_usd_mm": 0.0, "confidence": 1.0, "source": "none disclosed" },
    "vies": { "max_exposure_usd_mm": 0.0, "confidence": 1.0, "source": "none disclosed" },
    "operating_lease_rou": { "carrying_usd_mm": 12.0, "confidence": 1.0, "source": "..." }
  },
  "profile_lens": {
    "active_profile": "activist_governance",
    "summary": "Activist thesis (Forager 12.9%, Veradace 8.6%) is supported by capital-allocation grade C− (BillingTree/Kubra rollup did not earn ROIC) and operating margin gap to peers (FOUR, GPN, PAY) of ~600bp. Hidden value is modest — no real-estate stash. SOTP framework below.",
    "key_metrics": {
      "operating_margin_gap_to_peers_bps": 600,
      "sotp_estimate_per_share": 5.20,
      "current_price": 4.05,
      "implied_upside_pct": 28.4
    },
    "confidence": 0.7
  },
  "data_quality_concerns": [],
  "overall_confidence": 0.85
}
```

The Markdown assessment file mirrors this structure with narrative prose around each section, citations after every numeric claim, and a final "Synthesis" section that ties findings to the active profile's investment question.

## Worked example

**Test candidate**: RPAY (ticker), CIK 0001720592, profile `activist_governance`.

Inputs:
```
ticker = "RPAY"
cik = "0001720592"
profile = "activist_governance"
lookback_years = 5
include_peer_comps = true
```

Expected key intermediate outputs (smoke-test bar — not exact, illustrative):

- **Latest 10-K accession**: 0001193125-26-098518 (filed 2026-03-09).
- **Net debt**: ~$160M; net debt / LTM EBITDA ~2.4×.
- **OCF/NI**: positive OCF on negative NI — quality moderate (high D&A from rollup).
- **Sloan accruals ratio**: ~−0.04 (neutral).
- **Capital allocation grade**: C− (BillingTree, Payix, Kubra rollups; goodwill build; no impairment yet but trading 80% below buyback weighted avg).
- **Hidden value**: modest. Real estate $4.5M carrying. DTA $35M with $28M valuation allowance. No real stash thesis.
- **Activist lens**: SOTP ~$5.20 vs current $4.05 supports Forager $4.80 floor and a sponsor-led counter-bid case. Operating margin ~600bp below FOUR/GPN/PAY median — margin-recovery is a credible activist lever.

Final markdown sections in `RPAY_financial_assessment.md`:
1. Header (ticker, CIK, profile, as-of, latest filings).
2. Universal layer (balance sheet, cash flow, accruals, revenue recognition, capital allocation, hidden value).
3. Profile-specific lens (activist).
4. Peer comp table.
5. Synthesis tying universal findings to thesis question.
6. Data quality concerns (empty in this case).
7. Confidence summary.

Final JSON file `RPAY_metrics.json` populated per schema.

## Failure modes and recovery

- **EDGAR rate-limit (HTTP 429)**: respect the 10 req/s SEC limit, exponential backoff with jitter (max 5 retries). On exhaustion, return partial metrics with `confidence ≤ 0.5` on un-fetched fields and `data_quality_concerns: ["edgar_rate_limited"]`.
- **CIK invalid / company not found**: write status JSON with `{"status": "error", "error_class": "entity_unresolved", "recoverable": false}` and exit 1. Do not write a partial assessment file.
- **Filing parser failure (XBRL inconsistency)**: capture which field, lower confidence on that field to 0.3, source = `parser_failure`. Continue with remaining fields.
- **Peer comp lookup failure**: skip §4 with warning. Mark `peer_comps_available: false`.
- **Profile mismatch (e.g., user asks for binary_catalyst on a non-pharma company)**: output universal layer only and flag in `data_quality_concerns: ["profile_mismatch"]`. Do not silently fall back.
- **Going-concern flagged**: still produce output but elevate to top of synthesis section.

No silent failures. Every degraded output writes a `data_quality_concerns` entry and lowers `overall_confidence` accordingly.

## Compliance with system invariants

- All writes are atomic (temp file + rename) per D-052.
- All output rows carry `confidence` (0.0–1.0) and `source` (URL or file path) fields per CLAUDE.md §1.6.
- Append-only behavior: if a prior assessment exists for this ticker, the new file is timestamped and the prior is moved to `outputs/_archive/<ticker>_financial_assessment_<date>.md`.
- Never modifies the reference folder. All writes go to the working folder under `skills/analyze-candidate-financials/outputs/`.
- Tickers always rendered with company names per `feedback_ticker_company_names.md` — i.e., `RPAY (Repay Holdings Corporation)` in narrative prose, never `RPAY` alone in user-facing dashboards.
- HALT_FLAG check at startup; exit immediately if present.
- py_compile clean for any helper script invocations.
