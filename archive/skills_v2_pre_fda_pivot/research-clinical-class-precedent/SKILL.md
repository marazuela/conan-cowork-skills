---
name: research-clinical-class-precedent
description: Build a class-precedent reference set for an FDA decision. For a given mechanism of action and indication, this skill enumerates prior FDA outcomes (approvals, CRLs, withdrawals) for drugs in the same therapeutic and pharmacological class over the last ~10 years; computes class-level base rates (approval rate, AdCom convene rate, boxed-warning rate, time-from-NDA-to-decision); pulls the sponsor's own prior FDA history (CRLs received, breakthrough/priority designations, RTOR participation, ongoing inspection signals); and writes a compact JSON sidecar that downstream skills (notably analyze-fda-approval-prospects / P1) can read. Triggers when user asks "what's the FDA approval rate for drugs in this class", "find prior approvals for [MoA]", "is this similar to a class FDA already cleared", "compute class base rates for", "research class precedent for [drug]", or as a sub-step of a binary_catalyst dossier refresh. Operates against openFDA (api.fda.gov/drug/drugsfda.json) for approval history, the Federal Register API for AdCom announcements, ChEMBL for mechanism normalization, and EDGAR EFTS for company FDA disclosures. Produces a class-precedent markdown report plus the structured JSON sidecar consumed by P1.
type: skill
---

# research-clinical-class-precedent

## Purpose

Anchor a binary_catalyst probability estimate to *real FDA outcomes for drugs in the same class*, rather than to a generic industry-wide base rate. The skill is the principal upstream feeder for `analyze-fda-approval-prospects` (P1). When P2 has run and a sidecar JSON exists at the expected path, P1 picks it up automatically and replaces the conservative default anchor (~62% small-molecule NDA full-approval) with a *class-specific* anchor backed by an enumerated precedent list.

The skill also surfaces the sponsor's own FDA fingerprint — prior CRLs, breakthrough designations, priority-review history — which P1's `probability_synthesizer` uses as a sponsor-history modifier (+/- pp).

It is invoked when:

- A binary_catalyst candidate is being deep-dived and class precedent has not yet been computed for that drug.
- Pedro asks for class context ("what's the FDA's track record on NMDA antagonists for MDD?").
- An existing dossier's class-precedent section is older than 90 days or pre-dates a material precedent decision.
- P1 is about to run and the expected sidecar (`<drug>_class_basrates.json`) is missing.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `drug_name` | string | `AXS-05` | yes |
| `mechanism_of_action` | string | `NMDA receptor antagonist + CYP2D6 inhibitor` | yes |
| `indication` | string | `Major Depressive Disorder` | yes |
| `company_ticker` | string | `AXSM` | yes |
| `cik` | string (CIK leading zeros stripped) | `1579428` | recommended for EDGAR pulls |
| `company_name` | string | `Axsome Therapeutics` | optional, used as EDGAR EFTS query when CIK absent |
| `class_drugs` | list[string] | `["dextromethorphan", "ketamine", "esketamine"]` | optional; if provided, narrows openFDA queries; else inferred from MoA |
| `lookback_years` | int | `10` | optional, default `10` |
| `output_dir` | path | `skills/research-clinical-class-precedent/outputs/` | no, default to skill outputs/ |
| `offline` | bool | `false` | optional; when true skill returns illustrative defaults (used by smoke tests when network unavailable) |

If `class_drugs` is not provided, the skill attempts to infer the class membership by:
1. Splitting `mechanism_of_action` on common separators (`+`, `/`, `,`).
2. For each MoA fragment, looking up the canonical class via ChEMBL `target_search` (when bio-research MCP is available) or a hardcoded fallback table for common classes (NMDA antagonists, JAK inhibitors, GLP-1 agonists, anti-VEGF, anti-amyloid mAbs, anti-FXIa, etc.).
3. Falls back to the literal MoA string for openFDA full-text searches.

Class membership inference is recorded with `confidence < 0.7` to flag downstream that it is the skill's best guess, not a curated list.

## Outputs

Atomic-written:

1. `skills/research-clinical-class-precedent/outputs/<drug_slug>_class_precedent.md` — human-readable report listing approvals, CRLs, AdCom outcomes, label patterns, and sponsor history.
2. `skills/research-clinical-class-precedent/outputs/<drug_slug>_class_basrates.json` — structured sidecar consumed by P1.

`<drug_slug>` is `drug_name.split()[0].replace("/", "_")` to match P1's lookup pattern.

Final stdout JSON summary line:
```
{"status":"ok","drug":"AXS-05","class":"NMDA antagonist + CYP2D6 inhibitor","n_approvals":N,"n_crls":M,"approval_rate_class":0.62,"adcom_rate":0.10,"boxed_warning_rate":0.20,"confidence":0.72,"output_md":"...","output_json":"...","duration_s":T}
```

Failure exit codes are non-zero with structured stderr; the skill never silently swallows errors.

### Sidecar schema (`<drug>_class_basrates.json`)

```json
{
  "drug": "AXS-05",
  "indication": "Major Depressive Disorder",
  "ticker": "AXSM",
  "mechanism_of_action": "NMDA receptor antagonist + CYP2D6 inhibitor",
  "class_label": "NMDA antagonist (depression / mood)",
  "as_of": "2026-04-29T00:00:00Z",
  "lookback_years": 10,

  "approval_rate_class": 0.62,
  "approval_rate_class_ci": [0.45, 0.79],
  "adcom_rate": 0.10,
  "boxed_warning_rate": 0.20,
  "rems_rate": 0.15,
  "median_review_days": 305,

  "n_approvals": 7,
  "n_crls": 4,
  "n_withdrawals": 1,
  "n_total_in_class": 12,

  "approvals": [
    {
      "drug": "esketamine",
      "brand": "Spravato",
      "sponsor": "Janssen",
      "approval_date": "2019-03-05",
      "indication": "Treatment-resistant depression",
      "boxed_warning": true,
      "rems": true,
      "adcom_held": true,
      "adcom_vote": "14-2",
      "review_days": 244,
      "designation": ["Breakthrough", "Priority Review"],
      "source": "https://api.fda.gov/drug/drugsfda.json?search=...",
      "confidence": 0.95
    }
  ],

  "crls": [
    {
      "drug": "<drug>",
      "sponsor": "<sponsor>",
      "crl_date": "YYYY-MM-DD",
      "indication": "<indication>",
      "publicly_disclosed_grounds": ["CMC", "efficacy"],
      "subsequent_outcome": "approved on resubmission | withdrawn | still pending",
      "source": "<URL>",
      "confidence": 0.85
    }
  ],

  "sponsor_history": {
    "ticker": "AXSM",
    "cik": "1579428",
    "prior_approvals": [
      {"drug": "AXS-05", "indication": "MDD", "approval_date": "2022-08-19", "source": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=211078"}
    ],
    "prior_crls_received": [],
    "prior_crl_same_indication": false,
    "breakthrough_designation": false,
    "priority_review": false,
    "rtor_participation": false,
    "ongoing_inspection_concerns": false,
    "source": "https://efts.sec.gov/LATEST/search-index?q=%22complete+response+letter%22&forms=8-K&ciks=0001579428"
  },

  "confidence": 0.72,
  "source": "openFDA + Federal Register + EDGAR EFTS",
  "data_quality_notes": [
    "class_membership_inference: heuristic_from_moa",
    "n_total_in_class < 5: sparse class — base rate weakly anchored"
  ]
}
```

P1's `probability_synthesizer.synthesize()` reads `approval_rate_class` (used as anchor), `confidence` (used as `anchor_conf`), `source` (used in the ledger), and `sponsor_history.{prior_crl_same_indication, breakthrough_designation, priority_review}`. The skill MUST produce all of those fields, even if some are empty / `false` / null (with confidence lowered accordingly).

## Methodology

### Step 1 — Resolve class membership

The class is the unit of analysis. Bad class definition → garbage base rate.

1. **MoA splitting**: Split `mechanism_of_action` on `+`, `/`, `,`, ` and `, treating each fragment as a candidate MoA. Most drugs fall in a single class; AXS-05 is unusual in being a fixed-dose combo.
2. **Canonicalize via ChEMBL**: For each fragment, call `target_search` (bio-research MCP) to retrieve the canonical mechanism class label and target gene/protein. Cache responses to avoid re-querying.
3. **Indication overlay**: Class outcome rates differ markedly by indication. For example, NMDA antagonist approval rate in pain/anesthesia vs. depression/mood is very different. Always annotate the class label with the indication context: `"NMDA antagonist (depression / mood)"`, not bare `"NMDA antagonist"`.
4. **Class drug list**: If `class_drugs` provided, use that. Otherwise, query openFDA via `pharm_class_epc` and `openfda.pharm_class_moa` fields with the MoA string. Augment via PubMed class review search when available. Manual class lists for the active book (NMDA antagonists, JAK inhibitors, GLP-1 agonists, anti-amyloid mAbs, anti-FXIa, anti-VEGF, anti-IL-23/p19, IGF-1R inhibitors, complement C5) are stored in `helpers/class_atlas.py` as a fallback.

Confidence on the resolved class:
- 0.90+ if `class_drugs` was explicitly provided.
- 0.75 if openFDA `pharm_class_moa` returned ≥ 5 drugs that match.
- 0.60 if hardcoded class atlas fallback was used.
- 0.40 if only literal MoA string match — flag as `class_membership_inference: weak`.

### Step 2 — Pull approvals (last `lookback_years`)

Primary source: openFDA `api.fda.gov/drug/drugsfda.json`.

For each class drug, query:
```
https://api.fda.gov/drug/drugsfda.json?search=<query>&limit=100
```

Where `<query>` is one of:
- `openfda.generic_name:"<drug>"` (preferred)
- `openfda.brand_name:"<brand>"`
- `openfda.pharm_class_moa:"<moa>"`
- `openfda.substance_name:"<active_ingredient>"`

For each result, extract:
- Application number (NDA/BLA #)
- Sponsor (`sponsor_name`)
- Submission type (`submission_type`: ORIG-N where N is supplement number; ORIG-1 is the first NDA)
- Submission status (`submission_status`: AP = approved, TA = tentative approval, withdrawn, etc.)
- Action date (`submission_status_date`)
- Indication / generic name / dosage form
- Whether the application was an NCE (new chemical entity) vs. a 505(b)(2) reformulation

Filter to: `submission_type == "ORIG-1"` AND `submission_status == "AP"` AND date within `lookback_years`. This is the canonical "first approval" signal — ignores subsequent supplements.

### Step 3 — Pull CRLs / withdrawals

CRLs are not directly in openFDA. Use a layered approach:

1. **EDGAR EFTS full-text search** for 8-K filings mentioning "Complete Response Letter" by sponsors of class drugs. URL pattern:
   `https://efts.sec.gov/LATEST/search-index?q=%22Complete+Response+Letter%22+%22<drug>%22&forms=8-K&dateRange=custom&startdt=YYYY-MM-DD&enddt=YYYY-MM-DD`
   Parse the resulting JSON for filings; for each hit, capture sponsor CIK, filing date, accession number, and a link to the 8-K. The 8-K body usually contains the date the CRL was received and a high-level disclosure of grounds (CMC, efficacy, safety).
2. **FDA press releases / drug safety announcements** when CRL was material enough to be announced publicly.
3. **Subsequent-approval cross-reference**: drugs that received a CRL and were approved later show up in openFDA as `submission_type == ORIG-1` with `submission_status == AP` and a later date than the CRL — record both events in the timeline.

For each CRL, capture: drug, sponsor, CRL date, indication, publicly disclosed grounds, subsequent outcome (approved on resubmission / withdrawn / still pending), source URL, confidence (typically 0.8 from EDGAR 8-K disclosure, 0.95 from FDA direct).

### Step 4 — AdCom rate per division

AdCom convening is division-specific; depression / mood is reviewed by the Division of Psychiatry within OND.

1. Query the Federal Register API:
   `https://www.federalregister.gov/api/v1/documents.json?conditions[term]=<class+OR+division>+advisory+committee&conditions[publication_date][gte]=YYYY-MM-DD&conditions[publication_date][lte]=YYYY-MM-DD&per_page=100`
2. For each AdCom notice in the lookback window for the relevant division, capture: meeting date, drug discussed, indication, vote outcome (when reported).
3. Compute AdCom rate: `n_class_drugs_with_adcom / n_class_drugs_reviewed_by_division_in_period`.

If Federal Register is unreachable, fall back to a sentinel value (industry-wide AdCom rate ≈ 0.10–0.15) and mark `confidence: 0.40` with `source: "default_industry_rate"`.

### Step 5 — Label patterns

For each approved class drug, parse openFDA `openfda.pharm_class_*` and `boxed_warning` fields, and where available the FDA-published label PDF (`https://www.accessdata.fda.gov/drugsatfda_docs/label/<year>/<appl>.pdf`).

Capture: boxed warning (yes/no, classes), REMS (yes/no, components), indication restriction (full / restricted / second-line), post-marketing requirement (yes/no, study type).

Compute:
- `boxed_warning_rate`: fraction of class approvals with a boxed warning at first label.
- `rems_rate`: fraction with REMS.
- `restricted_indication_rate`: fraction whose initial label was narrower than the sponsor's NDA-requested indication (read against the FDA briefing doc when available, else inferred from press-release wording).

### Step 6 — Time-from-NDA-to-decision

For each approval and CRL, compute days from submission to action: `submission_status_date - submission_date`.

Median across class is the headline number. P25 and P75 quartiles are also recorded as the spread reflects review-pathway heterogeneity (Priority Review, RTOR, Standard).

### Step 7 — Sponsor-specific FDA history

Query EDGAR EFTS by CIK for the sponsor. Search:
- `q="Complete Response Letter"&forms=8-K&ciks=<cik>` → prior CRLs received
- `q="Breakthrough Therapy designation"&forms=8-K&ciks=<cik>` → BTD events
- `q="Priority Review"&forms=8-K&ciks=<cik>` → priority-review events
- `q="Real-Time Oncology Review"&forms=8-K&ciks=<cik>` → RTOR (oncology-specific)
- `q="Form 483"&forms=8-K,10-K,10-Q&ciks=<cik>` → ongoing inspection concerns

For each hit, capture form, accession, filing date, brief excerpt, source URL.

Aggregate into `sponsor_history` JSON object as per the schema.

### Step 8 — Synthesize approval rate with CI

Class approval rate = `n_approvals / (n_approvals + n_crls + n_withdrawals)` over the lookback window, where the denominator counts **decided** events only — ongoing review applications are excluded.

Confidence interval via Wilson score interval at 95% confidence. When the denominator is small (n < 10), the CI is wide and the skill flags the result as a "sparse class" with reduced anchor confidence.

Confidence rules:
- Anchor `confidence` ≥ 0.85 when n_total_in_class ≥ 10, class_membership_confidence ≥ 0.75, AdCom rate sourced from Federal Register, boxed warning rate sourced from openFDA labels.
- Anchor `confidence` 0.65–0.75 when n_total_in_class 5–9 OR class_membership inferred from atlas.
- Anchor `confidence` 0.50–0.60 when n_total_in_class < 5 OR Federal Register fallback was used.
- Anchor `confidence` < 0.50 when offline mode or any major data source returned no results.

### Step 9 — Atomic-write outputs

Write the JSON sidecar and the markdown report through `atomic_write_text` (temp + rename) to avoid corrupting existing files mid-write. Use the same helper module shared with P1 / U4.

Write order: JSON first, then markdown. If JSON write fails, abort before the markdown is touched (so the consumer sees a missing-sidecar rather than a stale-mismatch state).

## Profile-specific application

This skill is `binary_catalyst` only. It is not invoked for `merger_arb`, `activist_governance`, `litigation`, or `insider` candidates. Profile dispatch is the caller's responsibility (P1 or the dossier composer).

## Output schema

See the **Outputs → Sidecar schema** section above for the JSON. The markdown report follows this skeleton:

```
# Class Precedent — <drug> for <indication>

**Mechanism:** <MoA>
**Class label:** <resolved class>
**Lookback:** <years>y
**As of:** <ISO timestamp>

## Headline base rates

| Metric | Value | n | CI95 | Source |
|---|---|---|---|---|
| Class approval rate | 62% | 7 / 11 | 33%–85% | openFDA |
| AdCom convene rate | 18% | 2 / 11 | – | Federal Register |
| Boxed warning rate | 14% | 1 / 7 | – | openFDA labels |
| REMS rate | 14% | 1 / 7 | – | openFDA labels |
| Median review days | 305 | – | 244–366 (IQR) | openFDA |

## Approvals (n=7)

| Drug | Brand | Sponsor | Date | Indication | Boxed | REMS | AdCom | Vote | Days | Designation | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

## CRLs (n=3)

| Drug | Sponsor | CRL date | Indication | Grounds | Subsequent | Source |
|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... |

## Withdrawals / Failed (n=1)

| Drug | Sponsor | Date | Reason | Source |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## Sponsor FDA history — <ticker>

- Prior approvals: <list>
- Prior CRLs received: <list>
- Breakthrough designations: <list>
- Priority review participations: <list>
- RTOR participation: <bool + source>
- Ongoing inspection concerns: <bool + source>

## Class observations

(Free-text qualitative notes about label patterns, division attitude, sample size caveats, time trends.)

## Data-quality notes

- <each entry from data_quality_notes>

---

*Skill: research-clinical-class-precedent.*
```

## Worked example — AXS-05 (NMDA antagonist + CYP2D6 inhibitor) for MDD

Input:
- `drug_name = "AXS-05"`
- `mechanism_of_action = "NMDA receptor antagonist + CYP2D6 inhibitor"`
- `indication = "Major Depressive Disorder"`
- `company_ticker = "AXSM"`
- `cik = "1579428"`

Step 1 — class resolution: split MoA into `["NMDA receptor antagonist", "CYP2D6 inhibitor"]`. NMDA antagonist is the operative class for an MDD indication (CYP2D6 inhibition is a pharmacokinetic modifier, not the operative mechanism for efficacy). Resolved class label: `"NMDA antagonist (depression / mood)"` with confidence 0.80.

Step 2 — class drugs (mood/depression context): `["dextromethorphan", "esketamine", "ketamine", "memantine"]`. Note: memantine is approved for Alzheimer cognitive symptoms not MDD — kept in the class but flagged. Esketamine (Spravato) and AXS-05 itself are the two with FDA approvals for depression. AXS-05 is not counted in its own class precedent (would be circular).

Step 3 — approvals from openFDA:
- Esketamine (Spravato), Janssen, 2019-03-05, TRD: boxed warning yes, REMS yes, AdCom held (14-2 favorable), Priority Review + Breakthrough.
- Memantine (Namenda), Forest Labs, 2003-10-16, AD (not MDD; included as class context).

Step 4 — CRLs: AXS-05 itself received a CRL on 2021-08-12 (CMC ground; addressed and approved 2022-08-19). Memantine extended-release received a 2010 CRL on a label-expansion supplement.

Step 5 — Federal Register AdCom search for "psychopharmacologic drugs advisory committee" 2016-2026: 4 meetings, 2 of which were class-relevant. AdCom rate for the division on novel mood drugs ≈ 0.20.

Step 6 — boxed-warning rate among approvals: 1/2 = 50% for the narrow class; falls to 33% if memantine added; flagged as small-sample.

Step 7 — sponsor history (Axsome / AXSM, CIK 1579428): one prior CRL (the AXS-05 2021 CRL on CMC), one prior approval (AXS-05 2022 for MDD), no breakthrough, no RTOR (oncology only).

Step 8 — synthesize: anchor approval rate for class (depression context, n=2 approvals out of 3 decided events excluding AXS-05 itself if used as candidate) ≈ 0.67; CI very wide due to n=3. Confidence 0.55 with `data_quality_notes: ["sparse class — n_total_in_class = 3"]`.

Output JSON includes: `approval_rate_class: 0.67`, `confidence: 0.55`, `source: "openFDA + Federal Register"`, `sponsor_history.prior_crl_same_indication: true` (the 2021 AXS-05 CRL was on the same MDD application — but that exact application was ultimately approved in 2022; the question for P1 is whether this is currently a *standing* CRL that should depress probability, vs. a *resolved* CRL on a now-approved file. The skill records the resolved CRL but sets `prior_crl_same_indication: false` because the application is now approved — recording any prior-CRL grounds in `data_quality_notes` for the human reader).

Markdown report explains the sparse-class caveat in plain text in the "Class observations" section.

## Failure modes and recovery

| Failure mode | Detection | Skill response |
|---|---|---|
| openFDA returns 5xx | HTTP status | Retry 3x with exponential backoff; if all fail, write `data_quality_notes: ["openfda_unavailable"]`, set anchor confidence ≤ 0.50, fall back to industry default 0.62 |
| openFDA returns 0 results for the class | result list empty | Try alternate query (brand vs. generic vs. MoA); if all empty, mark `class_membership_inference: not_resolved`, confidence ≤ 0.45 |
| Federal Register API down | HTTP error / no `documents` field | Use industry default AdCom rate 0.10–0.15, mark `confidence: 0.40` for that field |
| EDGAR EFTS rate-limited (HTTP 429) | status code | Exponential backoff up to 60s; if still throttled, skip sponsor history, mark `sponsor_history: {ticker, "_status": "edgar_rate_limited"}` |
| ChEMBL MCP unavailable | tool exception | Fall back to `helpers/class_atlas.py` hardcoded table; if class not in atlas, use literal MoA string |
| Class drug list empty | `class_drugs == []` after Step 1 | Refuse to compute base rate; emit `{"status":"no_class_resolved","reason":"...","confidence":0.0}` instead of fabricating; never guess a number |
| Sponsor CIK invalid | EDGAR EFTS returns 0 hits despite valid CIK pattern | Try `company_name` query as fallback; record source URL of failed queries |
| Single-drug "class" | n_total_in_class == 1 | Refuse to anchor on n=1; widen spread; downstream P1 will keep its conservative default |
| Network-disabled environment (offline mode) | `--offline` flag | Use illustrative defaults from worked example, set confidence 0.30, mark `data_quality_notes: ["offline mode — values illustrative"]` |
| HALT_FLAG present | check `02_System/engine/health/HALT_FLAG` at startup (read-only via reference folder) | Log and exit immediately with status `halted` |

No silent failures. Every degraded path produces a `data_quality_notes` entry and a lowered confidence.

## Compliance with system invariants

- **Atomic writes** (temp file + rename via `atomic_write_text`) per D-052 — both JSON sidecar and markdown report.
- **Confidence + source on every output row** — every approval, CRL, AdCom event, sponsor history item, and the headline base rate carry a `confidence` (0.0–1.0) and `source` (URL or file path) per CLAUDE.md §1.6.
- **Append-only**: this skill writes to its own `outputs/` directory and never mutates ledgers in `02_System/engine/`. If a future caller wants to fold class-precedent data into `historical_events_ledger.json`, that is a separate skill (M1).
- **Reference folder is read-only** — the skill reads from `Investment tool backup/` (HALT_FLAG, profile_binary_catalyst.md, candidate dossiers) but writes only to `Investment tool backup skills/skills/research-clinical-class-precedent/outputs/`.
- **Bounded runtime** — typical run ≤ 45 s with full network access; offline mode ≤ 5 s.
- **Resumable** — re-running the skill on the same drug overwrites the prior output atomically; no partial-state leakage.
- **No invented data** — when a number is not in a primary source, the field is null with confidence 0.0 and a `data_quality_notes` entry; never guess.
- **Numeric tickers rendered with company names** — outputs always render tickers with company names per CLAUDE.md §1.7 / `feedback_ticker_company_names.md`.

## Helpers (in `helpers/`)

| Script | Purpose |
|---|---|
| `class_atlas.py` | Hardcoded class → drug lookup fallback for the active book (NMDA antagonists, JAK inhibitors, GLP-1 agonists, anti-amyloid mAbs, anti-FXIa, anti-IL-23/p19, anti-VEGF, IGF-1R inhibitors, complement C5). |
| `fda_class_lookup.py` | openFDA query wrapper with retry, pagination, and result normalization. Supports queries by generic name, brand name, MoA pharm class, and active ingredient. |
| `adcom_class_history.py` | Federal Register API wrapper for AdCom announcements, parameterized by class / division / date range. |
| `company_fda_history.py` | EDGAR EFTS wrapper that pulls 8-K filings by CIK matching CRL / BTD / Priority Review / RTOR / Form 483 keywords. |
| `analyze.py` | Orchestrator. Calls the four helpers above in sequence, synthesizes the JSON sidecar and markdown report, atomically writes outputs, prints structured stdout summary. |

All helpers obey: graceful network-error handling (return structured `{"ok": false, "reason": ...}` rather than crashing), atomic writes through the shared `atomic_write` helper from `monitor-kill-conditions/helpers/`, no global state.

## Invocation

CLI:
```
python helpers/analyze.py \
  --drug "AXS-05" \
  --indication "Major Depressive Disorder" \
  --moa "NMDA receptor antagonist + CYP2D6 inhibitor" \
  --ticker AXSM --cik 1579428 \
  --company-name "Axsome Therapeutics" \
  --output-dir <working>/skills/research-clinical-class-precedent/outputs
```

For an offline smoke test (no network):
```
python helpers/analyze.py \
  --drug "AXS-05" --indication "Major Depressive Disorder" \
  --moa "NMDA receptor antagonist + CYP2D6 inhibitor" \
  --ticker AXSM --cik 1579428 --offline \
  --output-dir <working>/skills/research-clinical-class-precedent/outputs
```

P1 (analyze-fda-approval-prospects) automatically picks up the resulting `<drug>_class_basrates.json` if it exists in the expected sibling outputs directory; no further wiring needed.

---

*Skill: research-clinical-class-precedent. Built per Phase 1 of the autonomous skill-build plan, ratified 2026-04-29.*
