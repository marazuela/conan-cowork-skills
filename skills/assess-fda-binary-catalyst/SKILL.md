---
name: assess-fda-binary-catalyst
description: Single-shot Opus skill that produces a full convergence_assessment_v1 payload for an FDA binary_catalyst asset — replaces the orchestrator's multi-stage chain (synthesis → hypothesis → pre-mortem → constitutional → Stage 9 extraction) with one disciplined Opus 4.7 invocation, optionally augmented by sub-skill calls (P1 analyze-fda-approval-prospects for trial forensics, U3 compare-to-historical-precedents for base rates). Output schema mirrors public.convergence_assessments columns so the result can be consumed by the existing eval harness AND, in a later cut-over, by the live orchestrator. Triggers when a Cowork routine asks for an assessment of a single (asset_id, reference_assessment_date, document_set) tuple — typically driven by the `skill_eval_replay` routine for the 271-case backtest, or by an operator one-off.
type: skill
---

# assess-fda-binary-catalyst

## Purpose

Produce one convergence assessment for an FDA binary_catalyst asset (a drug/biologic with a defined PDUFA date or upcoming Phase 3 readout). The output is the *same* JSON shape the v3 orchestrator's Stage 9 extractor produces — `thesis_direction`, `conviction_pct`, `evidence_quality`, `key_facts`, `uncertainties`, `cited_prose_blocks`, `hypotheses`, `pre_mortem`, `reference_class`, `reference_class_base_rate` — so callers can compare side-by-side with the live orchestrator's output on the same input.

This skill exists to test the hypothesis: **a single disciplined Opus 4.7 skill invocation, with optional sub-skill assists, produces a higher-quality assessment than the current 5-call orchestrator chain (Stage 1 ensemble + Stage 2 hypothesis + Stage 3 pre-mortem + Stage 7 constitutional + Stage 9 extraction).** The 271-case `eval_harness` set is the test bench.

The skill is invoked when:
- The `skill_eval_replay` routine sweeps the 271 historical FDA cases for backtesting.
- An operator asks "run the skill orchestrator on asset X as of date Y" — diagnostic / spot-check.
- A future sidecar routine fans out for each new live `orchestrator_runs` row to write a parallel `skill_assessments` artifact (not yet wired — see §Future).

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `asset_id` | uuid | `4f3aeeef-6deb-4fd3-a0ce-45d414038dda` | yes |
| `reference_assessment_date` | ISO date | `2024-09-10` | yes |
| `document_set` | uuid[] | `["e1f2…", "9a8b…"]` | yes — documents the orchestrator would have had access to as of the reference date (no leakage) |
| `eval_case_id` | uuid | row id in `public.eval_harness` | optional — when present, the skill writes its output keyed by this id and `realized_outcome` is available for grading downstream (the skill itself does NOT read realized_outcome — that would be leakage) |
| `orchestrator_run_id` | uuid | `dfc0ef55-…` | optional — when present, the skill writes its output keyed by this id so the sidecar driver can join against the live `convergence_assessments` row |
| `output_dir` | path | `skills_v2/assess-fda-binary-catalyst/outputs/` | optional |
| `enable_subskills` | bool | `true` | optional, default `true` — whether to invoke P1 / U3 sub-skills for trial forensics and historical precedents |

The skill reads `realized_outcome` ONLY for refusal-mode auditing (to confirm the input case is actually resolvable). When constructing the prediction it MUST NOT consult `realized_outcome`, `realized_outcome_data`, or any document with a timestamp greater than `reference_assessment_date`. Leakage is the most common failure mode; the skill self-checks before emitting.

## Outputs

Atomic-written:

1. `<output_dir>/<key>.json` — the structured assessment (schema below). `key` = `eval_case_id` if provided, else `orchestrator_run_id`, else `<asset_id>_<reference_assessment_date>`.
2. `<output_dir>/<key>.reasoning.md` — the Opus reasoning trace as Markdown (the `reasoning_trace` column equivalent), useful for human review.

Final stdout JSON:
```json
{"status":"ok","key":"...","conviction_pct":62.0,"thesis_direction":"long",
 "p_low":0.55,"p_mid":0.62,"p_high":0.72,"cost_usd":0.41,"latency_ms":42100,"output_json":"..."}
```

If the skill cannot produce a defensible assessment (e.g., empty document_set, or all documents are stale relative to the catalyst), it refuses:

```json
{"status":"refused","reason":"insufficient_document_coverage",
 "key":"...","detail":"document_set has 0 docs within 90 days of catalyst"}
```

## Output schema (mirrors `public.convergence_assessments`)

```json
{
  "asset_id": "uuid",
  "reference_assessment_date": "YYYY-MM-DD",
  "thesis_direction": "long | short | neutral | straddle",
  "conviction_pct": 62.0,
  "evidence_quality": 0.78,
  "raw_conviction_pct": 65.0,
  "thesis_summary": "One-paragraph plain-English thesis.",
  "key_facts": [
    {"fact": "...", "source_doc_id": "uuid", "evidence_quote": "...", "confidence": 0.9}
  ],
  "uncertainties": [
    {"uncertainty": "...", "magnitude_pp": 8, "rationale": "..."}
  ],
  "cited_prose_blocks": [
    {"claim": "...", "citation": "doc_id:span", "section": "trial_forensics"}
  ],
  "hypotheses": [
    {"id": "H1", "statement": "...", "supporting_evidence_ids": [...], "verdict": "supported|refuted|undetermined"}
  ],
  "pre_mortem": "Plain-text adversarial review identifying what would make this thesis wrong.",
  "adversarial_challenges": [
    {"challenge": "...", "rebuttal": "...", "severity": "low|medium|high"}
  ],
  "reference_class": "small-molecule NDA, neurology indication, mid-cap sponsor",
  "reference_class_base_rate": 0.84,
  "similar_resolved_case_ids": ["uuid1", "uuid2"],
  "p_low": 0.55,
  "p_mid": 0.62,
  "p_high": 0.72,
  "skill_version": "v0",
  "model_id": "claude-opus-4-7",
  "_meta": {
    "subskills_invoked": ["analyze-fda-approval-prospects", "compare-to-historical-precedents"],
    "documents_consulted": 23,
    "leakage_check_passed": true
  }
}
```

`thesis_direction`, `conviction_pct`, `evidence_quality` are the three load-bearing fields the eval harness scores against (`gold_standard.is_direction_correct` + `metrics.calibration_curve` + `metrics.ranking_auc`). The other fields exist for human review and for the sidecar comparison vs. live orchestrator output.

## Methodology

### Step 0 — Leakage self-check (MANDATORY before any reasoning)

1. Fetch the document set metadata via Supabase MCP:
   ```sql
   SELECT id, source_url, published_at, fetched_at, document_type, mime_type
     FROM public.documents
    WHERE id = ANY($1::uuid[])
    ORDER BY published_at ASC NULLS LAST;
   ```
2. Drop any document where `published_at > reference_assessment_date` — that's leakage. Record the dropped count in `_meta.dropped_for_leakage`.
3. If the surviving set has fewer than 3 documents OR none within 90 days of the catalyst date, refuse with `insufficient_document_coverage`. Don't try to fabricate.
4. Confirm `reference_assessment_date >= max(published_at of surviving docs)` — sanity check.

### Step 1 — Pull asset + fact context

1. Asset metadata:
   ```sql
   SELECT id, ticker, drug_name, indication, mechanism_of_action,
          catalyst_date, catalyst_type, sponsor_cik, watch_priority
     FROM public.fda_assets
    WHERE id = $1;
   ```
2. Pre-extracted facts available as of reference date (mirrors what Stage 1 of the live orchestrator gets):
   ```sql
   SELECT ef.id, ef.fact_type, ef.fact_text, ef.evidence_quote, ef.citation_span,
          ef.confidence, ef.extraction_model, ef.document_id, d.published_at
     FROM public.extracted_facts ef
     JOIN public.documents d ON d.id = ef.document_id
    WHERE ef.asset_id = $1
      AND ef.document_id = ANY($2::uuid[])
      AND d.published_at <= $3        -- reference_assessment_date
    ORDER BY ef.confidence DESC, d.published_at DESC
    LIMIT 200;
   ```
   200 is the live orchestrator's Tier-2 limit (per `bulk_orchestrator_run.md` step 3) — match it.
3. Asset-document link verdicts (pass-2 verified only, to mirror the live ensemble's input filter):
   ```sql
   SELECT ad.document_id, ad.link_type, ad.is_material, ad.pass2_verdict, ad.pass2_confidence
     FROM public.asset_documents ad
    WHERE ad.asset_id = $1
      AND ad.document_id = ANY($2::uuid[])
      AND ad.pass2_verdict IN ('confirmed', 'tentative_confirmed')
    LIMIT 50;
   ```

If any of these queries returns zero, log a warning and continue — the skill can still reason from the document text, but evidence_quality will be downgraded.

### Step 2 — Optional sub-skill calls

If `enable_subskills = true`:

1. **P1 (`analyze-fda-approval-prospects`)** — call for trial forensics + AdCom + label + CMC analysis. Pass:
   - `drug_name`, `indication`, `company_ticker`, `cik` from asset metadata
   - `catalyst_date_or_window = reference_assessment_date` so P1 doesn't peek forward
   - `mode = "evaluative"` if `catalyst_date - reference_assessment_date <= 60 days`, else `"forward_looking"`
   - `clinical_trial_ids` if extractable from `extracted_facts` (any fact_type `clinical_trial_id`)
   - **Important:** P1's `output_dir` MUST be a per-case subfolder (`<output_dir>/<key>/p1/`) so concurrent backtest invocations don't collide.
   - Capture P1's output: `(p_low, p_mid, p_high)`, trial_forensics list, AdCom risk, label risk, CMC risk, assumption_ledger.
2. **U3 (`compare-to-historical-precedents`)** — call for K-NN against historical FDA outcomes. Pass:
   - `candidate_id = asset.ticker`, `profile = "binary_catalyst"`, the trial fingerprint from P1.
   - **Important:** to avoid leakage, U3 must filter precedents to those with `outcome_date < reference_assessment_date`. Pass `as_of = reference_assessment_date` so the K-NN ignores future-dated neighbours.

If P1 or U3 errors, record the failure in `_meta.subskill_errors` and continue with degraded confidence (widen `p_high - p_low` by +5pp per missing sub-skill).

### Step 3 — Single Opus 4.7 reasoning pass (the core)

Compose ONE Anthropic Messages call to `claude-opus-4-7` with:

- **System prompt**: see `prompts/system.md` (loaded inline below).
- **User prompt**: structured payload with asset metadata, surviving documents (full text or document IDs + retrieval hints — the routine driver decides based on token budget), pre-extracted facts, P1/U3 outputs.
- **Max tokens**: 16384 (room for hypothesis enumeration + pre-mortem + structured JSON output).
- **Tools**: none for v0 (we want a single deterministic-shape JSON output; tool-use can come in v1 once the contract is stable).
- **Temperature**: 0.3 (some variability for hypothesis enumeration; not 0 because perfect determinism collapses the space, not 0.8 because the orchestrator's ensemble already covered that).
- **Cache**: enable prompt caching for the system block + the asset metadata block (reuse across the 271-case sweep).

The expected response shape is the JSON output schema above, embedded in a single fenced JSON block. The skill parses the block and validates against the schema (see `helpers/validate_output.py`); if validation fails, retry once with an explicit "fix the schema" follow-up turn, then refuse.

The system prompt enforces the six-field thesis discipline (cf. U2 `compose-thesis-with-discipline`):
1. **Variant perception** — what does the market currently believe vs. our view?
2. **Preconditions** — what must be true for the thesis to hold?
3. **Kill criteria** — what observations would invalidate it?
4. **Expected return distribution** — `p_low/p_mid/p_high` with explicit assumption ledger.
5. **Time horizon with milestones** — what events between now and catalyst should move conviction?
6. **Sizing inputs** — conviction_pct, evidence_quality, asymmetry estimate, correlation to existing book.

If any field cannot be defended, the skill emits a structured refusal (NOT a low-confidence pass-through). Refusals are signal — they tell us the skill's discipline is working.

### Step 4 — Deterministic post-processing

1. **Calibrate conviction_pct from p_mid**: `conviction_pct = p_mid * 100`. If the skill wants a non-linear mapping it must justify in the assumption ledger; default is identity.
2. **Derive band**: mirror `runtime.derive_band(conviction_pct)` exactly. The eval harness expects band consistency.
3. **Compute evidence_quality**: weighted average of (a) document recency vs catalyst, (b) fact confidence mean, (c) sub-skill confidence (P1 spread inversely correlated). Formula in `helpers/evidence_quality.py`.
4. **Run leakage post-check**: re-confirm no cited document_id post-dates reference_assessment_date. If any does, refuse — that's a regression bug.

### Step 5 — Write outputs

1. Write `<output_dir>/<key>.json` (atomic via temp + rename).
2. Write `<output_dir>/<key>.reasoning.md` with the full Opus response prose for human review.
3. If `eval_case_id` is provided, append a one-line summary to `<output_dir>/_index.jsonl`:
   ```json
   {"eval_case_id":"...","asset_id":"...","conviction_pct":62.0,"thesis_direction":"long","cost_usd":0.41,"completed_at":"..."}
   ```
   The driver routine reads this index for batch scoring.

## Failure modes and recovery

| Failure | Detection | Skill behavior |
|---|---|---|
| Document set empty after leakage filter | Step 0 | Refuse with `insufficient_document_coverage`; do not invoke Opus. |
| `fda_assets` row missing | Step 1 | Refuse with `unknown_asset`; do not invoke Opus. |
| P1 fails | sub-skill error | Continue without P1; log to `_meta.subskill_errors`; widen `p_high - p_low` by +5pp. |
| U3 fails | sub-skill error | Continue without U3; log; widen spread by +5pp. |
| Opus returns malformed JSON | schema validate fails | One retry with corrective turn. If still fails, refuse with `output_validation_failed` and dump the raw response to `<output_dir>/<key>.raw.txt`. |
| Opus returns conviction_pct outside [0,100] | post-process check | Refuse with `conviction_out_of_range`. |
| Cited document not in input set | post-process check | Refuse with `hallucinated_citation` + dump. This is the highest-severity failure — we cannot tolerate fabricated evidence. |
| Anthropic API 429 / credit exhaustion | client error | Re-raise — the routine driver pauses the sweep and surfaces to operator. Do not silently retry forever. |
| `reference_assessment_date > today` | Step 0 sanity | Refuse with `future_reference_date`. |

No silent failures. Every degraded path produces an explicit ledger entry in `_meta`.

## Compliance with system invariants

- **Folder scope.** Reads from `public.documents`, `public.extracted_facts`, `public.asset_documents`, `public.fda_assets`, `public.eval_harness` (read-only). Writes only to `<output_dir>`. Never modifies `convergence_assessments`, `orchestrator_runs`, or any live-pipeline table — the sidecar comparison happens in the driver routine, not in this skill.
- **No leakage.** Step 0 + Step 4 leakage checks are mandatory and must pass for the skill to emit. The eval harness depends on this — a leakage bug would silently inflate the skill's measured Brier score and corrupt the comparison vs. the live orchestrator.
- **Atomic writes.** All outputs via temp + rename.
- **Confidence + source on every claim.** Each `key_facts` entry, each `cited_prose_blocks` entry, and each `assumption_ledger` row in `_meta` carries `confidence ∈ [0,1]` and a `source` (document_id or sub-skill name).
- **Probability is always a range.** `p_low/p_mid/p_high` are required; `conviction_pct` is derived from `p_mid` for harness compatibility but the spread is preserved.
- **Bounded runtime.** Target ≤ 90s per case (asset + facts + 1 Opus call + 2 optional sub-skills); hard cap 240s. The 271-case sweep should complete in ≤ 7 hours wall-clock at sequential pacing.
- **Bounded cost.** Target ≤ $0.50 per case ($136 for the full 271-case sweep). The Opus 4.7 call dominates; sub-skills add ≤ $0.10 each. Skill self-aborts the sweep if rolling per-case mean exceeds $1.00.

## Reference

- Output schema source-of-truth: `public.convergence_assessments` columns (see migration `20260512000000_v3_phase_4b_convergence_assessments_tier.sql` and the column list in this skill's tests).
- Eval harness loader: `orchestrator_runtime/eval_harness/gold_standard.py` (`HarnessCase`, `is_direction_correct`).
- Eval scoring: `orchestrator_runtime/eval_harness/metrics.py` (`calibration_curve`, `ranking_auc`, `aggregate`).
- Live orchestrator stages this skill consolidates: `orchestrator_runtime/runtime.py` Stage 1 (synthesis), `hypothesis.py` Stage 2, `premortem.py` Stage 3, `constitutional.py` Stage 7, `runtime.py:662` Stage 9.
- Sub-skills: `skills_v2/analyze-fda-approval-prospects/SKILL.md` (P1), `skills_v2/compare-to-historical-precedents/SKILL.md` (U3).
- Driver routine: `skills/skill_eval_replay.md` — drives this skill against the 271-case `eval_harness` and produces a comparison report vs. live orchestrator's `convergence_assessments` for the same `(asset_id, reference_assessment_date)`.
- Default model: `claude-opus-4-7` (matches PR #40, `orchestrator_runtime/client.py:31`).

## Future (not in v0)

- **Tool use**: enable Anthropic-side tool dispatch (e.g., `fetch_clinical_trial`, `query_federal_register`) so the skill can pull primary sources mid-reasoning rather than relying on pre-extracted facts. Requires the live MCP-on-Anthropic-tools wiring.
- **Live sidecar**: a `skill_orchestrator_sidecar` routine that, for each new `orchestrator_runs` row marked completed, runs this skill in parallel and writes the result to a new `skill_assessments` table. Enables A/B evaluation on live traffic, not just historical backtest.
- **Multi-stage variant**: if the eval harness shows a single-call skill underperforms the multi-stage chain on a specific failure mode (e.g., pre-mortem omissions), spin off a `assess-fda-binary-catalyst-v1` that splits Step 3 into two Opus calls: (a) synthesis + hypothesis, (b) pre-mortem + extraction.
