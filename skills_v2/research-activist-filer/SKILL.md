---
name: research-activist-filer
description: Counterparty intelligence for an activist or major-shareholder filer. For a given filer name or CIK, this skill enumerates prior SC 13D and SC 13D/A filings (vs. 13G passive), groups them into discrete campaigns by target issuer, and resolves outcomes per campaign — proxy-fight win/loss, sale or merger, settlement (board seats / cooperation agreement), withdrawal, or still-active. Computes prior-campaign count, success rate, average disclosed position size at filing, average holding period, sector concentration, and escalation patterns (rate of 13D/A amendments per campaign, board-nomination filings, language intensification). Benchmarks the filer against tier-1 activist references (Elliott, Icahn, Starboard, ValueAct, Trian, Jana, Engaged Capital). Has international extensions for UK FCA TR-1 disclosures, Japan METI 5%-rule large-shareholder filings, EU Transparency Directive notifications. Triggers when an activist signal hits the engine ("who is this filer", "Forager track record", "activist 13D from <fund>", "research filer <name>", "is this filer credible", "compute success rate for <activist>") or as a sub-step of an activist_governance dossier refresh that needs filer credibility evidence. Operates against SEC EDGAR EFTS full-text search (https://efts.sec.gov/LATEST/search-index) and the EDGAR submissions API (https://data.sec.gov/submissions/CIK<padded>.json), with CIK resolution via the EDGAR company-tickers / company-search endpoints. Produces a markdown track-record report plus a structured JSON sidecar consumed by the U2 thesis composer (Activist Track Record dimension scoring) and the activist_governance dossier writer.
type: skill
---

# research-activist-filer

## Purpose

Anchor an activist-governance signal to *the filer's actual track record*, rather than a generic guess that "any 13D filer is credible." The skill is the principal upstream feeder for the activist_governance scoring profile's Dimension 3 (Activist Track Record, weighted ×1.5) and for U2 thesis composition (variant perception, conviction grade, sizing).

A 13D filed by Elliott Management is a different signal than the same 13D filed by an unknown LP. The skill quantifies the difference: campaign count, success rate, escalation cadence, sector concentration, and a tier benchmark. P3 produces a JSON sidecar that downstream skills (and the human reader) can consume, plus a human-readable markdown report citing every campaign with its primary-source URL.

It is invoked when:

- An EDGAR governance scanner surfaces a 13D from a filer whose track record is not already in the active book.
- Pedro asks for filer context ("what's Forager's record?", "who is Veradace?", "is this fund credible?").
- An existing activist dossier's Dimension 3 score is unjustified or stale (>180 d).
- The U2 thesis composer requires Dimension 3 evidence and the sidecar at the expected path is missing.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `filer_name_or_cik` | string | `Forager Fund, L.P.` or CIK `0001539281` | yes |
| `current_target_ticker` | string | `RPAY` | yes |
| `current_target_cik` | string | `1720592` | optional, used to verify the current campaign is in the campaign list |
| `lookback_years` | int | `15` | optional, default `15` (covers Dodd-Frank-era 13D evolution) |
| `output_dir` | path | `skills/research-activist-filer/outputs/` | optional, default to skill outputs/ |
| `offline` | bool | `false` | optional; when true the skill returns illustrative defaults (used by smoke tests when network unavailable) |
| `target_max_campaigns` | int | `30` | optional, hard cap on campaigns parsed (avoids pathological filers like passive index 13G filers misclassified as activists) |

If `filer_name_or_cik` is a CIK pattern (10-digit zero-padded, or numeric stripped), the skill skips name resolution. Otherwise it attempts to resolve the filer name → CIK via two paths in order:

1. EDGAR full-text search filtered by `forms=SC 13D` for the literal company name; harvest the unique CIK that filed the most 13Ds matching the name (typical pattern: a fund LP files via its general partner / investment adviser CIK).
2. EDGAR company-search index (`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=SC+13D`) HTML scrape fallback; parse the table for CIK matches.

Resolution confidence is recorded:
- 0.90+ if exact-match CIK found via EFTS with ≥3 prior 13D filings under that CIK and the filer name in `display_names`.
- 0.75 if EFTS surfaced multiple CIK candidates and the most-filing one was selected.
- 0.55 if HTML fallback was used.
- 0.30 if no CIK resolved — skill emits `{"status": "no_cik_resolved", ...}` and refuses to compute a track record (never guesses).

## Outputs

Atomic-written:

1. `skills/research-activist-filer/outputs/<filer_slug>_track_record.md` — human-readable report listing every prior campaign, outcome, return, and a tier-1 benchmark comparison.
2. `skills/research-activist-filer/outputs/<filer_slug>_campaigns.json` — structured sidecar consumed by U2 / scoring profile.

`<filer_slug>` is `re.sub(r"[^A-Za-z0-9]+", "_", filer_name).strip("_")` lowercased — e.g., `forager_fund_l_p` for `Forager Fund, L.P.`.

Final stdout JSON summary line:
```
{"status":"ok","filer":"Forager Fund, L.P.","cik":"1539281","prior_campaigns":N,"success_rate":0.42,"avg_position_pct":7.8,"avg_holding_days":540,"tier_classification":"emerging","confidence":0.78,"output_md":"...","output_json":"...","duration_s":T}
```

Failure exit codes are non-zero with structured stderr; the skill never silently swallows errors.

### Sidecar schema (`<filer>_campaigns.json`)

```json
{
  "filer_name": "Forager Fund, L.P.",
  "filer_cik": "1539281",
  "filer_aliases": ["Forager Capital Management, LLC", "Forager Fund LP"],
  "as_of": "2026-04-29T01:03:00Z",
  "lookback_years": 15,
  "current_target": {
    "ticker": "RPAY",
    "cik": "1720592",
    "company_name": "Repay Holdings Corporation",
    "in_campaign_list": true
  },

  "n_campaigns": 4,
  "n_distinct_targets": 4,
  "n_active": 1,
  "n_resolved": 3,
  "success_rate": 0.667,
  "success_rate_ci": [0.21, 0.94],
  "avg_position_pct": 7.8,
  "avg_holding_days": 540,
  "avg_amendments_per_campaign": 2.5,

  "sector_concentration": {
    "Technology": 2,
    "Consumer": 1,
    "Industrials": 1
  },

  "tier_classification": "emerging",
  "tier_benchmarks": {
    "tier_1_minimum_campaigns": 10,
    "tier_1_minimum_success_rate": 0.55,
    "this_filer_vs_tier_1": "below_threshold"
  },

  "campaigns": [
    {
      "campaign_id": "forager_RPAY_2025",
      "target_ticker": "RPAY",
      "target_cik": "1720592",
      "target_company_name": "Repay Holdings Corporation",
      "sector": "Technology",
      "first_13d_date": "2025-12-15",
      "first_13d_accession": "0001539281-25-000012",
      "first_13d_position_pct": 5.2,
      "latest_13da_date": "2026-04-17",
      "latest_13da_accession": "0001539281-26-000007",
      "latest_position_pct": 11.9,
      "n_amendments": 4,
      "amendment_cadence_days": 30,
      "outcome_status": "active",
      "outcome_event": "Tendered $4.80/sh all-cash proposal 2026-04-17; board engaged JPM/Sullivan & Cromwell to review",
      "outcome_event_date": "2026-04-17",
      "holding_days_to_outcome": null,
      "thesis_realized_return": null,
      "board_nomination_filing": null,
      "settlement_terms": null,
      "language_intensification": "high",
      "source_first": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001720592&type=SC+13D&dateb=&owner=include&count=40",
      "source_latest": "https://www.sec.gov/Archives/edgar/data/1720592/000165495426003460/primary_doc.xml",
      "confidence": 0.85
    }
  ],

  "international_extensions": {
    "uk_tr1_disclosures": [],
    "jp_meti_5pct_filings": [],
    "eu_transparency_notifications": []
  },

  "escalation_patterns": {
    "median_amendment_cadence_days": 35,
    "filings_with_board_nomination": 1,
    "filings_with_strategic_review_demand": 2,
    "language_intensity_trend": "increasing"
  },

  "confidence": 0.78,
  "source": "SEC EDGAR EFTS + EDGAR submissions API",
  "data_quality_notes": [
    "n_campaigns < 5: emerging-activist sample — success rate weakly anchored",
    "avg_holding_days computed only on n=2 resolved campaigns"
  ]
}
```

The activist_governance scoring profile reads `success_rate`, `n_campaigns`, and `tier_classification` to score Dimension 3 (Activist Track Record, ×1.5):
- Tier-1 (Elliott, Icahn, Starboard, ValueAct, Trian) → 5.
- Established (n_campaigns ≥ 3, success_rate ≥ 0.50) → 4.
- Emerging (n_campaigns 1–2 with credible PM background, OR n_campaigns ≥ 3 with success_rate < 0.50) → 3.
- First-time credible filer → 2.
- Unknown / no resolution → 1.

U2 reads `success_rate_ci`, `escalation_patterns.language_intensity_trend`, and `current_target.in_campaign_list` to assemble the variant-perception field and the conviction grade.

## Methodology

### Step 0 — HALT-flag check

Read `<reference>/02_System/engine/health/HALT_FLAG`. If present, log and exit with status `halted`. Reference folder is read-only.

### Step 1 — Resolve filer CIK

If input is a CIK pattern (10 digits zero-padded, or numeric ≤10 digits), accept directly and pad to 10 digits.

Otherwise:

1. **EFTS lookup**: query `https://efts.sec.gov/LATEST/search-index?q=%22<name>%22&forms=SC+13D` (URL-encoded). Parse `hits.hits[*]._source.ciks` (always a list of one element for 13D filers — the filer's CIK, not the issuer's). Aggregate filings per filer CIK; pick the CIK with the most matching filings AND with `display_names` containing the literal filer name. Record alternative CIKs in `filer_aliases` if the name appears across multiple related CIKs (typical: Fund LP + Investment Adviser GP).

2. **Company-search HTML fallback**: if EFTS returns 0 or ambiguous, fetch `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=SC+13D&dateb=&owner=include&count=40` and parse the result table. Lower confidence to 0.55.

If both fail, return `{"status": "no_cik_resolved", ...}` and exit 2.

### Step 2 — Pull all 13D / 13D/A filings by filer CIK

Query the EDGAR submissions API:
```
https://data.sec.gov/submissions/CIK<10-digit-padded>.json
```

Parse `filings.recent` and any `filings.files[*]` continuation files. Filter to `form in {"SC 13D", "SC 13D/A"}` and `filingDate >= now - lookback_years`. For each filing, capture: accession number, primary document URL, filing date, form, period of report, primary issuer (parsed from the filing index page or accession number → archives URL).

For older filings (>5y), also call:
```
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<padded>&type=SC+13D&dateb=&owner=include&count=400
```
Parse the HTML table for any 13D filings missing from the JSON submissions API.

For each 13D / 13D/A, follow the filing index link and parse the primary document for:
- Issuer CIK + name (header field)
- Reporting persons (filer + any aggregated names)
- Item 4 Purpose of Transaction (free text — used for language intensification)
- Item 5 Aggregate Beneficial Ownership (percent of class + share count)
- Whether the filing references board nomination, proxy contest, strategic review demand, sale demand, settlement / cooperation agreement

Best-effort parse: 13D primary docs are inconsistently formatted (HTML tables, plain text, XML schedules). Use a tiered parser:
1. **Structured XML schedule** (post-2009, common): parse the `<edgarSubmission>` schema for `subjectCompany`, `reportingOwner`, `aggregateAmount`, `percentOfClass`, `itemFourPurpose`.
2. **HTML primary document**: BeautifulSoup parse, extract tables for ownership rows, regex for Item 4 / Item 5 sections.
3. **Plain text fallback**: regex extraction of `\d+\.\d+%` near "percent of class" / "aggregate", and `Item 4` text block.

Record per-filing parse confidence (0.95 XML, 0.75 HTML, 0.55 text, 0.30 unparseable).

### Step 3 — Group filings into campaigns

Group by `(filer_cik, target_cik)`. Each group is one campaign:
- `first_13d_date` = earliest filing date with form `SC 13D` (the original campaign opener).
- `latest_13da_date` = latest filing in the group (any 13D/A; or the original 13D if no amendments).
- `n_amendments` = count of 13D/A filings in the group (excluding the original 13D).
- `amendment_cadence_days` = median day-difference between consecutive filings.
- `position_pct` time-series = chronological list of (date, percent) from each parsed filing.
- `first_13d_position_pct` = first parsed percent.
- `latest_position_pct` = last parsed percent.

If the same filer files a 13G first and converts to 13D later, the conversion date is treated as the campaign start (13D = activist intent declared) and the prior 13G is annotated in the campaign record but not counted as the campaign opener.

### Step 4 — Resolve outcome per campaign

For each campaign, classify the outcome by querying the *target's* EDGAR submissions API for filings ≥ `first_13d_date`:

| Outcome | Detection rule | Confidence |
|---|---|---|
| **Sale / merger closed** | DEFM14A or 8-K Item 2.01 (Completion of Acquisition) by target with effective date | 0.90 (with definitive agreement URL) |
| **Topping bid emerged** | Multiple SC TO-T filings or 8-K mentioning competing offer | 0.80 |
| **Settlement (board seats)** | 8-K mentioning "Cooperation Agreement", "settlement agreement", "board representation" + filer name | 0.85 (when filer name matches in the 8-K body); 0.65 when only inferred |
| **Settlement (standstill only, no seats)** | 8-K with "standstill" but no board-seat appointment 8-K Item 5.02 | 0.70 |
| **Proxy fight won** | DEF 14A vote results showing filer's nominees elected; or 8-K Item 5.07 with vote tally favoring filer slate | 0.85 |
| **Proxy fight lost** | DEF 14A vote results favoring management slate after a contested PRREN14A / DEFC14A | 0.85 |
| **Withdrawal** | 13D/A drops below 5% threshold OR converts to 13G with no governance outcome | 0.75 |
| **Active** | None of the above; latest amendment within last 365d | 0.70 |
| **Stale-active** | None of the above; no filings in last 365d AND latest position ≥ 5% | 0.50 — flag for manual review |

For each outcome, capture: outcome_event (free text), outcome_event_date, source URL (primary filing). When in doubt, assign `outcome_status = "manual_review"` with confidence 0.50 and `outcome_event = "ambiguous outcome — manual review"`. Never guess.

### Step 5 — Resolve target ticker + sector

For each unique target CIK in the campaign list, query:
```
https://data.sec.gov/submissions/CIK<padded>.json
```

Parse `tickers[0]` (most recent ticker), `name` (most recent company name), `sicDescription` (sector mapping per SIC 4-digit code). Pre-built SIC → high-level-sector table is in `helpers/sic_sector_map.py` (Technology, Healthcare, Financials, Consumer, Industrials, Energy, Materials, Utilities, Real Estate, Communication).

If the target is delisted / merged: ticker may be missing; record as `"_delisted"` and use `name` only.

### Step 6 — Compute aggregate metrics

- `success_rate` = `n_successes / n_resolved`. A "success" is `outcome_status in {sale_merger_closed, settlement_board_seats, proxy_fight_won, topping_bid_emerged}`. A "non-success" is `withdrawal`, `proxy_fight_lost`, `settlement_standstill_only`. `active`, `stale_active`, and `manual_review` are excluded from the denominator.
- `success_rate_ci` = Wilson 95% CI on `(n_successes, n_resolved)`. When `n_resolved < 5`, the CI will be very wide; flag as `data_quality_notes: ["sparse track record — n_resolved < 5"]`.
- `avg_position_pct` = mean of `latest_position_pct` across all campaigns.
- `avg_holding_days` = mean of (outcome_event_date − first_13d_date) in days, over resolved campaigns only.
- `avg_amendments_per_campaign` = mean of `n_amendments`.
- `sector_concentration` = `Counter(sector for campaign in campaigns)`.

### Step 7 — Tier classification + benchmark comparison

Hardcoded tier-1 benchmark table in `helpers/tier1_benchmark_data.py`:

| Filer | Approx campaigns | Approx success rate | Tier |
|---|---|---|---|
| Elliott Investment Management | 200+ | 0.65 | 1 |
| Carl Icahn / Icahn Enterprises | 150+ | 0.55 | 1 |
| Starboard Value | 100+ | 0.60 | 1 |
| ValueAct Capital | 60+ | 0.55 | 1 |
| Trian Fund Management | 30+ | 0.55 | 1 |
| Pershing Square | 25+ | 0.50 | 1 |
| Jana Partners | 50+ | 0.55 | 1 |
| Engaged Capital | 30+ | 0.55 | 1 |
| Blue Harbour | 25+ | 0.55 | 1 (legacy / wound down) |
| Cevian Capital | 30+ | 0.50 | 1 (Europe focus) |

Classification rules (in order):

1. **Tier-1 named** — exact match on filer_aliases vs. the tier-1 reference list (case-insensitive, punctuation-stripped) → `tier_classification = "tier_1"`.
2. **Established** — `n_campaigns ≥ 5 AND success_rate ≥ 0.50` → `"established"`.
3. **Emerging** — `n_campaigns ≥ 1 AND (n_campaigns < 5 OR success_rate < 0.50)` → `"emerging"`.
4. **First-time** — `n_campaigns == 1 AND that 1 is the current target` → `"first_time"`.
5. **Unknown** — `n_campaigns == 0` after exhaustive resolution → `"unknown"`.

`tier_benchmarks.this_filer_vs_tier_1` reports `"at_or_above_threshold"` if both `n_campaigns ≥ tier_1_minimum_campaigns` AND `success_rate ≥ tier_1_minimum_success_rate`; otherwise `"below_threshold"`.

### Step 8 — International extensions (best-effort)

For non-US filers (or any filer the user requests), additionally query:

- **UK FCA TR-1** (Form 8.3 / TR-1 disclosures): `https://api.fca.org.uk/...` — requires API setup; if unavailable, mark `uk_tr1_disclosures: []` with `data_quality_notes: ["uk_fca_unavailable"]`.
- **Japan METI 5%-rule**: 大量保有報告書 filings — accessible via EDINET (https://disclosure.edinet-fsa.go.jp/api/...). If filer is a Japan-domiciled fund, attempt this query; otherwise skip.
- **EU Transparency Directive**: each member state operates its own venue (BaFin Germany, AMF France, CONSOB Italy, etc.). Skill maintains a per-country base URL table; queries one per country only when filer name suggests EU domicile or current_target is an EU-listed company.

When international extensions are unavailable (most US-domiciled filer cases), emit `international_extensions: {"uk_tr1_disclosures": [], "jp_meti_5pct_filings": [], "eu_transparency_notifications": []}` with `data_quality_notes` flagging "international_extensions_not_queried". Do not crash on missing API tokens; degrade gracefully.

### Step 9 — Escalation patterns

For each campaign, compute:
- `amendment_cadence_days` median (computed in Step 3).
- `filings_with_board_nomination` = count of filings where Item 4 contains "board representation", "director nomination", "PRREN14A", "DEFC14A", or any reference to nominee-list filings.
- `filings_with_strategic_review_demand` = count where Item 4 contains "strategic alternatives", "explore sale", "sale process", "strategic review", "explore options".
- `language_intensity_trend` = compare token-overlap of the *first* Item 4 vs. the *latest* Item 4 across all amendments. If the latest contains substantially more demand-words ("require", "must", "demand", "tender", "remove", "replace") than the first, mark `"increasing"`. Else `"flat"` or `"decreasing"`.

This is best-effort heuristic — confidence on the trend label ≤ 0.65.

### Step 10 — Atomic-write outputs

Write the JSON sidecar and markdown report through `atomic_write_text` (temp + rename) to avoid corrupting existing files mid-write. Use the same helper module shared with P1/P2/U4.

Write order: JSON first, then markdown. If JSON write fails, abort before markdown is touched (so consumers see missing-sidecar rather than stale-mismatch state).

## Profile-specific application

This skill is `activist_governance` only. It is not invoked for `merger_arb` (use P4 instead — research-acquirer-history), `binary_catalyst`, `litigation`, or `insider`. Profile dispatch is the caller's responsibility (U2 thesis composer, scanner-triage layer, or human invocation).

## Output schema

See **Outputs → Sidecar schema** above for JSON. The markdown report follows this skeleton:

```
# Activist Track Record — <filer_name>

**Filer CIK:** <cik>
**Aliases:** <comma list>
**As of:** <ISO timestamp>
**Lookback:** <years>y
**Current target under review:** <ticker> (<company>) — in campaign list: <yes/no>

## Headline metrics

| Metric | Value | n | CI95 | Source |
|---|---|---|---|---|
| Prior campaigns | 4 | – | – | EDGAR submissions API |
| Distinct targets | 4 | – | – | EDGAR |
| Active campaigns | 1 | – | – | EDGAR |
| Resolved campaigns | 3 | – | – | EDGAR |
| Success rate | 67% | 2/3 | 21%–94% | derived |
| Avg position % at filing | 7.8% | – | – | parsed 13D |
| Avg holding days to outcome | 540 | – | – | derived |
| Avg amendments per campaign | 2.5 | – | – | EDGAR |

## Tier classification

**<tier_classification>** — <free-text rationale>

Versus tier-1 benchmark (Elliott / Icahn / Starboard / ValueAct / Trian / Pershing / Jana / Engaged):
- Tier-1 minimum campaigns: 10 → this filer: <n>
- Tier-1 minimum success rate: 55% → this filer: <pct>
- Verdict: <at_or_above_threshold | below_threshold>

## Campaigns

### Campaign 1 — <target_ticker> (<target_company>)

- Sector: <sector>
- First 13D: <date> — <position_pct>% (accession <acc>, [link](<url>))
- Latest 13D/A: <date> — <position_pct>% (<n_amendments> amendments, cadence <days>d, [link](<url>))
- Outcome: <outcome_status> — <outcome_event> (<outcome_event_date>)
- Holding days to outcome: <days>
- Realized return: <pct or n/a>
- Board nomination filing: <yes/no>
- Settlement terms: <text or none>
- Language intensification: <high/medium/low>
- Confidence: <0.0-1.0>

(repeat for each campaign)

## Sector concentration

| Sector | Campaigns |
|---|---|
| Technology | 2 |
| Consumer | 1 |
| Industrials | 1 |

## Escalation patterns (this filer)

- Median amendment cadence: <days>
- Filings with board nomination: <n>
- Filings with strategic review demand: <n>
- Language intensity trend: <increasing/flat/decreasing>

## International extensions

- UK FCA TR-1: <list or none>
- JP METI 5%-rule: <list or none>
- EU Transparency Directive: <list or none>

## Data-quality notes

- <each entry from data_quality_notes>

## Sources

- Filer submissions API: <url>
- Per-campaign primary docs: <list>
- Tier-1 benchmark reference: helpers/tier1_benchmark_data.py

---

*Skill: research-activist-filer.*
```

## Worked example — Forager Fund, L.P. on RPAY

Input:
- `filer_name_or_cik = "Forager Fund, L.P."`
- `current_target_ticker = "RPAY"`
- `current_target_cik = "1720592"`

Step 1 — CIK resolution: EFTS search for `"Forager Fund, L.P."` filtered by `forms=SC 13D`. Resolves CIK `1539281` (Forager Capital Management LLC files via this CIK; the LP and the GP share it for filing purposes). Confidence 0.85. Aliases recorded: `["Forager Fund, L.P.", "Forager Capital Management, LLC"]`.

Step 2 — Pull 13D / 13D/A filings: query `https://data.sec.gov/submissions/CIK0001539281.json`. Returns all filings; filter to `SC 13D`/`SC 13D/A` in last 15 years. Suppose returns 12 filings across 4 distinct targets (illustrative — actual count produced live in smoke test):

- RPAY (issuer CIK 1720592): SC 13D 2025-12-15, 13D/A x4 through 2026-04-17.
- Two prior targets in 2018 / 2021 (resolved campaigns).
- One target in 2023 (active or stale).

Step 3 — Group into 4 campaigns by target CIK.

Step 4 — Outcome resolution per campaign:
- Campaign A (small-cap industrials, 2018): 8-K 2019 announcing sale to strategic acquirer → `sale_merger_closed`, holding 410 days, success.
- Campaign B (consumer name, 2021): 13D/A in 2022 dropped to 4.9% → `withdrawal`, holding 280 days, non-success.
- Campaign C (technology, 2023): 8-K Item 5.02 board-seat appointment naming a Forager-aligned director → `settlement_board_seats`, holding 420 days, success.
- Campaign D (RPAY, 2025): live — `active`, see Forager $4.80/sh proposal Apr 2026.

Step 5 — Sector tagging via SIC.

Step 6 — Aggregate:
- n_campaigns = 4, n_resolved = 3, n_successes = 2, success_rate = 0.667 (Wilson CI 0.21–0.94).
- avg_position_pct ≈ 9.1% (4 campaigns).
- avg_holding_days = 370 (across 3 resolved).

Step 7 — Tier classification: name not in tier-1 list. `n_campaigns = 4` (just below 5) and `success_rate = 0.667 ≥ 0.50` — borderline established. Apply Step 7 rules: "Emerging" (because `n_campaigns < 5`). Verdict vs tier-1: `below_threshold` on campaign count.

Step 8 — International extensions: filer is US-domiciled; skip UK/JP/EU queries. Emit empty arrays with note.

Step 9 — Escalation: RPAY campaign shows 4 amendments in ~120 days, language intensifying from "discussions with management" to "tender offer at $4.80/share" — language_intensity_trend = `"increasing"`. Other campaigns moderate.

Step 10 — Atomic-write outputs. Final JSON contains all four campaigns with primary-source URLs; markdown report human-readable; structured sidecar consumed by U2 (Activist Track Record dimension scoring rationale).

Sidecar `confidence` ≈ 0.78 (n=4 campaigns < tier-1 threshold but resolution quality high; CI wide but each campaign well-sourced). The activist_governance scoring profile reading this sidecar would score Dimension 3 = 3 (Emerging with 1–2 known successes), aligned with the RPAY dossier's qualitative read of Forager as "credible, focused, multi-name fund."

## Failure modes and recovery

| Failure mode | Detection | Skill response |
|---|---|---|
| EDGAR submissions API returns 5xx | HTTP status | Retry 3x with exponential backoff; if all fail, write `data_quality_notes: ["edgar_submissions_unavailable"]`, set sidecar confidence ≤ 0.40, return `{"status": "degraded", ...}` |
| EDGAR EFTS rate-limited (HTTP 429) | status code | Exponential backoff up to 60s; if still throttled, fall back to per-CIK browse-edgar HTML; mark `confidence ≤ 0.55` |
| 13D primary document unparseable | XML parse fail + HTML parse fail + plain-text regex empty | Mark that filing's `position_pct = null` with `parse_confidence: 0.30`; do NOT drop the filing — record presence with reduced confidence |
| Filer name resolves to multiple CIKs | EFTS returns multiple `display_names` | Pick the one with the most 13D filings; record alternatives in `filer_aliases`; lower confidence to 0.70 |
| Filer name resolves to 0 CIKs | Both EFTS and HTML return empty | Return `{"status": "no_cik_resolved", "filer_name": "...", "confidence": 0.0}`, exit 2 |
| Target outcome ambiguous | Multiple competing classifiers fire | Mark `outcome_status = "manual_review"`, `outcome_event = "ambiguous — multiple signals"`, confidence 0.50 |
| n_campaigns == 0 (exhaustive but empty) | Step 2 + Step 3 yield 0 | Return sidecar with `n_campaigns: 0`, `tier_classification: "first_time"` IF `current_target_cik` is in campaign list when written by an external upstream (i.e., the activist signal at hand is the filer's first 13D); else `"unknown"`. Set confidence 0.50 |
| International API token absent | env-var lookup fails | Emit empty `international_extensions` with `data_quality_notes: ["international_extensions_unavailable"]`; do NOT crash |
| HALT_FLAG present | check `02_System/engine/health/HALT_FLAG` at startup (read-only) | Log and exit immediately with status `halted` |
| Network-disabled environment (offline mode) | `--offline` flag | Use illustrative defaults from worked example, set confidence 0.30, mark `data_quality_notes: ["offline mode — values illustrative"]` |
| Tier-1 name overlap (false-positive match) | filer name e.g., "Elliott Capital LLC" not = "Elliott Investment Management" | Strict full-name match required; partial matches do NOT promote to tier_1; lower-confidence "name_collision" annotation in `data_quality_notes` |

No silent failures. Every degraded path produces a `data_quality_notes` entry and a lowered confidence.

## Compliance with system invariants

- **Atomic writes** (temp file + rename via `atomic_write_text`) per D-052 — both JSON sidecar and markdown report.
- **Confidence + source on every output row** — every campaign, outcome event, sponsor history item, and the headline aggregate carry `confidence` (0.0–1.0) and `source` (URL or file path) per CLAUDE.md §1.6.
- **Append-only**: this skill writes to its own `outputs/` directory and never mutates ledgers in `02_System/engine/`. Folding filer track-record events into `historical_events_ledger.json` is M1's job, not P3's.
- **Reference folder is read-only** — the skill reads from `Investment tool backup/` (HALT_FLAG, profile_activist_governance.md, dossiers) but writes only to `Investment tool backup skills/skills/research-activist-filer/outputs/`.
- **Bounded runtime** — typical run ≤ 60 s with full network access; offline mode ≤ 5 s.
- **Resumable** — re-running the skill on the same filer overwrites the prior output atomically; no partial-state leakage.
- **No invented data** — when a number is not in a primary source, the field is null with confidence 0.0 and a `data_quality_notes` entry; never guess.
- **Numeric tickers rendered with company names** — outputs always render tickers with company names per CLAUDE.md §1.7 / `feedback_ticker_company_names.md`. e.g., `RPAY (Repay Holdings Corporation)`.
- **EDGAR User-Agent compliance** — every HTTP request includes `User-Agent: investment-tool-research-activist-filer/1.0 javiergorordo13@hotmail.com` per SEC EDGAR fair-access policy.

## Helpers (in `helpers/`)

| Script | Purpose |
|---|---|
| `atomic_write.py` | Atomic write helper (temp file + rename). Re-imported pattern from monitor-kill-conditions/helpers. |
| `edgar_filer_history.py` | EDGAR EFTS + submissions-API wrapper that resolves filer CIK and pulls all SC 13D / SC 13D/A filings by that CIK with rate-limit backoff. |
| `campaign_outcome_resolver.py` | Per-campaign outcome classifier — queries target-issuer EDGAR submissions for follow-up filings (DEFM14A, 8-K Item 2.01 / 5.02 / 5.07, withdrawal patterns) and applies the outcome decision tree. |
| `tier1_benchmark_data.py` | Hardcoded tier-1 activist reference table (Elliott, Icahn, Starboard, ValueAct, Trian, Pershing, Jana, Engaged, Blue Harbour, Cevian) with approximate campaign counts and success rates for benchmark comparison. Includes case-insensitive alias matcher. |
| `sic_sector_map.py` | SIC 4-digit → high-level sector mapping (Technology / Healthcare / Financials / Consumer / Industrials / Energy / Materials / Utilities / Real Estate / Communication). |
| `analyze.py` | Orchestrator. Calls the helpers above in sequence, synthesizes the JSON sidecar and markdown report, atomically writes outputs, prints structured stdout summary. |

All helpers obey: graceful network-error handling (return structured `{"ok": false, "reason": ...}` rather than crashing), atomic writes through the shared `atomic_write_text`, no global state.

## Invocation

CLI:
```
python helpers/analyze.py \
  --filer "Forager Fund, L.P." \
  --target-ticker RPAY \
  --target-cik 1720592 \
  --output-dir <working>/skills/research-activist-filer/outputs
```

For an offline smoke test (no network):
```
python helpers/analyze.py \
  --filer "Forager Fund, L.P." \
  --target-ticker RPAY \
  --target-cik 1720592 \
  --filer-cik 1539281 \
  --offline \
  --output-dir <working>/skills/research-activist-filer/outputs
```

The U2 thesis composer and activist_governance scanner-triage layer automatically pick up the resulting `<filer_slug>_campaigns.json` if it exists in the expected sibling outputs directory; no further wiring needed.

---

*Skill: research-activist-filer. Built per Phase 2 of the autonomous skill-build plan, ratified 2026-04-29.*
