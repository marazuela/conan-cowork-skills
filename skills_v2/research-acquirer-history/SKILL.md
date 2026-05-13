---
name: research-acquirer-history
description: Counterparty intelligence for an acquirer in an announced or rumored M&A deal. For a given acquirer name (or CIK / LEI / local filer ID) plus current target ticker plus deal jurisdiction, this skill enumerates every prior M&A transaction the acquirer has been a buyer in over the lookback window, classifies each by outcome (closed / withdrawn / blocked-by-regulator / re-priced / topped), captures financing approach (all-cash / stock / cash-and-stock / debt-funded), regulatory outcomes broken out per jurisdiction (US DOJ-FTC, UK CMA, EU EC, China MOFCOM, Australia FIRB, Hong Kong SFC, Korea FSC, India SEBI, Brazil CADE), MAC clause invocation patterns from definitive agreements, time-to-close distribution, premium-paid distribution, and break-fee patterns. Computes prior-deal count, success rate, average time-to-close, average premium-to-VWAP, regulatory clearance rate by jurisdiction, MAC-invocation rate, and benchmarks against tier-1 strategic and financial sponsor reference profiles. Triggers when a merger_arb signal hits the engine ("who is this acquirer", "BAWAG track record", "has this PE shop closed deals", "research acquirer <name>", "is this acquirer credible", "compute close-rate for <buyer>") or as a sub-step of a merger_arb dossier refresh that needs acquirer credibility evidence. Operates against SEC EDGAR (DEFM14A / S-4 / SC TO-T / SC TO-I / 8-K Item 1.01 + 2.01), DOJ-FTC merger filings (HSR), CMA / EC / FIRB / MOFCOM / SFC / FSC / SEBI / CADE press release feeds, plus EDGAR full-text search for definitive merger agreements (MAC parse). Produces a markdown M&A history report plus a structured JSON sidecar consumed by the U2 thesis composer (Acquirer Track Record dimension scoring) and the merger_arb dossier writer.
type: skill
---

# research-acquirer-history

## Purpose

Anchor a merger_arb signal to *the acquirer's actual closing record*, rather than a generic guess that "any announced deal closes." The skill is the principal upstream feeder for the merger_arb scoring profile's Dimension 2 (Deal Certainty, weighted ×2.5) and is consumed by U2 thesis composition (variant perception, kill criteria, conviction grade, sizing).

A definitive merger agreement signed by Berkshire Hathaway is a different signal than the same agreement signed by a serial-bidder PE shop with a 30% close rate. The skill quantifies the difference: prior deal count, close rate, regulatory-clearance rate broken out by jurisdiction, MAC-invocation history, time-to-close distribution, and a tier benchmark. P4 produces a JSON sidecar that downstream skills (and the human reader) can consume, plus a human-readable markdown report citing every prior deal with its primary-source URL.

It is invoked when:

- A merger-arb scanner surfaces an announced deal whose acquirer track record is not already in the active book.
- Pedro asks for acquirer context ("what's BAWAG's M&A record?", "is this PE buyer credible?", "has this strategic ever blown a deal on antitrust?").
- An existing merger-arb dossier's Deal Certainty score is unjustified or stale (>180 d).
- The U2 thesis composer requires Deal Certainty evidence (kill criteria → financing fall-through, MAC invocation, regulatory block) and the sidecar at the expected path is missing.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `acquirer_name_or_cik` | string | `BAWAG Group AG` or CIK `0001735707` or LEI `529900SLLA8R3JEDDS73` | yes |
| `current_target_ticker` | string | `PTSB` | yes |
| `current_target_cik` | string | `1738758` | optional, used to verify the current campaign is reflected (won't be a "prior" deal) |
| `deal_jurisdiction` | string | `IE`, `US`, `UK`, `EU`, `JP`, `AU`, `HK`, `KR`, `IN`, `BR` | yes |
| `lookback_years` | int | `10` | optional, default `10` |
| `output_dir` | path | `skills/research-acquirer-history/outputs/` | optional, default to skill outputs/ |
| `offline` | bool | `false` | optional; when true the skill returns illustrative defaults sourced from a hand-curated reference card (used by smoke tests when network unavailable) |
| `target_max_deals` | int | `30` | optional, hard cap on deals parsed |

If `acquirer_name_or_cik` is a CIK pattern (10-digit zero-padded, or numeric stripped), the skill skips name resolution and queries EDGAR directly. Otherwise it attempts to resolve the acquirer name → identifier via this priority:

1. **EDGAR EFTS lookup** filtered by `forms=DEFM14A,SC TO-T,S-4` for the literal acquirer name; harvest the CIK that filed the most matching filings naming this acquirer in `display_names`.
2. **EDGAR company-search HTML scrape** at `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=DEFM14A,SC+TO,S-4`.
3. **Foreign-filer fallback** — for acquirers without EDGAR presence, the skill records `acquirer_id_type = "name_only"` and queries international regulators by literal name match (CMA decisions, EC competition cases, FIRB approvals).
4. **LEI lookup (optional)** — when an LEI is supplied directly, the skill annotates the LEI and uses it as the canonical key for cross-jurisdiction matching.

Resolution confidence is recorded:
- 0.90+ if exact-match CIK found via EFTS with ≥3 prior M&A filings under that CIK and the acquirer name in `display_names`.
- 0.75 if EFTS surfaced multiple CIK candidates and the most-filing one was selected.
- 0.55 if HTML fallback was used.
- 0.50 if `name_only` resolution against international regulators (no EDGAR presence).
- 0.30 if no identifier resolved — skill emits `{"status": "no_acquirer_resolved", ...}` and refuses to compute a track record (never guesses).

## Outputs

Atomic-written:

1. `skills/research-acquirer-history/outputs/<acquirer_slug>_ma_history.md` — human-readable report listing every prior deal, regulatory outcome by jurisdiction, financing approach, time-to-close, premium paid, and a tier-1 benchmark comparison.
2. `skills/research-acquirer-history/outputs/<acquirer_slug>_deals.json` — structured sidecar consumed by U2 / scoring profile.

`<acquirer_slug>` is `re.sub(r"[^A-Za-z0-9]+", "_", acquirer_name).strip("_")` lowercased — e.g., `bawag_group_ag` for `BAWAG Group AG`.

Final stdout JSON summary line:
```
{"status":"ok","acquirer":"BAWAG Group AG","cik":null,"prior_deals":N,"close_rate":0.85,"avg_time_to_close_days":210,"avg_premium_pct":24.1,"mac_invocation_rate":0.0,"tier_classification":"established_strategic","confidence":0.78,"output_md":"...","output_json":"...","duration_s":T}
```

Failure exit codes are non-zero with structured stderr; the skill never silently swallows errors.

### Sidecar schema (`<acquirer>_deals.json`)

```json
{
  "acquirer_name": "BAWAG Group AG",
  "acquirer_id_type": "name_only",
  "acquirer_cik": null,
  "acquirer_lei": "529900SLLA8R3JEDDS73",
  "acquirer_aliases": ["BAWAG P.S.K.", "BAWAG Holding GmbH"],
  "acquirer_country": "AT",
  "as_of": "2026-04-29T01:30:00Z",
  "lookback_years": 10,
  "current_target": {
    "ticker": "PTSB",
    "cik": "1738758",
    "company_name": "Permanent TSB Group Holdings",
    "jurisdiction": "IE",
    "in_deal_list": false
  },

  "n_prior_deals": 6,
  "n_closed": 5,
  "n_withdrawn": 0,
  "n_blocked": 0,
  "n_repriced": 1,
  "n_active": 0,
  "close_rate": 0.833,
  "close_rate_ci": [0.44, 0.97],
  "avg_time_to_close_days": 210,
  "median_time_to_close_days": 195,
  "avg_premium_pct": 24.1,
  "median_premium_pct": 22.5,
  "mac_invocation_rate": 0.0,
  "break_fee_paid_count": 0,

  "financing_distribution": {
    "all_cash": 5,
    "all_stock": 0,
    "cash_and_stock": 1,
    "debt_funded": 0
  },

  "regulatory_outcomes_by_jurisdiction": {
    "EU": {"deals_reviewed": 4, "cleared_unconditional": 3, "cleared_with_remedies": 1, "blocked": 0, "withdrawn_pre_decision": 0},
    "AT": {"deals_reviewed": 3, "cleared_unconditional": 3, "cleared_with_remedies": 0, "blocked": 0, "withdrawn_pre_decision": 0},
    "DE": {"deals_reviewed": 2, "cleared_unconditional": 2, "cleared_with_remedies": 0, "blocked": 0, "withdrawn_pre_decision": 0},
    "US": {"deals_reviewed": 1, "cleared_unconditional": 1, "cleared_with_remedies": 0, "blocked": 0, "withdrawn_pre_decision": 0},
    "IE": {"deals_reviewed": 0, "cleared_unconditional": 0, "cleared_with_remedies": 0, "blocked": 0, "withdrawn_pre_decision": 0}
  },

  "tier_classification": "established_strategic",
  "tier_benchmarks": {
    "tier_1_strategic_minimum_deals": 10,
    "tier_1_strategic_minimum_close_rate": 0.85,
    "tier_1_pe_minimum_deals": 20,
    "tier_1_pe_minimum_close_rate": 0.80,
    "this_acquirer_vs_tier_1": "below_deal_count_threshold"
  },

  "deals": [
    {
      "deal_id": "bawag_<target_slug>_<announced_date>",
      "target_ticker": "DPB",
      "target_cik": null,
      "target_company_name": "Deutsche Pfandbriefbank AG (illustrative)",
      "target_country": "DE",
      "sector": "Financials",
      "announced_date": "2024-06-15",
      "definitive_agreement_url": "https://www.example.com/dpb_offer.pdf",
      "deal_value_usd_mm": 1450,
      "consideration_type": "all_cash",
      "premium_to_30d_vwap_pct": 22.0,
      "structure": "tender_offer",
      "regulatory_jurisdictions": ["EU", "DE", "AT"],
      "regulatory_outcomes": {
        "EU": {"agency": "European Commission DG COMP", "outcome": "cleared_unconditional", "decision_date": "2024-11-30", "source": "https://ec.europa.eu/competition/elojade/isef/case_details.cfm?proc_code=2_M.11500"},
        "DE": {"agency": "Bundeskartellamt", "outcome": "cleared_unconditional", "decision_date": "2024-09-12", "source": "https://www.bundeskartellamt.de/..."},
        "AT": {"agency": "Bundeswettbewerbsbehörde", "outcome": "cleared_unconditional", "decision_date": "2024-09-05", "source": "https://www.bwb.gv.at/..."}
      },
      "outcome_status": "closed",
      "outcome_event_date": "2025-01-20",
      "time_to_close_days": 219,
      "mac_invoked": false,
      "mac_clause_quoted": "...material adverse effect on the Target taken as a whole...",
      "break_fee_terms": "EUR 25M payable by Target if board changes recommendation",
      "financing_source": "balance_sheet_cash + EUR 500M revolver",
      "post_close_realized_premium_to_announce_pct": 0.0,
      "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2024-06-15-Offer.html",
      "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2025-01-20-Closing.html",
      "confidence": 0.85
    }
  ],

  "international_extensions": {
    "uk_cma_decisions": [],
    "eu_ec_decisions": [],
    "au_firb_decisions": [],
    "cn_mofcom_decisions": [],
    "hk_sfc_takeover_panel": [],
    "kr_fsc_decisions": [],
    "in_sebi_decisions": [],
    "br_cade_decisions": []
  },

  "mac_clause_patterns": {
    "deals_with_mac_carve_outs_for_pandemic_or_war": 4,
    "deals_with_financing_condition_carve_outs": 2,
    "deals_with_no_mac_clause": 0,
    "deals_with_invoked_mac": 0,
    "trend": "tightening"
  },

  "confidence": 0.78,
  "source": "SEC EDGAR + EU EC case database + DE Bundeskartellamt + AT BWB + acquirer IR press releases",
  "data_quality_notes": [
    "n_prior_deals < 10 — close rate weakly anchored",
    "BAWAG is non-US filer; EDGAR-only resolution paths return zero — primary sources are EU/AT regulators and acquirer IR site"
  ]
}
```

The merger_arb scoring profile reads `close_rate`, `n_prior_deals`, `mac_invocation_rate`, and `tier_classification` to inform Dimension 2 (Deal Certainty, ×2.5):
- Tier-1 strategic with close_rate ≥0.85 over n≥10 → +0.5 to Certainty rubric score (push toward 5).
- Established strategic (close_rate ≥0.80, n≥5) → +0.25.
- First-time-or-rare-acquirer with no track record → no bonus, mark `data_quality_notes` flag.
- MAC-invocation-prone acquirer (rate ≥0.10) → −0.5 to Certainty.
- Pattern of withdrawn/blocked deals in the same jurisdiction as current deal → cap Certainty score at 3 unless a credible mitigation is documented.

U2 reads `close_rate_ci`, `mac_clause_patterns`, and `regulatory_outcomes_by_jurisdiction[deal_jurisdiction]` to assemble the kill-criteria field (regulatory block, MAC invocation, financing fall-through) and the conviction grade.

## Methodology

### Step 0 — HALT-flag check

Read `<reference>/02_System/engine/health/HALT_FLAG`. If present, log and exit with status `halted`. Reference folder is read-only.

### Step 1 — Resolve acquirer identifier

If input is a CIK pattern (10 digits zero-padded, or numeric ≤10 digits), accept directly and pad to 10 digits. Set `acquirer_id_type = "cik"`.

Otherwise:

1. **EFTS lookup**: query `https://efts.sec.gov/LATEST/search-index?q=%22<name>%22&forms=DEFM14A,S-4,SC+TO-T,SC+TO-I` (URL-encoded). Parse `hits.hits[*]._source.ciks` and `display_names`. Aggregate filings per CIK; pick the CIK with the most matching filings AND with `display_names` containing the literal acquirer name.

2. **Company-search HTML fallback**: if EFTS returns 0 or ambiguous, fetch `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=&dateb=&owner=include&count=40` and parse the result table. Lower confidence to 0.55.

3. **Foreign-filer fallback** — if both above return 0, set `acquirer_id_type = "name_only"`, `acquirer_cik = null`, and proceed against international regulators by literal-name match. Confidence 0.50. This is the typical path for non-US acquirers (BAWAG AG, Mitsubishi UFJ, Unilever PLC, etc.) that have not filed in EDGAR for the lookback window.

If all three fail, return `{"status": "no_acquirer_resolved", ...}` and exit 2.

### Step 2 — Pull all prior M&A filings (US path)

When `acquirer_id_type == "cik"`:

Query the EDGAR submissions API:
```
https://data.sec.gov/submissions/CIK<10-digit-padded>.json
```

Parse `filings.recent` and any `filings.files[*]` continuation files. Filter to forms in `{"DEFM14A", "PREM14A", "S-4", "S-4/A", "SC TO-T", "SC TO-T/A", "SC TO-I", "SC TO-I/A", "SC 13E3", "SC 13E3/A", "8-K"}` and `filingDate >= now - lookback_years`. For 8-Ks, narrow to those mentioning Item 1.01 (Material Definitive Agreement) or Item 2.01 (Completion of Acquisition) by parsing the filing index.

For each candidate filing, follow the index URL and parse the primary document to determine whether the filer is the *acquirer* (vs the target). Acquirer-side filings include: S-4 prospectuses (acquirer is the registrant offering its shares as consideration), SC TO-T schedules (acquirer making a tender offer), DEFM14A where the acquirer's shareholders are voting on the merger. Target-side filings (DEFM14A by the target, SC 14D9 responses, etc.) are *excluded*.

For older filings (>5y), also call:
```
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<padded>&type=&dateb=&owner=include&count=400
```
Parse the HTML table for missing filings.

For each filing classified as acquirer-side, capture: accession number, primary document URL, filing date, form, target identifier (parsed from filing body), deal value (parsed from form headers / body / exhibit financials), consideration type, structure, financing source.

### Step 3 — Pull all prior M&A filings (international path)

When `acquirer_id_type == "name_only"` (or as a complementary pass when CIK is set but acquirer is also active outside US), query the per-jurisdiction regulator press-release / decision feeds:

| Jurisdiction | Source |
|---|---|
| EU | https://ec.europa.eu/competition/elojade/isef/index.cfm (case database, DG COMP) |
| UK | https://www.gov.uk/cma-cases (CMA published decisions) |
| Germany | https://www.bundeskartellamt.de/SiteGlobals/Forms/Suche/Entscheidungssuche_Formular.html |
| Austria | https://www.bwb.gv.at/entscheidungen |
| France | https://www.autoritedelaconcurrence.fr/fr/decisions |
| Italy | https://www.agcm.it/dotcmsdoc/decisioni-di-recente-adozione |
| Australia | https://www.firb.gov.au/news (FIRB approvals; commercial-in-confidence partial) + https://www.accc.gov.au/public-registers/mergers-registers |
| China | http://gkml.samr.gov.cn/nsjg/fldj/ (SAMR / former MOFCOM antimonopoly decisions) |
| Hong Kong | https://www.sfc.hk/en/Regulatory-functions/Listings-and-takeovers/Takeovers-and-Mergers (SFC Takeovers Panel + Stock Exchange of HK) |
| Korea | https://www.ftc.go.kr (KFTC) |
| India | https://www.cci.gov.in/antitrust/orders (Competition Commission of India) + https://www.sebi.gov.in (SEBI for takeover code) |
| Japan | https://www.jftc.go.jp/en/pressreleases/yearly/index.html (JFTC merger reviews) |
| Brazil | https://www.gov.br/cade/pt-br/assuntos/casos-de-destaque (CADE merger reviews) |
| Canada | https://www.canada.ca/en/competition-bureau/news/notices.html |

For each source, query by acquirer name (literal + each alias). Parse the result list for: deal name, target, decision date, decision outcome (cleared_unconditional / cleared_with_remedies / blocked / withdrawn_pre_decision), source URL.

Each per-jurisdiction query is best-effort. When a jurisdiction's API is unavailable, mark `data_quality_notes: ["<jurisdiction>_unavailable"]` and continue. Never crash.

### Step 4 — Group filings into deals

Group acquirer-side filings by `(acquirer_id, target_id, announce_year)`. Each group is one deal:
- `announced_date` = earliest filing date in the group.
- `definitive_agreement_url` = the S-4 / DEFM14A primary doc URL (or SC TO-T for tender offers).
- `deal_value_usd_mm` = parsed from filing or merger agreement; null if not parseable.
- `consideration_type` = parsed from definitive agreement Item 1 / Plan of Merger / Offer to Purchase.
- `structure` = `merger`, `tender_offer`, `scheme_of_arrangement`, `going_private`, `stock_for_stock_merger`, `cash_and_stock_merger`.
- `regulatory_jurisdictions` = list of jurisdictions where the deal triggered review (parsed from definitive agreement filings index OR inferred from target country + acquirer country + deal value crossing local thresholds).

A deal is considered the same when target_id matches; multiple filings (PREM14A → DEFM14A → 8-K Item 2.01 close) all roll up into the single deal record.

### Step 5 — Resolve outcome per deal

For each deal, classify the outcome by querying the *target's* EDGAR submissions API (US deals) or the acquirer's IR press-release feed (international deals) for filings ≥ `announced_date`:

| Outcome | Detection rule | Confidence |
|---|---|---|
| **Closed** | 8-K Item 2.01 (Completion of Acquisition or Disposition of Assets) by target with effective date OR acquirer press release "deal closes" with the target name | 0.90 |
| **Withdrawn (acquirer pulled)** | 8-K Item 1.02 (Termination of Material Definitive Agreement) where the acquirer or target announces termination + acquirer paid no break fee, OR acquirer-side announcement of withdrawal | 0.85 |
| **Withdrawn (target accepted competing bid)** | 8-K naming a competing acquirer + termination announcement | 0.85 |
| **Blocked by regulator** | Press release / 8-K naming the regulator (DG COMP, FTC, CMA, MOFCOM, etc.) + decision URL | 0.95 |
| **Withdrawn pre-decision (regulatory headwind)** | Acquirer withdrew during a Phase II review with public statement citing regulatory concerns | 0.75 |
| **Re-priced (price cut or bumped post-announcement)** | Amendment to definitive agreement + 8-K Item 1.01/A | 0.85 |
| **MAC invoked** | 8-K mentioning MAC / Material Adverse Change invocation by acquirer | 0.95 (with primary source) |
| **Active** | None of the above; latest filing within last 365d | 0.70 |
| **Stale-active** | None of the above; no filings in last 365d | 0.50 — flag for manual review |

For each outcome, capture: `outcome_event` (free text), `outcome_event_date`, `source URL` (primary filing). When in doubt, assign `outcome_status = "manual_review"` with confidence 0.50. Never guess.

### Step 6 — Resolve regulatory outcomes per deal per jurisdiction

For each deal, for each jurisdiction in `regulatory_jurisdictions`:

1. Query the per-jurisdiction regulator feed for a decision matching the deal (by acquirer name + target name + announce_year window).
2. Capture: `agency`, `outcome` (cleared_unconditional / cleared_with_remedies / blocked / withdrawn_pre_decision), `decision_date`, `source` URL.
3. If a deal triggered review in a jurisdiction but no decision is found, mark `outcome = "review_status_unknown"` with confidence 0.40 and continue.

The skill maintains a per-jurisdiction parser registry in `helpers/regulatory_outcome_tracker.py` that knows the URL pattern and parse logic for each agency listed in Step 3.

### Step 7 — MAC clause extraction (when definitive agreement parseable)

For each deal where the definitive agreement (S-4, definitive merger agreement exhibit, scheme document, offer document) is retrievable:

1. Locate the MAC / Material Adverse Effect / Material Adverse Change clause (typically Article I or Article VI).
2. Extract the carve-out list (pandemics, wars, market-wide events, sector-wide events, changes in law, etc.).
3. Extract the financing-condition status (financing condition / no financing condition).
4. Extract the break-fee terms (target-side break fee, acquirer-side reverse termination fee).
5. Score on the MAC-tightness scale: tight (broad target-friendly carve-outs, no financing condition) / moderate / loose (acquirer-friendly carve-outs).

Persist into the deal record: `mac_clause_quoted`, `mac_carve_outs`, `mac_tightness_score`, `break_fee_terms`, `financing_condition_present`.

### Step 8 — Compute aggregate metrics

- `close_rate` = `n_closed / (n_closed + n_withdrawn + n_blocked + n_repriced_to_close)`. Active deals are excluded from the denominator. Re-priced deals that ultimately close count as "closed."
- `close_rate_ci` = Wilson 95% CI on `(n_closed, n_resolved)`.
- `avg_time_to_close_days` = mean of (outcome_event_date − announced_date) for closed deals.
- `median_time_to_close_days` = median of same.
- `avg_premium_pct` = mean of (deal_value − target_30d_vwap_market_cap) / target_30d_vwap_market_cap. Where 30d VWAP is unavailable, fall back to T-1 close.
- `mac_invocation_rate` = `n_deals_with_invoked_mac / n_resolved`.
- `financing_distribution` = Counter over `consideration_type` across all deals.
- `regulatory_outcomes_by_jurisdiction` = aggregated table: per jurisdiction, count of cleared_unconditional / cleared_with_remedies / blocked / withdrawn_pre_decision.

### Step 9 — Tier classification + benchmark comparison

Tier-1 acquirer benchmark tables in `helpers/tier1_acquirer_benchmark.py`:

**Tier-1 Strategic acquirers (selected; benchmark only):**
| Acquirer | Approx prior-deal count | Approx close rate | Tier | Notes |
|---|---|---|---|---|
| Microsoft | 30+ | 0.92 | 1 | Tech rollup; high close rate |
| Berkshire Hathaway | 80+ | 0.95 | 1 | Friendly only; record close rate |
| JP Morgan Chase | 40+ | 0.85 | 1 | Financial-services consolidator |
| Constellation Software | 200+ | 0.90 | 1 | Vertical-software roll-up |
| Roper Technologies | 60+ | 0.88 | 1 | Diversified industrials roll-up |
| Danaher | 40+ | 0.85 | 1 | Industrial conglomerate |
| Unilever PLC | 30+ | 0.80 | 1 | CPG roll-up |
| LVMH | 25+ | 0.85 | 1 | Luxury-goods roll-up |

**Tier-1 PE / Financial sponsor:**
| Sponsor | Approx prior-deal count | Approx close rate |
|---|---|---|
| KKR | 100+ | 0.85 |
| Blackstone | 100+ | 0.85 |
| Apollo Global Management | 80+ | 0.80 |
| Carlyle | 80+ | 0.80 |
| TPG | 70+ | 0.80 |
| Bain Capital | 60+ | 0.80 |
| CD&R | 50+ | 0.80 |
| Advent International | 50+ | 0.80 |
| Permira | 40+ | 0.80 |

Classification rules (in order):

1. **Tier-1 named** — exact match on `acquirer_aliases` vs the tier-1 reference list (case-insensitive, punctuation-stripped) → `tier_classification = "tier_1_strategic"` or `"tier_1_pe"`.
2. **Established strategic** — `n_prior_deals ≥ 5 AND close_rate ≥ 0.80` AND `is_corporate (not_pe)` → `"established_strategic"`.
3. **Established PE** — `n_prior_deals ≥ 10 AND close_rate ≥ 0.75` AND `is_pe` → `"established_pe"`.
4. **Emerging acquirer** — `n_prior_deals ≥ 1 AND (n_prior_deals < 5 OR close_rate < 0.80)` → `"emerging"`.
5. **First-time** — `n_prior_deals == 0 AND current_target.in_deal_list == false` → `"first_time"`.
6. **Unknown** — `n_prior_deals == 0` after exhaustive resolution AND no current-target hit → `"unknown"`.

`tier_benchmarks.this_acquirer_vs_tier_1` reports `"at_or_above_threshold"` if both `n_prior_deals ≥ tier_1_minimum_deals` AND `close_rate ≥ tier_1_minimum_close_rate`; else `"below_threshold"` (and which threshold failed).

PE vs strategic detection: heuristic on acquirer name (suffixes `LP`, `Partners`, `Capital`, `Fund`, etc.) + presence of fund vintage in name + EDGAR registration form (most PE shops file Form ADV with the SEC and not S-4). Confidence ≤ 0.75 on the PE / strategic classification when not in the tier-1 reference table.

### Step 10 — Atomic-write outputs

Write the JSON sidecar and markdown report through `atomic_write_text` (temp + rename). Use the same helper module shared with P1/P2/P3/U4.

Write order: JSON first, then markdown. If JSON write fails, abort before markdown is touched (so consumers see missing-sidecar rather than stale-mismatch state).

## Profile-specific application

This skill is `merger_arb` only. It is not invoked for `activist_governance` (use P3 instead — research-activist-filer), `binary_catalyst`, `litigation`, or `insider`. Profile dispatch is the caller's responsibility (U2 thesis composer, scanner-triage layer, or human invocation).

A future P6 skill (`assess-takeover-vulnerability`, pre-edge merger_arb) will *consume* P4 outputs to weight the credibility of its plausible-acquirer rank list — but P4 itself does not orchestrate P6.

## Output schema

See **Outputs → Sidecar schema** above for JSON. The markdown report follows this skeleton:

```
# Acquirer M&A History — <acquirer_name>

**Acquirer ID type:** <cik | name_only | lei>
**CIK:** <cik or "n/a">
**LEI:** <lei or "n/a">
**Aliases:** <comma list>
**Country:** <ISO-2>
**As of:** <ISO timestamp>
**Lookback:** <years>y
**Current target under review:** <ticker> (<company>) — jurisdiction <ISO-2>

## Headline metrics

| Metric | Value | n | CI95 | Source |
|---|---|---|---|---|
| Prior deals | 6 | – | – | EDGAR submissions API + EU EC + AT BWB |
| Closed | 5 | – | – | derived |
| Withdrawn | 0 | – | – | derived |
| Blocked | 0 | – | – | derived |
| Re-priced | 1 | – | – | derived |
| Active | 0 | – | – | derived |
| Close rate | 83.3% | 5/6 | 44%–97% | derived |
| Avg time-to-close | 210d | – | – | derived |
| Avg premium to 30d VWAP | 24.1% | – | – | derived |
| MAC invocation rate | 0% | 0/6 | – | parsed defs |

## Tier classification

**<tier_classification>** — <rationale>

Versus tier-1 strategic / PE benchmark (Microsoft / Berkshire / JPM / Constellation / Roper / KKR / Blackstone / Apollo / Carlyle / TPG / Bain / CD&R / Advent / Permira):
- Tier-1 minimum deals: 10 (strategic) / 20 (PE) → this acquirer: <n>
- Tier-1 minimum close rate: 85% (strategic) / 80% (PE) → this acquirer: <pct>
- Verdict: <at_or_above_threshold | below_threshold (criterion)>

## Deals

### Deal 1 — <target_ticker> (<target_company>)

- Sector: <sector>
- Country: <ISO-2>
- Announced: <date>
- Deal value: $<value>M
- Consideration: <consideration_type>
- Structure: <structure>
- Premium to 30d VWAP: <pct>%
- Definitive agreement: [link](<url>)
- Regulatory jurisdictions: <comma list>
- Per-jurisdiction outcomes:
    - <jurisdiction>: <outcome> (<agency>, <decision_date>, [source](<url>))
- Outcome: <outcome_status> — <outcome_event> (<outcome_event_date>)
- Time to close: <days>
- MAC invoked: <yes/no>
- MAC carve-outs: <list>
- Break fee: <text>
- Financing source: <text>
- Confidence: <0.0-1.0>

(repeat for each deal)

## Regulatory clearance summary by jurisdiction

| Jurisdiction | Reviewed | Cleared unconditional | Cleared w/ remedies | Blocked | Withdrawn pre-decision |
|---|---|---|---|---|---|
| EU | 4 | 3 | 1 | 0 | 0 |
| AT | 3 | 3 | 0 | 0 | 0 |
| ... | | | | | |

## Financing distribution

| Consideration | Deals |
|---|---|
| All-cash | 5 |
| All-stock | 0 |
| Cash-and-stock | 1 |
| Debt-funded | 0 |

## MAC clause patterns

- Deals with pandemic / war carve-outs: <n>
- Deals with financing-condition carve-outs: <n>
- Deals with no MAC clause: <n>
- Deals with invoked MAC: <n>
- Trend over lookback: <tightening | loose | stable>

## International extensions

- UK CMA: <list or none>
- EU EC: <list or none>
- AU FIRB: <list or none>
- CN MOFCOM/SAMR: <list or none>
- HK SFC: <list or none>
- KR FSC: <list or none>
- IN SEBI: <list or none>
- BR CADE: <list or none>

## Data-quality notes

- <each entry from data_quality_notes>

## Sources

- Acquirer submissions API: <url or n/a>
- Per-deal primary docs: <list>
- Per-jurisdiction regulator decisions: <list>
- Tier-1 benchmark reference: helpers/tier1_acquirer_benchmark.py

---

*Skill: research-acquirer-history.*
```

## Worked example — BAWAG Group AG on PTSB (Permanent TSB)

Input:
- `acquirer_name_or_cik = "BAWAG Group AG"`
- `current_target_ticker = "PTSB"`
- `current_target_cik = "1738758"`
- `deal_jurisdiction = "IE"`

Step 1 — Identifier resolution: EFTS query for `"BAWAG Group AG"` filtered by `forms=DEFM14A,S-4,SC TO-T,SC TO-I` returns 0 hits (BAWAG has not filed in EDGAR within the lookback window). Falls back to company-search HTML — also 0 hits. Falls back to `acquirer_id_type = "name_only"`, `acquirer_country = "AT"`, confidence 0.50. Aliases recorded: `["BAWAG P.S.K.", "BAWAG Holding GmbH"]`.

Step 2 — US path: skipped (no CIK).

Step 3 — International path: query EU EC case database, AT BWB, DE Bundeskartellamt, UK CMA for "BAWAG" matches in last 10y. Suppose returns 6 illustrative prior deals (actual count produced live in production):

- 2024-06: BAWAG / Deutsche Pfandbriefbank (illustrative) — DE / EU / AT review — closed 2025-01-20, all-cash, premium 22%, no MAC invocation.
- 2023-03: BAWAG / Knab (Dutch online bank, illustrative) — NL / EU review — closed 2023-09-28, all-cash, premium 18%.
- 2022-08: BAWAG / Raiffeisen Bausparkasse (illustrative AT consolidation) — AT review — closed 2023-02-15, all-cash, premium 20%.
- 2021-11: BAWAG / Hello bank! (BNP Paribas Austria, illustrative) — AT / FR review — closed 2022-04-30, all-cash, premium 15%.
- 2020-06: BAWAG / Südwestbank (illustrative DE add-on) — DE review — closed 2020-12-15, cash-and-stock, premium 25%.
- 2019-04: BAWAG / Sirio (illustrative IT consumer-finance carve-out) — EU review, cleared with remedies (loan-portfolio divestiture) — closed 2019-12-20, all-cash, premium 30%, re-priced once during diligence.

Step 4 — Group into 6 deals.

Step 5 — Outcome resolution per deal: 5 closed straightforwardly, 1 re-priced and then closed.

Step 6 — Regulatory outcomes per deal per jurisdiction (illustrative): EU 4 reviews (3 cleared unconditional, 1 cleared with remedies), AT 3 reviews (3 cleared unconditional), DE 2 reviews (2 cleared unconditional), NL 1 review (cleared unconditional), FR 1 review (cleared unconditional), IT 1 review (cleared with remedies).

Step 7 — MAC clause extraction: 4 deals have pandemic / war carve-outs, 2 have financing-condition carve-outs, 0 have no MAC clause, 0 with invoked MAC. Trend: tightening over time.

Step 8 — Aggregate:
- n_prior_deals = 6, n_closed = 5 (counting the re-priced one as "closed"), n_withdrawn = 0, n_blocked = 0, n_repriced = 1.
- close_rate = 5/6 = 0.833 (Wilson CI 0.44–0.97 — wide because n=6).
- avg_time_to_close_days = 210, median 195.
- avg_premium_pct = 21.7%, median 21.0%.
- mac_invocation_rate = 0/6 = 0.0%.
- financing_distribution = `{"all_cash": 5, "all_stock": 0, "cash_and_stock": 1, "debt_funded": 0}`.

Step 9 — Tier classification: name not in tier-1 list. n_prior_deals = 6 ≥ 5; close_rate = 0.833 ≥ 0.80; no PE-shop signals (no LP / Partners / Fund in name; corporate group structure) → `tier_classification = "established_strategic"`. Verdict vs tier-1 strategic: `below_deal_count_threshold` (6 < 10), but close-rate threshold is met.

Step 10 — Atomic-write outputs. Final JSON contains all 6 deals with primary-source URLs (when available); markdown report human-readable; structured sidecar consumed by U2 (Deal Certainty dimension scoring rationale).

Sidecar `confidence` ≈ 0.78 (n=6 deals, name-only acquirer resolution, regulator queries best-effort against 4 jurisdictions, MAC parses on 6/6 definitive agreements). The merger_arb scoring profile reading this sidecar would assign Dimension 2 (Deal Certainty) a score of 4 (Established strategic with high close rate, no MAC history, EU/AT regulatory familiarity matching the IE deal jurisdiction context), aligned with the PTSB dossier's qualitative read of BAWAG as a credible cross-border consolidator backed by the Irish Minister for Finance.

## Failure modes and recovery

| Failure mode | Detection | Skill response |
|---|---|---|
| EDGAR submissions API returns 5xx | HTTP status | Retry 3x with exponential backoff; if all fail, write `data_quality_notes: ["edgar_submissions_unavailable"]`, set sidecar confidence ≤ 0.40, return `{"status": "degraded", ...}` |
| EDGAR EFTS rate-limited (HTTP 429) | status code | Exponential backoff up to 60s; if still throttled, fall back to per-CIK browse-edgar HTML; mark `confidence ≤ 0.55` |
| International regulator API unavailable / 5xx | HTTP status | Mark `data_quality_notes: ["<jurisdiction>_unavailable"]`, set per-jurisdiction outcome confidence to 0.40, do NOT crash |
| Definitive agreement primary doc unparseable (S-4 / scheme document) | XML parse fail + HTML parse fail + plain-text regex empty | Mark that deal's `mac_clause_quoted = null`, `mac_tightness_score: null` with `parse_confidence: 0.30`; do NOT drop the deal |
| Acquirer name resolves to multiple CIKs | EFTS returns multiple `display_names` | Pick the one with the most M&A filings; record alternatives in `acquirer_aliases`; lower confidence to 0.70 |
| Acquirer name resolves to 0 CIKs and no international hits | All resolution paths empty | Return `{"status": "no_acquirer_resolved", "acquirer_name": "...", "confidence": 0.0}`, exit 2 |
| Deal outcome ambiguous | Multiple competing classifiers fire | Mark `outcome_status = "manual_review"`, `outcome_event = "ambiguous — multiple signals"`, confidence 0.50 |
| n_prior_deals == 0 (exhaustive but empty) | Step 2 + Step 3 yield 0 | Return sidecar with `n_prior_deals: 0`, `tier_classification: "first_time"` IF current deal is the acquirer's first signal; else `"unknown"`. Set confidence 0.50 |
| MAC clause not findable in definitive agreement | Regex / parse pass returns nothing for the standard clause locations | Mark `mac_clause_quoted: null` with `data_quality_notes: ["mac_parse_failed for deal_id <id>"]`; do NOT crash |
| HALT_FLAG present | check `02_System/engine/health/HALT_FLAG` at startup (read-only) | Log and exit immediately with status `halted` |
| Network-disabled environment (offline mode) | `--offline` flag | Use illustrative defaults from worked example, set confidence 0.30, mark `data_quality_notes: ["offline mode — values illustrative"]` |
| Tier-1 name overlap (false-positive match) | acquirer name e.g., "Apollo Funds Management" not = "Apollo Global Management" | Strict full-name match required; partial matches do NOT promote to tier_1; lower-confidence "name_collision" annotation in `data_quality_notes` |
| LEI lookup mismatch | Supplied LEI does not match ID returned by EDGAR or international regulator | Record both, lower confidence to 0.60, `data_quality_notes: ["lei_mismatch — manual reconciliation needed"]` |

No silent failures. Every degraded path produces a `data_quality_notes` entry and a lowered confidence.

## Compliance with system invariants

- **Atomic writes** (temp file + rename via `atomic_write_text`) per D-052 — both JSON sidecar and markdown report.
- **Confidence + source on every output row** — every deal, regulatory decision, MAC clause extraction, financing record, and the headline aggregate carry `confidence` (0.0–1.0) and `source` (URL or file path) per CLAUDE.md §1.6.
- **Append-only**: this skill writes to its own `outputs/` directory and never mutates ledgers in `02_System/engine/`. Folding acquirer M&A events into `historical_events_ledger.json` is M1's job, not P4's.
- **Reference folder is read-only** — the skill reads from `Investment tool backup/` (HALT_FLAG, profile_merger_arb.md, dossiers) but writes only to `Investment tool backup skills/skills/research-acquirer-history/outputs/`.
- **Bounded runtime** — typical run ≤ 60 s with full network access; offline mode ≤ 5 s.
- **Resumable** — re-running the skill on the same acquirer overwrites the prior output atomically; no partial-state leakage.
- **No invented data** — when a number is not in a primary source, the field is null with confidence 0.0 and a `data_quality_notes` entry; never guess.
- **Numeric tickers rendered with company names** — outputs always render tickers with company names per CLAUDE.md §1.7. e.g., `PTSB (Permanent TSB Group Holdings)`.
- **EDGAR User-Agent compliance** — every HTTP request includes `User-Agent: investment-tool-research-acquirer-history/1.0 javiergorordo13@hotmail.com` per SEC EDGAR fair-access policy.

## Helpers (in `helpers/`)

| Script | Purpose |
|---|---|
| `atomic_write.py` | Atomic write helper (temp file + rename). Re-imported pattern from P3 / monitor-kill-conditions/helpers. |
| `acquirer_ma_history.py` | EDGAR EFTS + submissions-API wrapper that resolves acquirer CIK and pulls all DEFM14A / S-4 / SC TO-T / 8-K Item 1.01 filings by that CIK with rate-limit backoff. Also handles the international name-only fallback path. |
| `regulatory_outcome_tracker.py` | Per-jurisdiction parser registry. Knows the URL pattern and parse logic for each agency (EU EC, UK CMA, DE Bundeskartellamt, AT BWB, FR Autorité, IT AGCM, AU FIRB+ACCC, CN SAMR, HK SFC, KR FTC, JP JFTC, IN CCI+SEBI, BR CADE, CA Competition Bureau). Returns `{"agency": ..., "outcome": ..., "decision_date": ..., "source": ...}` for each deal/jurisdiction pair. |
| `mac_clause_extractor.py` | Definitive-agreement parser. Locates MAC / Material Adverse Effect clause, extracts carve-outs, computes tightness score, captures break-fee terms and financing-condition status. Tiered parser (XML → HTML → plain-text fallback). |
| `tier1_acquirer_benchmark.py` | Hardcoded tier-1 strategic + PE benchmark tables (Microsoft, Berkshire, JPM, Constellation, Roper, Danaher, Unilever, LVMH, KKR, Blackstone, Apollo, Carlyle, TPG, Bain, CD&R, Advent, Permira) with approximate deal counts and close rates. Includes case-insensitive alias matcher and `is_pe` heuristic. |
| `analyze.py` | Orchestrator. Calls helpers in sequence, synthesizes JSON sidecar and markdown report, atomically writes outputs, prints structured stdout summary. |

All helpers obey: graceful network-error handling (return structured `{"ok": false, "reason": ...}` rather than crashing), atomic writes through the shared `atomic_write_text`, no global state.

## Invocation

CLI:
```
python helpers/analyze.py \
  --acquirer "BAWAG Group AG" \
  --target-ticker PTSB \
  --target-cik 1738758 \
  --jurisdiction IE \
  --output-dir <working>/skills/research-acquirer-history/outputs
```

For an offline smoke test (no network):
```
python helpers/analyze.py \
  --acquirer "BAWAG Group AG" \
  --target-ticker PTSB \
  --target-cik 1738758 \
  --jurisdiction IE \
  --offline \
  --output-dir <working>/skills/research-acquirer-history/outputs
```

The U2 thesis composer and merger_arb scanner-triage layer automatically pick up the resulting `<acquirer_slug>_deals.json` if it exists in the expected sibling outputs directory; no further wiring needed.

---

*Skill: research-acquirer-history. Built per Phase 2 of the autonomous skill-build plan, ratified 2026-04-29.*
