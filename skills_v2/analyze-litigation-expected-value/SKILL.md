---
name: analyze-litigation-expected-value
description: Outcome probability tree x magnitude per outcome x time-to-resolution NPV for any litigation candidate (federal civil, Delaware Chancery, ITC 337, PTAB IPR, SEC enforcement, DOJ/FTC antitrust). Includes precedent lookup and graceful auth-required handling for CourtListener.
type: skill
---

# analyze-litigation-expected-value (P5)

## Purpose

Given a litigation signal (case, court, claim type, claim amount, parties), produce a defensible expected-value estimate that the thesis composer (U2) and dossier author can plug into the litigation profile's scorecard rubric. The skill is invoked when the litigation profile dispatch needs an EV anchor, when a fresh `sec_enforcement_scanner` / `courtlistener_scanner` / `chancery` signal scores into the Watchlist or Immediate band, or when an active litigation dossier reaches a procedural milestone (motion-to-dismiss ruling, summary judgment, settlement filing) that warrants re-pricing.

The deliverable is a **probability-weighted outcome tree, magnitude per branch, time-to-resolution, and discounted EV** with assumption ledger and per-row confidence + source URL. Every numeric output carries `confidence` (0.0–1.0) and `source` (URL or file path) per CLAUDE.md §1.6.

## Inputs

| Field | Type | Example | Notes |
|---|---|---|---|
| `case_id_or_docket` | string | `1:24-cv-04563`, `2024-0123-AGB`, `337-TA-1432`, `IPR2024-00451`, `LR-26539` | Free-form; resolver classifies |
| `court` | string | `S.D.N.Y.`, `Delaware Chancery`, `USITC`, `USPTO PTAB`, `SEC` | Used as routing hint |
| `case_type` | string | `securities_fraud`, `antitrust`, `patent`, `delaware_breach_of_fiduciary_duty`, `itc_337`, `ptab_ipr`, `sec_enforcement` | Drives precedent lookup |
| `parties` | object | `{plaintiff: ["X"], defendant: ["Y"], publicly_traded_party_ticker: "PTON"}` | Includes the in-universe issuer ticker |
| `claim_amount_usd` | number | `250_000_000` | Plaintiff's claimed damages; null for non-monetary |
| `enterprise_value_usd_pre_signal` | number | `4_500_000_000` | Required for materiality denominator (CLAUDE.md §1.6, D-059) |
| `motion_stage` | string | `complaint_filed`, `mtd_pending`, `mtd_denied`, `discovery`, `summary_judgment_pending`, `verdict_appealed` | Drives outcome priors |
| `mode` | string | `evaluative` (current snapshot) \| `forward_looking` (anticipating next milestone) | |

`case_id_or_docket` plus `court` are mandatory. All other inputs are recommended; missing values fall back to conservative defaults that lower the per-row confidence and append the missing field to `assumptions_ledger`.

## Outputs

Two files, atomic-written to `skills/analyze-litigation-expected-value/outputs/`:

1. `<case_slug>_ev_analysis.md` — human-readable analysis (markdown).
2. `<case_slug>_outcome_tree.json` — machine-readable sidecar (JSON).

`<case_slug>` = `case_id_or_docket` lowercased, non-alphanumeric replaced with `_`, max 64 chars (e.g. `1_24_cv_04563`, `lr_26539`, `337_ta_1432`).

The JSON sidecar conforms to the **Output schema** below.

A structured stdout summary line is printed on completion: a one-line JSON containing `case_slug`, `n_branches`, `ev_usd_mm`, `ev_pct_of_ev`, `time_to_resolution_median_days`, `discount_rate`, `confidence`, `source`, `auth_status`, `duration_s`.

## Methodology

The skill runs in five passes plus a synthesis step. Each pass writes its own contribution to the JSON sidecar with a per-pass `confidence` and `source`, so a downstream consumer can tell which dimensions are firm and which are weak.

### Pass 1 — Case classification and party-resolution

1. Parse `case_id_or_docket` and `court` to detect the court family (federal civil / chancery / itc / ptab / sec / antitrust). Each family maps to a strategy in `02_System/engine/strategies/lit_*.md` for primary-source URLs.
2. Resolve the publicly-traded party. Required: `parties.publicly_traded_party_ticker`. If absent, attempt name → CIK match via SEC EDGAR submissions API. If the docket names a subsidiary, look up Exhibit 21 in `01_Opportunities/active/<ticker>/dossier.md` (working folder mirror) or call out for the parent map. **If party-resolution confidence < 0.85, the rubric requires we drop the signal — emit `auth_status: "party_confidence_low"` and exit with status `manual_review` (mirrors profile_litigation.md auto-cap).**
3. Compute `materiality_denominator`: prefer `enterprise_value_usd_pre_signal` (the 30-day-pre-signal VWAP-based EV per D-059). If missing, fall back to last-known EV from the most recent 10-K/10-Q and tag `materiality_denominator_note: "fallback_to_filing_ev"` with confidence ≤ 0.60.

Pass-1 confidence: 0.90 if exact CIK match + EV present; 0.70 if name-only match; 0.50 if subsidiary inferred; 0.30 if EV fallback was needed. Source: SEC EDGAR submissions API URL or local dossier path.

### Pass 2 — Outcome probability tree

Build a **one-step or two-step** outcome tree depending on `motion_stage`:

**One-step tree** (used when stage is past summary judgment, or for SEC enforcement settled releases): branches are terminal — `dismissed`, `settled`, `verdict_for_plaintiff`, `verdict_for_defendant`. Probabilities sum to 1.0 ± 0.001.

**Two-step tree** (used when stage is `complaint_filed` or `mtd_pending`): step-1 branches include `mtd_granted` (terminal, dismissed), `mtd_denied → goes to settlement / verdict / continued`, with the conditional sub-tree expanded under `mtd_denied`. Top-level probabilities still sum to 1.0.

Probabilities come from **`precedent_settlements.py`** (see helper) which holds case-type-conditional base rates derived from public empirical work (see `helpers/precedent_settlements.py` docstring for citations: BU School of Law class action settlement studies, NERA Securities class-action reviews, USITC docket statistics, PTAB E2E published trial outcomes, Cornerstone settlement reports). Where empirical data is sparse for the case type, the helper returns a `prior_band` + `prior_uncertainty` object, and Pass-2 confidence falls.

For each terminal branch the helper returns:
- `probability`: point estimate
- `probability_ci_low`, `probability_ci_high`: Wilson 95% CI (or 80% CI if n < 10 and explicit `n` available)
- `n_supporting_precedents`: counts used
- `confidence`: 0.0–1.0 (driven by `n` and recency of precedents)
- `source`: empirical-source URL or `helpers/precedent_settlements.py:<case_type>_priors`

The `motion_stage` field shifts priors: e.g., `securities_fraud` MTD-grant rate of ~50% means the post-MTD-denied conditional drops the `dismissed-on-MTD` probability mass to 0.

### Pass 3 — Magnitude per branch

For each branch:

- **`dismissed`** — magnitude = $0 to defendant equity (defense costs paid out of insurance / reserves; treat as immaterial unless > 1% of EV).
- **`settled`** — magnitude = `claim_amount_usd × settlement_multiple`, where `settlement_multiple` comes from `precedent_settlements.py` for this case type. Securities fraud settlement multiples typically 0.5–5% of claimed damages in NERA studies; antitrust 2–15%; patent 1–8%; chancery merger-objection 0.5–3% bumps; SEC enforcement disgorgement is a different formula — `disgorgement + civil_penalty` where helper returns priors for both.
- **`verdict_for_plaintiff`** — magnitude = `claim_amount_usd × verdict_haircut_multiple`. Helper returns case-type haircut (jury haircuts averaging 25–60% off claimed damages depending on case type).
- **`verdict_for_defendant`** — magnitude = $0 (cost of defense capitalized).

Magnitudes are signed: a `verdict_for_plaintiff` is a **liability for the publicly-traded defendant**, so `magnitude_to_issuer` is negative; if our publicly-traded party is the **plaintiff**, signs flip. The helper handles this via the `publicly_traded_party_role` field.

For SEC enforcement (case_type `sec_enforcement`):
- `magnitude_disgorgement` = mean disgorgement for similar respondent profile (helper baseline ~$50M issuer-side per profile_litigation.md Dimension 1 note);
- `magnitude_penalty` = mean civil penalty;
- `magnitude_to_issuer = -(disgorgement + penalty)`;
- branches collapse to `settled_consent_order` and `litigated_then_settled` and `dismissed`.

Pass-3 confidence per branch: 0.80 if helper has ≥ 30 precedent settlements, 0.60 if 10–29, 0.40 if 5–9, 0.25 if < 5. Source: helper return.

### Pass 4 — Time-to-resolution

For each branch, `time_to_resolution_days` median + IQR comes from `precedent_settlements.py:<case_type>_time_priors`. Examples:
- Securities class-action: settlement median ~ 1100 days; verdict ~ 2200 days.
- ITC 337: institution-to-final-determination ~ 480 days.
- PTAB IPR: petition-to-final-written-decision ~ 540 days.
- Delaware Chancery breach-of-fiduciary-duty (in announced-deal context): ~ 120–180 days when expedited.
- SEC enforcement settled administrative action: ~ 30–90 days from release.

The skill writes both `branch_time_to_resolution_days` (per branch) and an EV-weighted aggregate `time_to_resolution_median_days_overall`.

Pass-4 confidence: 0.75 if helper has explicit IQR; 0.50 if helper returns mean only.

### Pass 5 — Discounted expected value (NPV)

Use `discount_rate_calc.py` (see helper). Default `discount_rate` = 0.10 annualized (issuer cost of equity proxy; can be overridden via input). For each branch:

```
npv_branch = magnitude_to_issuer / (1 + discount_rate) ** (time_to_resolution_days / 365.0)
ev_contribution = probability × npv_branch
```

Aggregate:

```
ev_usd = sum_over_branches(ev_contribution)
ev_pct_of_ev = ev_usd / materiality_denominator
```

Compute a **materiality band** (mirrors profile_litigation.md Dimension 1):
- > 20% of EV → `band: very_high`
- 10–20% → `band: high`
- 5–10% → `band: moderate`
- 2–5% → `band: low`
- < 2% → `band: minimal`

### Synthesis — Confidence aggregation

Per CLAUDE.md §1.6, every output row carries `confidence` (0.0–1.0) and `source`. The synthesis step:

1. Computes `overall_confidence` as the harmonic mean of per-pass confidences (harmonic mean penalizes the weakest pass — e.g., if precedent counts are low, EV confidence drops fast).
2. Builds `assumptions_ledger`: an ordered list of `{assumption, basis, source, confidence_impact}` capturing every fallback / inference / default that was used.
3. Tags `auth_status` ∈ `{ok, party_confidence_low, courtlistener_auth_required, partial_data, blocked}`.

If `auth_status != "ok"`, the markdown report leads with the auth-status banner and the JSON sidecar carries `recoverable: true|false`. Per `known_blockers` in the build plan, **CourtListener token absence must not crash the skill** — it must return cleanly with `auth_status: "courtlistener_auth_required"`, `recoverable: true`, and a `next_steps` field instructing how to obtain the token.

## Profile-specific dispatch

This skill is single-profile (`litigation`). Its outputs feed the `litigation` scorecard rubric (Dimension 1 — Financial Materiality, Dimension 2 — Outcome Probability, Dimension 4 — Resolution Timeline) directly. The `band` from Pass-5 maps to a Dimension-1 score per the table in `profile_litigation.md`. `verdict_for_plaintiff.probability` maps to Dimension-2.

For dual-profile candidates (e.g., a chancery appraisal during an announced merger), this skill still runs as the litigation lens; the merger_arb lens is handled separately by P4. The two outputs are reconciled by U2 (compose-thesis-with-discipline).

## Output schema (JSON sidecar)

```json
{
  "skill_id": "P5",
  "skill_name": "analyze-litigation-expected-value",
  "ran_at_utc": "2026-04-29T02:14:00Z",
  "inputs": {
    "case_id_or_docket": "LR-26539",
    "court": "SEC",
    "case_type": "sec_enforcement",
    "parties": {
      "plaintiff": ["SEC"],
      "defendant": ["John Fernandez", "Avail Progression, LLC", "Elite Generators, LLC"],
      "publicly_traded_party_ticker": null
    },
    "claim_amount_usd": null,
    "enterprise_value_usd_pre_signal": null,
    "motion_stage": "complaint_filed",
    "mode": "evaluative",
    "discount_rate": 0.10
  },
  "case_slug": "lr_26539",
  "auth_status": "party_confidence_low",
  "recoverable": true,
  "next_steps": "Manual review — defendants are individuals + private LLCs. No publicly-traded party resolved; signal would be auto-archived per profile_litigation.md Dimension-6 auto-cap.",
  "passes": {
    "pass_1_party_resolution": {
      "publicly_traded_party_match_confidence": 0.0,
      "materiality_denominator_usd": null,
      "materiality_denominator_note": "no_public_party",
      "confidence": 0.10,
      "source": "https://www.sec.gov/enforcement-litigation/litigation-releases/lr-26539"
    },
    "pass_2_outcome_tree": { ... },
    "pass_3_magnitude": { ... },
    "pass_4_time_to_resolution": { ... },
    "pass_5_npv": {
      "ev_usd": 0.0,
      "ev_pct_of_ev": 0.0,
      "band": "n_a_no_public_party",
      "confidence": 0.10,
      "source": "helpers/discount_rate_calc.py"
    }
  },
  "outcome_tree": [
    {
      "branch": "settled_consent_order",
      "probability": 0.65,
      "probability_ci_low": 0.55,
      "probability_ci_high": 0.74,
      "n_supporting_precedents": 47,
      "magnitude_to_issuer_usd": null,
      "time_to_resolution_days": 60,
      "npv_to_issuer_usd": null,
      "ev_contribution_usd": null,
      "confidence": 0.70,
      "source": "helpers/precedent_settlements.py:sec_enforcement_priors"
    }
  ],
  "assumptions_ledger": [
    {
      "assumption": "No publicly-traded defendant — auto-archive per Dimension-6",
      "basis": "All three respondents are individual / private LLC",
      "source": "https://www.sec.gov/enforcement-litigation/litigation-releases/lr-26539",
      "confidence_impact": "blocking"
    }
  ],
  "overall_confidence": 0.10,
  "duration_s": 0.05
}
```

## Worked example — LR-26539 (most-recent SEC enforcement signal)

**Input** (from `02_System/engine/signals/sec_enforcement_scanner_output.json`, signal_id 897e8ca1bd23e4a8f14e126bcde57b02):
- `case_id_or_docket`: "LR-26539"
- `court`: "SEC"
- `case_type`: "sec_enforcement"
- `parties.defendant`: ["John Fernandez", "Avail Progression, LLC", "Elite Generators, LLC"]
- `parties.publicly_traded_party_ticker`: null

**Pass 1**: Party resolution fails — none of the three respondents map to an in-universe public issuer. Per `profile_litigation.md` Dimension-6 auto-cap (party confidence < 0.85), the signal is auto-archived. The skill exits cleanly with `auth_status: "party_confidence_low"`, `overall_confidence: 0.10`, and a `next_steps` instructing manual review.

**Output**: `lr_26539_ev_analysis.md` (banner: "AUTO-ARCHIVED: no publicly-traded defendant") and `lr_26539_outcome_tree.json` (sidecar with empty `outcome_tree` and full `assumptions_ledger`).

This is the **expected behavior** for this signal — the skill correctly refuses to fabricate an EV when the rubric's mandatory auto-cap fires. A wrong-party EV would contaminate the signal log, per `feedback_primary_source_discipline.md`.

A second illustrative path runs through the helper using a synthetic securities-fraud case (`SEC_v_Mock_Issuer_Inc`) to demonstrate the full happy-path pipeline. See `helpers/analyze.py --offline-illustrative` for that.

## Failure modes and recovery

| Failure | Detection | Skill response |
|---|---|---|
| `HALT_FLAG` present | Read at startup | Log + exit 0; sidecar carries `halted: true` |
| CourtListener token missing | Helper returns `{auth_required: true}` | `auth_status: "courtlistener_auth_required"`, `recoverable: true`, `next_steps: "Add COURTLISTENER_API_TOKEN to 02_System/engine/config/secrets.env per Q-017"`; offline-mode fallback path uses cached precedent priors only — exit 0, do not crash |
| Party resolution confidence < 0.85 | Pass-1 internal | `auth_status: "party_confidence_low"`, `recoverable: true`; sidecar still produced with empty outcome tree + `assumptions_ledger` entry |
| `claim_amount_usd` missing | Pass-3 | Per-branch magnitude flagged `claim_amount_imputed_from_case_type_median`; confidence drops by 0.20 |
| `enterprise_value_usd_pre_signal` missing | Pass-1 | Fallback to most-recent 10-K filing EV; `materiality_denominator_note: "fallback_to_filing_ev"`; confidence drops by 0.20 |
| `precedent_settlements.py` returns sparse priors (n < 5) | Pass-2/3 | Per-row `confidence ≤ 0.30`; sidecar carries `sparse_class: true`; markdown reports prominently flag the sparse class |
| Atomic write fails (disk full / permission) | Helper raises | Skill propagates exception; meta_scheduler captures stderr; sidecar not partially written (atomic invariant) |
| Edit-tool null padding (per `feedback_edit_tool_null_padding.md`) | Post-write | Helper sanity-checks output file via tail-byte read; if null bytes detected, retry once via Write (not Edit) |

## Compliance with system invariants

- **Atomic writes** — temp file + os.replace (`atomic_write.py` reused).
- **Confidence + source on every row** — every branch in `outcome_tree[]`, every entry in `assumptions_ledger[]`, every pass.
- **Append-only behavior** — the skill does not mutate prior outputs; each run produces a new `<case_slug>_*` file pair (overwrite is allowed only when re-running on the same case).
- **Read-only reference folder** — skill reads `Investment tool backup/02_System/engine/...` for strategy docs, never writes.
- **HALT_FLAG honored** at the top of `analyze.py`.
- **No ephemeral fixes** — sparse-class detection is a structural feature, not a per-case workaround.
- **Numeric-ticker rendering** — when the publicly-traded party has a numeric ticker (e.g. JP, 6027), the report renders `6027 (Bengo4.com)` per `feedback_ticker_company_names.md`.
- **D-059 denominator anchor** — uses 30-day-pre-signal VWAP-based EV.
- **Primary-source discipline** — every magnitude / probability / time prior cites a primary or empirical source; helper code holds the citations in module docstrings.

## Helper inventory

| File | Role |
|---|---|
| `helpers/atomic_write.py` | Reused atomic file write (temp + os.replace). |
| `helpers/case_outcome_tree.py` | Probability tree builder by `(case_type, motion_stage)`. Returns branches with Wilson CIs and per-row confidence. |
| `helpers/precedent_settlements.py` | Case-type-conditional priors: settlement multiples, verdict haircuts, time-to-resolution medians + IQR, n-counts, citation URLs. |
| `helpers/discount_rate_calc.py` | NPV calc with `discount_rate` and `time_to_resolution_days` per branch. Pure-functional, no I/O. |
| `helpers/courtlistener_client.py` | Auth-required client. Returns `{auth_required: true, recoverable: true}` if token missing. Used for live docket enrichment in `evaluative` mode. |
| `helpers/analyze.py` | Orchestrator. Resolves party, calls helpers in sequence, computes overall_confidence, atomic-writes outputs, prints structured stdout. CLI flags: `--case-id`, `--court`, `--case-type`, `--ticker`, `--claim-usd`, `--ev-usd`, `--motion-stage`, `--mode`, `--discount-rate`, `--offline`, `--offline-illustrative`. |

All helpers are `py_compile`-clean; `analyze.py` precompiles its imports on entry per D-070.

---

**End of SKILL.md.**
