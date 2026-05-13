---
name: analyze-fda-approval-prospects
description: Comprehensive FDA approval-probability assessment for a drug/biologic with a defined PDUFA date or upcoming Phase 3 readout. Synthesizes (1) clinical trial-data forensics (primary endpoint integrity, statistical analysis plan, safety profile, ITT vs mITT vs PP, recruitment + dropout patterns), (2) AdCom risk (does this division typically convene one for this indication; historical base rate), (3) label risk (boxed warning likelihood, REMS, indication restriction, post-marketing requirements), (4) CMC / manufacturing risk (Form FDA-483 history, recent warning letters, facility inspections), and (5) class precedent (calls research-clinical-class-precedent / P2 to anchor base rates). Produces a probability range with explicit assumption ledger, NOT a point estimate. Two modes — evaluative (PDUFA imminent) and forward-looking (Phase 3 readout approaching). Triggers when the user asks to "estimate FDA approval probability for", "analyze PDUFA odds for", "what are the approval prospects for", or as part of generating or refreshing a binary_catalyst dossier.
type: skill
---

# analyze-fda-approval-prospects

## Purpose

Estimate the FDA approval probability for a drug/biologic with primary-source-grounded forensic analysis of its trial program and regulatory context, and emit a *probability range* (not a single number) with an explicit assumption ledger. The output is a structured analytical artifact that can be consumed by U2 (compose-thesis-with-discipline) for downstream thesis composition and by U4 (monitor-kill-conditions) for kill-trigger calibration.

This skill is the principal heavy-lift for the binary_catalyst profile. It is invoked when:

- A PDUFA date is within ≤ 60 days (`mode = evaluative`).
- A Phase 3 pivotal readout is anticipated within ≤ 90 days (`mode = forward_looking`).
- A material trial publication or regulatory event has changed the input evidence and a refresh is warranted.
- Pedro asks for a deep-dive on approval prospects for a specific drug.

The skill is deliberately *not* prescriptive on the final number — clinical / regulatory outcomes have heavy-tailed distributions and consensus probability estimates compress reality into a single number with false precision. The skill produces a (P_low, P_mid, P_high) range with an explicit assumption ledger so each downstream consumer can re-read the assumptions and adjust.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `drug_name` | string | `AXS-05 (Auvelity)` | yes |
| `indication` | string | `Alzheimer Disease Agitation` | yes |
| `company_ticker` | string | `AXSM` | yes |
| `cik` | string | `0001579428` | required for primary-source EDGAR pulls |
| `catalyst_date_or_window` | ISO date or "YYYY-MM..YYYY-MM" | `2026-04-30` | yes |
| `mode` | enum: `evaluative` \| `forward_looking` | `evaluative` | yes |
| `mechanism_of_action` | string | `NMDA receptor antagonist + CYP2D6 inhibitor` | optional, used when calling P2 |
| `clinical_trial_ids` | list of NCT IDs | `["NCT04524351", "NCT04763590"]` | optional, recommended |
| `output_dir` | path | `skills/analyze-fda-approval-prospects/outputs/` | no |

If `clinical_trial_ids` is not provided, the skill attempts to discover trials via the ClinicalTrials.gov API using `(drug_name, indication, sponsor)` and proceeds with whatever it finds — flagging discovery as `inferred` rather than `verified`.

## Outputs

Atomic-written:

1. `skills/analyze-fda-approval-prospects/outputs/<drug>_approval_analysis.md` — comprehensive markdown report.
2. `skills/analyze-fda-approval-prospects/outputs/<drug>_probability_estimate.json` — structured estimate + assumption ledger.

Final stdout JSON:
```
{"status":"ok","drug":"...","mode":"...","p_low":0.55,"p_mid":0.65,"p_high":0.75,"output_md":"...","output_json":"...","duration_s":T}
```

If P2 (class precedent) cannot be called or its output is missing, the skill still proceeds but downgrades confidence: it explicitly records `class_precedent: unavailable` in the assumption ledger and the resulting probability range widens (the spread between p_low and p_high grows).

## Methodology

### Step 1 — Resolve trial set

1. If `clinical_trial_ids` provided → use them; mark `trial_discovery: provided`.
2. Otherwise, query ClinicalTrials.gov via `helpers/fetch_trial_data.py`:
   - Search by `intervention=<drug_name>`, `condition=<indication>`, `lead_sponsor=<company_ticker>` (resolve company ticker → sponsor name via EDGAR or a hardcoded ticker→name map for the active book).
   - Filter to interventional trials with phase ≥ 2 marked completed, active, or terminated.
   - Mark `trial_discovery: inferred` and record the search query.
3. Persist trial metadata to `<output_dir>/<drug>_trials.json` for downstream auditing.

### Step 2 — Trial data forensics (the core)

For each pivotal trial (typically the Phase 3s that support the indication), evaluate:

#### 2a — Primary endpoint achievement
- Was the pre-specified primary endpoint hit? (yes / no / partial). Source: published results, ClinicalTrials.gov posted results, sponsor press release with corroborating EDGAR 8-K Item 7.01.
- Was there alpha-spending across multiple endpoints? Hierarchical testing? Bonferroni? If a hierarchical sequence was broken, downstream "wins" are technically not nominal.
- Effect magnitude vs. comparator: report the standardized effect (Cohen's d, hazard ratio with CI, NNT). Avoid relying on p-values alone.
- Durability: was a secondary endpoint at a longer time horizon also positive? If a 12-week trial hit but the 26-week extension missed, this is a yellow flag.

#### 2b — Statistical analysis plan integrity
- ITT, mITT, or PP analysis used for the primary? FDA division preference matters: e.g., neurology often demands ITT; oncology routinely accepts mITT.
- Adaptive design? Interim analysis with sample-size re-estimation? Adaptive features increase risk of statistical multiplicity unless properly controlled.
- Sample size relative to expected effect — was the trial powered for the observed effect or did it surf good luck on an effect smaller than pre-specified?

#### 2c — Safety profile
- TEAE (treatment-emergent AE) rates by organ class — compare to placebo in absolute terms, not relative.
- SAEs and deaths — any imbalance vs. placebo?
- Discontinuations due to AEs.
- Specific signals known to draw FDA attention in this indication: e.g., for a new antipsychotic in an elderly population, mortality + cerebrovascular events; for a hepatotoxic class, ALT/AST elevations + Hy's law cases; for cardiac drugs, QT prolongation.
- Open-label extension data (where present) — typically more conservative on safety than the controlled period.

#### 2d — Population
- Was the trial population representative of the labeled indication's expected use? Geographic distribution, race/ethnicity, age, sex, comorbidities.
- Subgroup analyses — did any pre-specified subgroup miss while the overall hit?

#### 2e — Trial-design tells
- Was the comparator appropriate (active comparator vs placebo vs SoC)?
- Recruitment timeline — slow recruitment historically correlates with lower effect (sites enroll harder-to-treat patients later).
- Dropout rate — high dropout asymmetry between arms is a yellow flag for treatment effect overestimation.
- Prior failed trials in the same indication (by this sponsor or others) — pattern recognition.

Each forensic finding is recorded as `{dimension, finding, evidence, source, signal: positive | neutral | negative, confidence}`.

### Step 3 — AdCom risk

Look up the FDA review division for the indication (e.g., DPP for psychopharmacologic drugs; OOD for oncology). Pull AdCom history for that division over the past 5–10 years (via `helpers/adcom_history_lookup.py`):

- Approval rate when AdCom convened vs. not.
- AdCom convening base rate for this indication / mechanism class.
- Any AdCom currently noticed in the Federal Register matching the drug or indication.

Output: AdCom risk score `{ low | moderate | elevated | confirmed_scheduled }` with rationale.

### Step 4 — Label risk

- Boxed warning probability — based on class precedents (data from P2). Examples: serotonergic drugs in the elderly often carry serotonin-syndrome warnings; antipsychotics in dementia carry mortality boxed warnings.
- REMS likelihood — class history.
- Indication restriction probability — narrower-than-applied label.
- Post-marketing requirement (PMR) likelihood — almost universal for accelerated approvals; less common for full approvals.

Output: a label-risk profile that downstream sizing can incorporate.

### Step 5 — CMC / manufacturing risk

Query EDGAR for any prior CRL referencing CMC issues for this drug or this sponsor's facility network. Cross-reference recent FDA inspection results (Form 483 observations, warning letters) for the manufacturing site of record (if disclosable). Sponsors with recent quality issues at the relevant facility have elevated CMC risk.

Output: `{ low | moderate | elevated }` with primary-source citations.

### Step 6 — Class precedent integration (calls P2)

Call `research-clinical-class-precedent` (P2) with `(mechanism_of_action, indication, company_ticker)`. Consume its outputs:

- Class approval rate (last 10y).
- AdCom rate for this division.
- Boxed-warning probability for the class.
- Sponsor-specific FDA history.

If P2 has not been built or its output is unavailable, the skill records `class_precedent: unavailable` and uses a conservative default (US small-molecule NDA full-approval base rate ≈ 84% per BIO/Informa "Clinical Development Success Rates" 2011-2020 historical aggregate; AdCom base rate ≈ 10–15%) with explicit caveat.

### Step 7 — Synthesize probability range

Combine the inputs into three numbers — `p_low`, `p_mid`, `p_high` — using the following rule of thumb:

- Anchor at the class base rate (p_anchor).
- Adjust by trial-data forensic signal sum (each `positive` finding +2 to +5 pp, each `negative` finding −3 to −10 pp). Cap aggregate adjustment at ±25 pp.
- Apply AdCom modifier: `confirmed_scheduled` → −10 to −20 pp depending on division history; `low` → no adjustment.
- Apply CMC modifier: `elevated` → −5 to −15 pp.
- Apply sponsor-specific FDA history (from P2): prior CRL on the same drug for the same indication = strong negative; prior breakthrough/RTOR designations = mild positive.
- Compute spread (p_high − p_low) as a function of evidence quality: a tight spread (≤ ±5pp) is reserved for cases where every dimension has high-confidence primary-source evidence; a wide spread (> ±15pp) reflects genuine ambiguity.

The skill never emits a single point estimate — always a range. The midpoint is the natural decision-relevant number, but the *spread* is itself information.

### Step 8 — Forward-looking mode adjustments

When `mode == forward_looking`:

- The probability is `P(positive readout)`, not `P(approval)`. The synthesis weights trial-design integrity higher (because no decision has been made yet) and excludes AdCom / label / CMC as primary drivers.
- The skill produces *two* probability ranges: `P(positive readout)` and the conditional `P(approval | positive readout)` separately. The product is the unconditional `P(approval)`.
- Pre-readout signals (recruitment cadence, sites added, blinded SAE patterns, safety committee continuations) are surfaced as the dominant evidence.

### Step 9 — Assumption ledger

Every probability adjustment is recorded as a row in the assumption ledger: `{adjustment, sign, magnitude_pp, rationale, source, confidence}`. The ledger is the audit trail. Downstream consumers can re-evaluate with the ledger in hand.

### Step 10 — Atomic-write outputs

Markdown report sections:
1. Header with drug, indication, ticker, mode, catalyst date.
2. Probability range box (p_low, p_mid, p_high).
3. Trial forensics — per-trial subsection.
4. AdCom risk.
5. Label risk.
6. CMC risk.
7. Class precedent (or `unavailable` note).
8. Assumption ledger (table).
9. Sources.

JSON schema as in Step 9 — see *Output schema* below.

## Profile-specific application

Binary catalyst is the only profile that uses this skill. The skill's outputs feed (a) U2 thesis composition (specifically the variant-perception and expected-return-distribution fields), (b) U4 kill-condition monitoring (the (p_low, p_high) range translates to specific kill thresholds — e.g., "if Federal Register publishes AdCom convening, p_high drops to ≤ 0.50 and the dossier is auto-archived"), and (c) U3 historical-precedent comparison (the trial design fingerprint is a feature for K-NN against prior PDUFAs).

For non-binary-catalyst dossiers, the skill is not invoked — it would not produce useful output for a merger_arb or activist case.

## Output schema

```json
{
  "drug_name": "AXS-05 (Auvelity)",
  "indication": "Alzheimer Disease Agitation",
  "company_ticker": "AXSM",
  "cik": "0001579428",
  "catalyst": "2026-04-30",
  "mode": "evaluative",
  "as_of": "2026-04-29T01:35:00Z",
  "trial_set": [
    {"nct_id": "NCT04524351", "name": "ADVANCE-1", "phase": 3, "primary_endpoint_hit": true, ...}
  ],
  "trial_forensics": [
    {"dimension": "primary_endpoint_achievement", "finding": "3 of 4 pivotal Phase 3 trials hit primary",
     "signal": "positive", "magnitude_pp": 7, "evidence": "...", "source": "...", "confidence": 0.95}
  ],
  "adcom": {"status": "low", "rationale": "PDAC has not noticed an AdCom for AXS-05; brexpiprazole AdCom 2023 sets a precedent the FDA may not feel a need to repeat.", "source": "https://www.federalregister.gov/...", "confidence": 0.85},
  "label_risk": {"boxed_warning_probability": 0.30, "rems_probability": 0.10, "indication_restriction_probability": 0.20, "rationale": "...", "source": "...", "confidence": 0.70},
  "cmc": {"status": "low", "rationale": "...", "source": "...", "confidence": 0.80},
  "class_precedent": {"status": "available_via_P2_or_inferred", "approval_rate_class": 0.78, ...},
  "probability": {"p_low": 0.55, "p_mid": 0.65, "p_high": 0.75},
  "assumption_ledger": [
    {"adjustment": "class base rate", "sign": "anchor", "magnitude_pp": 60, "rationale": "small-molecule NDA full-approval class precedent", "source": "BIO/Informa 2020 aggregate", "confidence": 0.70},
    {"adjustment": "Phase 3 hit rate 3/4", "sign": "positive", "magnitude_pp": 5, "rationale": "Three positive pivotals + one miss with explainable placebo response", "source": "ClinicalTrials.gov + AAN 2025 abstract", "confidence": 0.90},
    ...
  ]
}
```

## Worked example — AXSM `evaluative` mode (test_candidate from plan)

Inputs:
- drug = `AXS-05 (Auvelity)`
- indication = `Alzheimer Disease Agitation`
- ticker = `AXSM`
- cik = `0001579428`
- catalyst = `2026-04-30`
- mode = `evaluative`
- moa = `NMDA receptor antagonist + CYP2D6 inhibitor`
- trials = `[ADVANCE-1, ADVANCE-2, ACCORD-1, ACCORD-2]`

Trial forensics summary:
- Primary endpoint: 3 of 4 hit → `positive` (+5 pp). ADVANCE-2 missed primarily due to large placebo response (12.6 vs ~8–10 typical) — `neutral` (explainable miss).
- SAP integrity: Phase 3 trials used pre-specified ITT; alpha allocation across hierarchical primary-secondary endpoints documented → `positive` (+2 pp).
- Safety: ~100K patient-years of MDD commercial exposure; no new signals in elderly subpopulation that aren't already labeled → `positive` (+3 pp). Bupropion seizure risk in frail elderly is dose-dependent — addressable via labeling → `neutral`.
- Population: elderly with AD; dementia-specific subpopulation analysis documented → `neutral`.
- Comparator + design: placebo-controlled with active observation arm; recruitment timeline normal; dropout rate symmetric → `neutral`.

AdCom risk: `low` — no PDAC convening in Federal Register through 2026-04-29; brexpiprazole AdCom 2023 was contentious but the FDA appears to have moved past the categorical AdCom requirement for this indication.

Label risk: `boxed_warning_probability ~ 0.30` (likely bupropion seizure warning, possibly a frailty-population caution); `rems_probability ~ 0.10`; `indication_restriction_probability ~ 0.20` (could be limited to specific care-setting populations).

CMC risk: `low` — no recent 483 observations on the AXSM contract manufacturers of record per public inspection database.

Class precedent (via P2 if available, else inferred conservative): class approval rate ~ 0.75 for combination-mechanism CNS drugs given prior approvals in the same therapeutic area (Auvelity 2022, Nuplazid 2016, Rexulti ADA expansion 2023).

Probability synthesis: anchor 0.60–0.65 → +5 (P3 hit rate) → +2 (SAP integrity) → +3 (safety) → +0 (AdCom low — no adjustment) → +0 (CMC low) → +2 (priority review granted; sponsor commercial prep aligned). Cap +12 pp net. Result: midpoint ~ 0.65, range 0.55–0.75. Aligns with the active dossier's stated 60–70% range, consistent within rounding.

Spread (0.20) reflects: ADVANCE-2 miss is a real downside risk; FDA could push for AdCom even at T-1; label-restriction risk is real.

JSON output:
```json
{"drug_name":"AXS-05 (Auvelity)","indication":"Alzheimer Disease Agitation","company_ticker":"AXSM","catalyst":"2026-04-30","mode":"evaluative","probability":{"p_low":0.55,"p_mid":0.65,"p_high":0.75}, "assumption_ledger":[...], ...}
```

Markdown report opens with the probability box:
```
**P(approval) = 0.65 (range 0.55 – 0.75)** — confidence: medium-high. Spread 20 pp reflects ADVANCE-2 miss + label-restriction risk.
```

## Failure modes and recovery

| Failure | Detection | Skill behavior |
|---|---|---|
| ClinicalTrials.gov unreachable | helper raises `clinical_trials_unavailable` | proceed with `trial_discovery: inferred_from_dossier_only` if dossier is available; widen probability spread by +5pp |
| Federal Register API 5xx | helper raises `federal_register_unavailable` | AdCom risk = `unverifiable`; widen spread by +5pp |
| EDGAR submissions API down | `edgar_unavailable` | CMC risk = `unverifiable`; cite limitation in report |
| P2 not yet built or output missing | dependency check fails | `class_precedent: unavailable`; use conservative class base rate; widen spread by +5–10pp |
| openFDA AE feed unreachable | `openfda_unavailable` | safety forensic limited to trial-data and label-derived signals only |
| All inputs unverifiable | sweep | refuse to emit a probability range; emit `{"status":"refused","reason":"insufficient primary-source coverage"}` |
| User specifies a drug with no ClinicalTrials.gov record | search returns empty | refuse with `{"status":"refused","reason":"no trials found; verify drug_name/indication"}` |

No silent failures. Every degraded path produces an explicit ledger entry.

## Compliance with system invariants

- **Folder scope.** Reads from reference folder for context; writes only to working `output_dir`. Never mutates the reference folder.
- **Atomic writes.** All outputs via temp + rename.
- **Confidence + source.** Every forensic finding and every assumption-ledger row carries `confidence` ∈ [0,1] and a `source` URL.
- **Primary-source discipline.** Trial-level findings cite ClinicalTrials.gov, PubMed, FDA, or EDGAR. Sponsor press releases are corroborating-only — must be backed by an EDGAR 8-K or similar.
- **No point estimate.** Probability is always a range with explicit spread. Spread is itself information.
- **No leakage.** The skill never reads outcomes (e.g., the post-decision approval announcement) when running on a forward-dated catalyst — relevant to the D-097 leakage discipline applied in the calibration system.
- **Numeric tickers always render with company names.** Per `feedback_ticker_company_names.md`.
- **Bounded runtime.** Target ≤ 60 s typical, hard cap ≤ 120 s. ClinicalTrials.gov queries are cached locally via `<output_dir>/_cache/`.
