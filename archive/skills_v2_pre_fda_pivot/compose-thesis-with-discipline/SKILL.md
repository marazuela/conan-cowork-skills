---
name: compose-thesis-with-discipline
description: Disciplined investment-thesis composer. Refuses to output a thesis unless all six load-bearing fields are populated and defended — variant perception, preconditions, kill criteria, expected return distribution with explicit probabilities, time horizon with milestones, and sizing inputs (conviction, asymmetry, correlation). Consumes outputs from analyze-candidate-financials (U1), profile-specific research skills (P1–P6), and historical precedents (U3). Triggers when a candidate is graduating to dossier or refreshing after a material event.
type: skill
---

# compose-thesis-with-discipline

## Purpose

Compose a disciplined investment thesis for any candidate. Six fields are *required*. The skill refuses to emit a thesis if any field is empty, vague, or unjustified — failure to populate one of them is the most common path to a sloppy thesis that the calibration system later struggles to score.

Invoke this skill when:
- A candidate has graduated to Immediate band and a full dossier is being built.
- A material event (8-K, court decision, FDA letter) has changed the thesis and a refresh is required.
- A scheduled refresh cadence (every 14 days for active dossiers) reaches a candidate.

This is the *integration* skill — it is intended to be called after the supporting skills (U1, P1–P6, U3) have produced their outputs. It does not regenerate financial or counterparty research; it consumes existing outputs and synthesizes them into a defensible thesis document.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `candidate_id` | string (ticker or composite identifier) | `AXSM` | yes |
| `profile` | enum: merger_arb \| activist_governance \| binary_catalyst \| litigation \| insider | `binary_catalyst` | yes |
| `supporting_skill_outputs` | dict of paths | `{"U1": "skills/analyze-candidate-financials/outputs/AXSM_metrics.json", "P1": "skills/analyze-fda-approval-prospects/outputs/AXS-05_approval_analysis.md", "U3": "skills/compare-to-historical-precedents/outputs/AXSM_precedents.md"}` | yes |
| `existing_dossier_path` | string (optional) | `01_Opportunities/active/AXSM/dossier.md` | no |
| `output_dir` | path | `skills/compose-thesis-with-discipline/outputs/` | no |

If `existing_dossier_path` is provided and exists, the skill produces a *delta* thesis (a refresh) anchored to the existing dossier; otherwise it produces a fresh thesis.

## Outputs

`skills/compose-thesis-with-discipline/outputs/<candidate>_thesis.md` — Markdown thesis document, atomic-written.

The document follows the dossier shape used in `01_Opportunities/active/<candidate>/dossier.md` (per `framework/candidate_template.md`) but with strict enforcement of the six required fields.

If any required field cannot be populated to the minimum bar, the skill writes a refusal record to `skills/compose-thesis-with-discipline/outputs/<candidate>_thesis_REFUSED.md` listing exactly which fields failed and why, and emits a stdout JSON summary `{"status": "refused", "missing_fields": [...], "candidate_id": "..."}`.

## Methodology

### Step 1 — Load supporting skill outputs

For each path in `supporting_skill_outputs`:
1. Verify the file exists. If missing, lower confidence on that input and log to `data_quality_concerns`.
2. Parse JSON outputs (e.g., U1 `metrics.json`) into structured fields.
3. Capture key Markdown sections from human-readable outputs (e.g., U1 `financial_assessment.md`, P1 `approval_analysis.md`, P3 `track_record.md`) — note the headline takeaway from each.

### Step 2 — Verify or construct the six required fields

### Step 0 — Load upstream skill outputs (MANDATORY before composing)

Before composing any of the six fields, the skill MUST attempt to load every
relevant upstream skill output from disk. Reading the dossier directly without
first consulting upstream outputs is a verification failure (this was the bug
identified in the 2026-04-29 AXSM verification: U2 read the dossier and
ignored the verified P1 output that already existed for AXS-05).

**Loading priority for `supporting_skill_outputs`:**

For every relevant skill ID, the skill checks paths in this order. The first
path that exists wins; if none exist, the skill records the gap and continues
(the gap may force a refusal under Step "Refusal" below).

1. The path explicitly provided in `supporting_skill_outputs` (caller-provided).
2. `skills/<skill-folder>/outputs/<TICKER>_*_verified_<DATE>.{json,md}` (most-recent verified output).
3. `skills/<skill-folder>/outputs/<TICKER>_*.{json,md}` (build-phase smoke-test output).
4. `skills/<skill-folder>/outputs/<DRUG_OR_ID>_*.{json,md}` (alternate identifier — e.g. drug name for biotech).

**Per-profile required upstream skills:**

| Profile | Required upstream skills |
|---|---|
| `merger_arb` | U1, P4, U3 |
| `activist_governance` | U1, P3, U3 |
| `binary_catalyst` | U1, P1, P2, U3 |
| `insider` | U1, U3 |
| `litigation` | U1, P5, U3 |

If any required upstream skill output is missing for the profile, the thesis
records that gap in `data_quality_notes` and proceeds with degraded
confidence — but never silently substitutes the dossier text for an upstream
analytical output. The dossier is read for *context* only (frontmatter, prior
manual analyst notes), not as a substitute for skill outputs.

**Wired-in protocol (what the skill must do, in order):**

1. Read `supporting_skill_outputs` from input. Resolve any relative paths.
2. For each entry, attempt to open the file. If JSON, parse and store under that skill ID. If markdown, extract the structured tables/sections.
3. For any required upstream skill not in `supporting_skill_outputs`, search disk for the most-recent verified output by glob pattern `skills/<skill>/outputs/{TICKER,DRUG}_*verified*.json` then fall back to `skills/<skill>/outputs/{TICKER,DRUG}_*.json`.
4. For any still-missing required upstream, record `{"missing_upstream": "<skill_id>", "looked_at": ["<paths>"]}` in `data_quality_notes`.
5. Only after Steps 1–4, read the dossier (read-only reference folder) for frontmatter and contextual narrative.
6. Compose the six fields using upstream outputs as primary input and dossier as context.

**Verification check:** The thesis output's `verification` block MUST list every upstream skill ID consulted, the path that was loaded, and a content-checksum of the loaded JSON or first 200 chars of the loaded markdown. If `verification.upstream_loaded` is empty, that is a hard refusal — the thesis cannot emit.

Each of the six fields below has a specific bar. The skill iterates through them in order. If any field cannot meet its bar, the skill records the gap and continues evaluating; at the end, it either emits the thesis or refuses with the gap list.

#### Field 1 — Variant perception (one sentence)

**Bar**: a single declarative sentence stating *what we believe that the consensus does not*. The sentence must (a) be falsifiable in principle, and (b) name the consensus position implicitly or explicitly.

Bad examples (refused):
- "RPAY is undervalued." → not variant; everyone says this about cheap stocks.
- "AXSM has a binary PDUFA." → factual, not a variant view.

Good examples:
- "Consensus prices ~50% approval probability for AXS-05 in ADA; we estimate 65% based on 3-of-4 Phase 3 hits and absence of AdCom — a ~15pp mispricing."
- "Market treats RPAY's 12.5% poison pill trigger as routine defense; we read it as numerical evidence the board sees Forager as a credible bidder, which historically resolves in negotiated transaction at 30–60% premiums."

How to construct: pull the U1 financial-lens summary, the P-skill profile-specific finding, and the U3 precedent base rate. The variant is whatever the integration of those three pieces produces that is not already in the consensus narrative.

#### Field 2 — Preconditions

**Bar**: enumerable list (3–7 items) of facts that must be true for the thesis to play out. Each precondition must be (a) currently observable or scheduled, and (b) tagged with current verification status (verified / pending / at-risk).

Examples for AXSM PDUFA:
1. PDUFA date 2026-04-30 not extended. (verified — no Form 8-K extension as of latest scan)
2. No AdCom convened. (verified — Federal Register clear)
3. No additional CRL on file beyond the 2024 CMC CRL. (verified — primary source dossier)
4. Phase 3 ACCORD-2 results consistent with topline (no late-arriving safety signal). (pending — full publication anticipated)

#### Field 3 — Kill criteria

**Bar**: 3–6 specific, ex-ante, falsifiable triggers. Each kill criterion must be: (a) a discrete event with a clear primary source, (b) actionable within ≤72 hours, (c) tied to a specific position action (full exit, partial exit, hedge engagement).

Bad: "valuation gets too high" (not specific).
Good: "Forager files 13D/A reducing stake by ≥2% — full exit within 3 trading days. Source: SEC EDGAR full-text search filer=Forager Capital Management."

The skill cross-checks against the profile-specific kill conditions in `framework/profile_<profile>.md`. If the candidate dossier is for `merger_arb`, ensure at least one kill criterion addresses deal-break (8-K, withdrawal, MAC invocation). For `binary_catalyst`, at least one addresses CRL/extension. For `activist_governance`, at least one addresses settlement/withdrawal. For `litigation`, at least one addresses dismissal/settlement. For `insider`, at least one addresses cluster reversal or insider selling at thesis target.

#### Field 4 — Expected return distribution

**Bar**: 3–5 scenarios with explicit probability weights summing to 1.0, each tagged with target price, return percentage, and a one-line justification anchored to either a precedent (U3 output), a model (U1 SOTP, P1 EV calc), or a counterparty pattern (P3/P4 history).

Probabilities must:
- Sum to 1.0 (within ±0.01).
- Have at least one bear/break scenario with probability ≥ 0.10 (no all-bull thesis).
- Reference at least one source per scenario.

Output format (machine-readable side-output):
```json
{
  "scenarios": [
    {"label": "base", "probability": 0.50, "target_price": 5.40, "return_pct": 33.3, "anchor": "U1 SOTP $5.20 + standard sponsor counter-bid premium"},
    {"label": "bull", "probability": 0.25, "target_price": 6.75, "return_pct": 66.7, "anchor": "Strategic acquirer multiple per peer FOUR/GPN takeouts"},
    {"label": "bear", "probability": 0.20, "target_price": 2.80, "return_pct": -30.9, "anchor": "Pre-offer 30d VWAP per fact pack"},
    {"label": "tail", "probability": 0.05, "target_price": 4.00, "return_pct": -1.2, "anchor": "Litigation overhang — Unocal/Moran precedent"}
  ],
  "expected_value_pct": 27.1,
  "anchor_price": 4.05
}
```

#### Field 5 — Time horizon with milestones

**Bar**: a horizon date or window AND at least 3 milestone dates between now and the horizon. Each milestone must be: (a) calendar-bounded, (b) tied to an expected event (filing, regulatory action, vote), (c) tied to a position-sizing reweight rule (scale up / scale down / hold).

Example for AXSM (PDUFA 2026-04-30):
- T+0 (today): starter 1.5%, entry $175–$180
- T+10 (Apr 23, AdCom announcement window closes): scale to 2.0% if no AdCom; reduce to 0.75% if AdCom announced
- T+14 (PDUFA date Apr 30): hold through decision; exit T+1 post-decision
- T+1 post-PDUFA: full exit on approval at +20–30%; full exit on CRL at floor

#### Field 6 — Sizing inputs

**Bar**: three numeric/categorical fields, each justified by a specific input from the supporting skills.

- **Conviction grade** — high / medium / low. Justified by U1 confidence + P-skill probability anchor + U3 precedent density.
- **Asymmetry score** — ratio of (probability-weighted upside) / (probability-weighted downside). Computed from Field 4 scenarios. Must be ≥ 1.5 for any positive recommendation.
- **Correlation with existing book** — list of currently-active dossiers (read from `01_Opportunities/active/`) and their thesis exposures. Flag if the candidate adds a third+ dossier exposed to the same factor (sector, deal type, regulator).

### Step 3 — Refusal logic

Apply the bars above. If any field fails:

1. Write `<candidate>_thesis_REFUSED.md` with the gap list, the supporting evidence available so far, and a directive to the upstream skill (e.g., "U1 capital-allocation grade missing — re-run with refreshed 10-K") or an instruction to the user (e.g., "U3 precedent density < 5 neighbors — request manual precedent review before thesis finalization").
2. Emit stdout summary `{"status": "refused", "missing_fields": [...]}`.
3. Do NOT emit a partial thesis under a different filename — silent partial output defeats discipline.

### Step 4 — Compose the thesis document

If all six fields pass, compose the Markdown thesis with the following sections:

1. Header (frontmatter matching `candidate_template.md` §Header).
2. Headline thesis (1–2 sentences encapsulating Field 1 — the variant).
3. Variant perception (full Field 1 paragraph).
4. Preconditions (table of Field 2 with verification status).
5. Kill criteria (table of Field 3 with source and action).
6. Expected return distribution (table + JSON sidecar of Field 4).
7. Time horizon and milestones (table of Field 5).
8. Sizing inputs (Field 6 with rationale).
9. Supporting evidence (citations to U1/P1–P6/U3 outputs).
10. Confidence summary (top-level number derived from supporting-skill confidences).

### Step 5 — Atomic write + scheduler summary

Write the thesis file atomically (temp + rename). Emit stdout summary:

```json
{"status": "ok", "candidate_id": "...", "profile": "...", "rows_written": 1, "thesis_path": "..."}
```

If invoked as a scheduled task, also append a one-line entry to `02_System/engine/working/scheduler_log.jsonl` (when run in the live system, not in the working folder smoke test).

### Step 6 — Confidence rules

- Top-level thesis confidence = min(supporting_skill_confidences) × variant_anchoring_factor.
- variant_anchoring_factor = 1.0 if variant is anchored to a specific quantitative gap (e.g., "consensus 50%, our estimate 65%"); 0.85 if anchored to qualitative gap ("market underappreciates X").
- If thesis confidence < 0.5, the thesis still emits but with `recommendation: "watch_only"` instead of an active position recommendation.

## Profile-specific application

The methodology applies universally; the *content* of each field shifts by profile:

| Field | merger_arb | activist | binary_catalyst | litigation | insider |
|---|---|---|---|---|---|
| Variant | Spread vs deal-break probability gap | Activist credibility vs market-priced campaign success | Approval prob gap | Outcome-tree gap | Cluster-pattern reading |
| Preconditions | Deal definitive, no withdrawal | 13D active, no settlement | PDUFA on calendar, no AdCom | Case live, no settlement | Plan basis, no recent insider sale |
| Kill criteria | 8-K close/withdrawal, spread <2% | 13D/A withdraw, settlement | CRL/extension | Dismissal/settle | Cluster reversal |
| Scenario tree | Close/break/extend | Activist win/lose/withdraw | Approve/CRL/extend/reject | Plaintiff/settle/dismiss | Hit/no-move/miss |
| Time horizon | Days-to-close | 6–12 months | Days-to-PDUFA | 12–24 months | 90 days typical |
| Sizing input | Annualized return | Conviction + activist track | EV gap | EV calc | Cluster strength |

## Output schema

`<candidate>_thesis.md` follows `framework/candidate_template.md`. The machine-readable scenario tree is embedded as a JSON code block at the end of §Expected return distribution.

`<candidate>_thesis_REFUSED.md` (only on refusal):

```markdown
# <candidate> thesis — REFUSED

**Refused at**: <ISO timestamp>
**Refusing skill**: compose-thesis-with-discipline v1.0
**Profile**: <profile>

## Failed required fields

| Field | Bar | Gap | Source needed |
|---|---|---|---|
| Variant perception | ... | ... | ... |
| ...

## Supporting evidence available

- U1 output: <path> (confidence X)
- P-skill output: <path> (confidence X)
- ...

## Required next action

<specific instruction to upstream skill or to user>
```

## Worked example

**Test candidate**: AXSM — AXS-05 ADA PDUFA 2026-04-30.

Inputs:
```
candidate_id = "AXSM"
profile = "binary_catalyst"
supporting_skill_outputs = {
  "U1": "skills/analyze-candidate-financials/outputs/AXSM_metrics.json",
  "P1": "skills/analyze-fda-approval-prospects/outputs/AXS-05_approval_analysis.md",
  "P2": "skills/research-clinical-class-precedent/outputs/AXS-05_class_precedent.md",
  "U3": "skills/compare-to-historical-precedents/outputs/AXSM_precedents.md"
}
```

Expected six fields:

1. **Variant perception**: "Consensus prices ~50% approval probability for AXS-05 in ADA; we estimate 65% based on 3-of-4 Phase 3 hits, Priority Review grant, no AdCom, and commercial-prep behavior (sales force 300→600) inconsistent with internal CRL expectation — a ~15pp mispricing."
2. **Preconditions**:
   - PDUFA 2026-04-30 not extended. (verified — primary source EDGAR scan)
   - No AdCom announced. (verified — Federal Register)
   - No new CRL beyond 2024 CMC. (verified — dossier)
   - ACCORD-2 results consistent with topline. (pending publication)
3. **Kill criteria**:
   - 8-K announcing AdCom convening — reduce position 50% within 24h.
   - 8-K announcing CRL — full exit at floor within 1 trading day.
   - Insider 10b5-1 affirmation absent + insider sale > $1M — reduce 50%.
   - PDUFA extension via Form 8-K — full exit within 3 trading days.
4. **Expected return distribution** (3-outcome EV per `profile_binary_catalyst.md` §Auto-cap):
   - Approval (P=0.65, +35%): "Approval at $245 PT per Jefferies; commercial scale post-MDD."
   - CRL (P=0.20, −20%): "CMC repeat — historical CRL recovery 12–18mo, stock floors at MDD valuation."
   - Extension (P=0.10, −7%): "Routine 3mo PDUFA push."
   - Outright reject (P=0.05, −30%): "Unprecedented for this profile but tail-priced."
   - **EV = 0.65×35 + 0.20×(−20) + 0.10×(−7) + 0.05×(−30) = +16.55%**.
5. **Time horizon**: T+14 trading days to PDUFA 2026-04-30.
   - T+0: starter 1.5%
   - T+5: scale to 2.0% if no AdCom announced
   - T+10: hold; brace
   - PDUFA day: hold through; exit T+1
6. **Sizing inputs**:
   - Conviction: medium-high (U1 confidence 0.85, P1 EV +16.55% ≥ 5% gate)
   - Asymmetry: (0.65×35 + 0.10×0) / (0.20×20 + 0.05×30) = 22.75 / 5.5 = 4.1× → passes ≥ 1.5
   - Correlation: only other binary_catalyst dossier is VRDN (TED1 Phase 3 readout) — same profile, different MoA, different review division → low correlation, OK to size full

Final thesis emits to `skills/compose-thesis-with-discipline/outputs/AXSM_thesis.md`. All six fields populated; thesis confidence 0.75; recommendation `active_position`.

## Failure modes and recovery

- **Missing supporting skill output**: refusal record is the right answer; do NOT silently substitute. Direct the user/upstream skill to produce the missing input.
- **Partial scenario tree (probabilities don't sum to 1.0)**: refuse with specific gap. The thesis composer is the discipline gate; partial scenario trees become bad calibration data.
- **Confidence < 0.5 from supporting inputs**: still emit thesis but with `recommendation: "watch_only"` — preserves the analytic discipline without committing the position.
- **Existing dossier mismatch**: if `existing_dossier_path` exists but `profile` differs from existing dossier's profile field, emit refusal with directive to user — profile changes are material decisions, not silent overwrites.
- **No precedent density (U3 returns < 3 neighbors)**: continue but flag in confidence; precedents are a confidence input, not a hard gate.
- **Falsifiability test failure** on variant perception (e.g., variant is unfalsifiable): refusal with text suggestion of how to make it falsifiable.

## Compliance with system invariants

- All writes are atomic (temp file + rename) per D-052.
- Six required fields are *non-negotiable*; refusal is the correct behavior when they cannot be populated.
- Append-only behavior: existing thesis is moved to `outputs/_archive/<candidate>_thesis_<date>.md` before new file is written.
- Never modifies the reference folder. All writes go to the working folder.
- Tickers always rendered with company names per `feedback_ticker_company_names.md`.
- HALT_FLAG check at startup.
- Scenario probabilities must sum to 1.0 (±0.01); validated before write.
