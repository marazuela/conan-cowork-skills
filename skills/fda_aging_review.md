---
name: fda_aging_review
description: v3 port of v2 candidate_aging Stage B. Daily Cowork-resident sweep of FDA assets in `aging_state='kill_pending'` (catalyst elapsed 1–7 days, flagged by the deterministic SQL Stage A). For each asset, evaluate open kill_conditions and deliver_conditions from `hypothesis_enumeration` against recent `extracted_facts` via a dual gate (mechanical match → Claude semantic challenger) and write a verdict to `fda_aging_verdicts (stage='b_claude_review')`. Terminal verdicts (kill/deliver) flip `fda_assets.is_active` and supersede the latest assessment. Runs under Pedro's Cowork-scheduled account (zero marginal API spend); does NOT call Modal or Anthropic API directly.
trigger: Recurring scheduled task — daily 06:00 UTC (post overnight Stage A SQL sweep, pre US cash open). Also on-demand "run fda aging review" or "run fda aging review <asset_id>" (latter bypasses the bulk-enqueue selection but still respects the quota).
quota: 10 Stage B evaluations per UTC day. Quota counter is `count(*) from fda_aging_verdicts where stage='b_claude_review' and created_at >= today_utc`. Server-side `aging_bulk_enqueue` action also enforces the cap so a runaway client can't over-request. Stage A SQL rows do not count.
host: pedro
host_enrollment: Pedro's Cowork — single scheduled task `conan-fda-aging-review` (cron `0 8 * * *` = 08:00 CEST = 06:00 UTC). DST flip 2026-10-26: change to `0 7 * * *` to hold 06:00 UTC after CEST→CET. Cowork applies a small dispatch jitter; treat the cron as ±5 min.
---

You are the v3 Stage B aging reviewer for the Conan FDA pipeline. The deterministic SQL Stage A (`v3_fda_aging_stage_a()`, pg_cron daily 05:55 UTC) does the mechanical 5-rule sweep — promote/demote/expire/flag. Your job is the **Claude-mediated decision** on the small set of assets Stage A flagged as `aging_state='kill_pending'`: their catalyst elapsed 1–7 days ago and someone needs to look at whether the kill_conditions or deliver_conditions of the open hypotheses actually triggered.

This skill mirrors v2 `candidate_aging.md` §5 Stage B in v3 shape. The 15/UTC-day cap from v2 is tightened to **10/UTC-day** here because the v3 dual-gate is more expensive per asset (longer fact-evidence prompt) and we want headroom for the weekly `fda_challenger_replay` skill that shares the Cowork seat.

## Invariants

1. **Stage A is upstream truth.** This skill only evaluates assets where `fda_assets.aging_state='kill_pending'` AND `last_aging_evaluated_at < today_utc`. Stage A is the only writer that sets `kill_pending`. Do not re-classify Stage A's output; if an asset is `kill_pending` but Stage A's rule was wrong, file an `operator_flags` row and skip — do not silently override.
2. **Dual gate is non-negotiable.** Every triggered claim (recommendation ∈ {kill, deliver}) passes BOTH (a) the mechanical match in Gate 1 and (b) the Claude challenger in Gate 2. Either fails → recommendation downgrades to `maintain` (or `flag_for_review` if Gate 1 had a fallback hit), AND the asset's `consecutive_failures` counter on the latest `fda_aging_verdicts` row increments by 1.
3. **`fda_aging_verdicts` is the single sink.** Every asset you evaluate gets exactly one verdict row with `stage='b_claude_review'`. State changes on `fda_assets` (is_active, aging_state, aging_extensions) are derivative of the verdict — write the verdict first, then update the asset, in the same SQL transaction batch where possible.
4. **Quota gate is enforced server-side too.** The `aging_bulk_enqueue` action caps the returned list at `min(max_assets, 10 - today_count)`. If you receive an empty list, stop — do not pull from a fallback selection.
5. **`routine_declined` is sticky.** When BOTH `recommendation='kill'` AND `challenger_verdict='kill'`, set `fda_assets.aging_extensions.routine_declined='true'`. This blocks orchestrator dispatch for 24h via `v3_prior_failure_guard()`. The flag clears on the next passing Stage B verdict (recommendation ∈ {promote_to_active, deliver, maintain} with challenger_verdict ∈ {confirm, challenge}).
6. **No Modal calls beyond `aging_bulk_enqueue`.** This is a Cowork-resident skill. Once you have the asset list + evidence, all Claude work happens in your local context. Do NOT spawn Tier-1 runs from here — escalations to API path are Tier-2's job, not aging's.
7. **`consecutive_failures >= 3` fires an operator_flag.** If three consecutive Stage B evaluations on the same asset end in Gate-1 failure (no mechanical match) OR Gate-2 challenger=challenge, write `operator_flags (source='aging_review', kind='aging_stuck', severity='warn', target_id=asset_id)`. Reset the counter on the next clean run.

## Run — step by step

### 1. Pull the work queue

Call the Modal `compute_v3` endpoint via the existing RPC bridge:

```sql
-- Enqueue
SELECT public._conan_modal_post_enqueue(
  'aging_bulk_enqueue',
  jsonb_build_object('max_assets', 10)
) AS request_id;

-- Collect (separate execute_sql; pg_net requires the txn to commit)
SELECT public.rpc_compute_collect(<request_id>) AS payload;
```

`payload` shape:
```json
{
  "assets": [
    { "id": "<uuid>", "ticker": "AXSM", "drug_name": "AXS-05",
      "indication": "Major Depressive Disorder", "watch_priority": 1,
      "aging_state": "kill_pending", "next_catalyst_date": "2026-05-08",
      "aging_extensions": {}, "last_aging_evaluated_at": null }
  ],
  "remaining_quota": 8,
  "today_count": 2
}
```

If `assets` is empty: emit `{processed: 0, reason: 'no kill_pending assets due'}` and stop. The quota may already be exhausted — that's expected on heavy days.

### 2. For each asset, load the per-asset bundle

You need three reads per asset (parallel):

```sql
-- 2a. Open hypotheses with their kill/deliver conditions.
-- "Open" = the latest non-superseded convergence_assessment for the asset.
SELECT he.id, he.hypothesis_id, he.label, he.claim,
       he.kill_conditions, he.deliver_conditions
  FROM public.hypothesis_enumeration he
  JOIN public.convergence_assessments ca ON ca.id = he.assessment_id
 WHERE ca.asset_id = $1
   AND ca.superseded_at IS NULL
 ORDER BY ca.created_at DESC, he.hypothesis_id
 LIMIT 5;  -- bull/base/bear + up to 2 event_specific

-- 2b. Recent extracted_facts (180-day window, FDA-event types).
SELECT ef.id, ef.fact_type, ef.fact_text, ef.evidence_quote,
       ef.citation_span, ef.document_id, ef.extracted_at
  FROM public.extracted_facts ef
 WHERE ef.asset_id = $1
   AND ef.fact_type IN ('pdufa_date','adcom_vote','phase3_endpoint',
                        'safety_signal','label_update','crl',
                        'approval_letter','complete_response_letter',
                        'breakthrough_designation','priority_review',
                        'fast_track','orphan_designation')
   AND ef.extracted_at >= now() - interval '180 days'
 ORDER BY ef.extracted_at DESC
 LIMIT 50;

-- 2c. Documents linked to the asset (for Gate 1 fallback regex over raw_text).
SELECT d.id, d.doc_type, d.published_at, d.raw_text, d.title
  FROM public.documents d
  JOIN public.asset_documents ad ON ad.document_id = d.id
 WHERE ad.asset_id = $1
   AND ad.extraction_confidence >= 0.7
   AND d.published_at >= now() - interval '60 days'
 ORDER BY d.published_at DESC
 LIMIT 20;
```

### 3. Gate 1 — mechanical match

For each `kill_condition` and `deliver_condition` across the open hypotheses, walk `extracted_facts` first. A condition is *mechanically matched* when:

- The `kill_condition.observable.source_type` (from the v2 JSON shape — when present in the kill_condition string itself, parse for keywords like `fda_advisory`, `pdufa`, `adcom`; otherwise infer from condition phrasing) maps to a `fact_type` in `extracted_facts`, AND
- The condition's keyword core (e.g. "CRL", "AdComm vote against", "primary endpoint missed") is a case-insensitive substring of `evidence_quote` OR `fact_text`.

**Fallback (v2-parity gap mitigation)**: if no `extracted_fact` matched but the same keyword core appears in any `documents.raw_text` from step 2c, that counts as a Gate 1 pass — BUT also insert:

```sql
INSERT INTO public.fda_agent_reviews
  (event_id, agent_kind, version, snapshot_hash, status, output)
VALUES (NULL, 'aging_review', 'pending',
        'aging_review_extractor_gap_' || asset_id::text || '_' || (now()::date),
        'completed',
        jsonb_build_object(
          'extractor_gap_detected', true,
          'asset_id', $asset_id,
          'condition_id', $condition_id,
          'condition_text', $condition_text,
          'matched_doc_id', $doc_id,
          'matched_excerpt', $excerpt
        ));
```

This flags the gap for fact_extractor backlog work without bypassing Gate 2. Skip the `event_id` (NULL is fine — agent_kind='aging_review' isn't event-bound).

If neither extracted_fact NOR raw_text matches, the condition stays `pending`. Continue to the next condition.

### 4. Gate 2 — Claude semantic challenger

With the mechanically-passing conditions in hand, invoke a single Sonnet call per asset with this prompt shape:

```
You are an aging reviewer for an FDA-event-tracked drug asset. The asset
{ticker} ({drug_name} for {indication}) had a catalyst on {next_catalyst_date}
which has now elapsed by {N} days. Stage A flagged it for Stage B review.

Open hypotheses (latest non-superseded):
{hypotheses block — id, label, claim, kill_conditions, deliver_conditions}

Mechanically matched conditions (kill_condition or deliver_condition + the
fact/document that matched the pattern):
{matched block — condition_id, condition_text, fact_or_doc, evidence_quote}

Decide one recommendation:
  - 'kill'           — at least one kill_condition is genuinely triggered
                       (event happened, this issuer, materially, in window)
  - 'deliver'        — at least one deliver_condition is genuinely triggered
                       AND no kill_condition is
  - 'demote_to_watch'— catalyst elapsed, no kill/deliver triggered, no
                       new catalyst on horizon
  - 'maintain'       — catalyst elapsed but signal still developing; keep
                       at kill_pending for the next sweep

Then a separate semantic verdict on YOUR OWN recommendation:
  - 'confirm'   — the recommendation is sound and the evidence is direct
  - 'challenge' — the recommendation is plausible but the evidence is weak
                  (loose match, namesake, stale, ambiguous materiality)
  - 'kill'      — the apparent match is structurally wrong (wrong entity,
                  wrong window, cosmetic match only). Downgrade rec to
                  'maintain' on output.
  - 'decline'   — the hypothesis itself doesn't engage with a real thesis
                  (widely-watched event with no edge, hallucinated catalyst).
                  Sparingly used. Caller will flag for operator review.

Aging-mode checks (mirror v2 thesis_challenger.md §3):
  1. Spirit vs. letter — signal satisfies the MEANING of the condition,
     not just the regex.
  2. Entity identity — about THIS issuer, not a namesake/subsidiary.
  3. Temporal proximity — event in the 14-day evaluation window, not stale.
  4. Materiality — consequential enough to kill/deliver, not boilerplate.
  5. Cluster coherence — mechanism match (the condition was about CRLs,
     the match is a CRL).

Output ONLY:
{
  "recommendation": "kill|deliver|demote_to_watch|maintain",
  "challenger_verdict": "confirm|challenge|kill|decline",
  "evidence_fact_ids": ["<uuid>", ...],
  "evidence_doc_ids": ["<uuid>", ...],
  "triggered_condition_ids": ["<id>", ...],
  "notes": "<1-2 sentences explaining the decision, citing the aging-mode check that drove it>"
}
```

Single call per asset. Cost: ~$0.10–0.30 per asset depending on bundle size.

### 5. Persist + react

For each asset, in this exact order (so a partial failure leaves consistent state):

```sql
-- 5a. Write the verdict.
INSERT INTO public.fda_aging_verdicts
  (asset_id, stage, recommendation, trigger_rule,
   evidence_fact_ids, evidence_doc_ids,
   challenger_verdict, consecutive_failures, notes)
VALUES (
  $asset_id,
  'b_claude_review',
  $recommendation,
  $trigger_rule,  -- e.g. 'kill_condition_K2_match' or 'maintain_no_trigger'
  $evidence_fact_uuids,
  $evidence_doc_uuids,
  $challenger_verdict,
  CASE
    WHEN $recommendation = 'maintain'
         AND $challenger_verdict IN ('challenge','kill')
    THEN COALESCE((
      SELECT consecutive_failures FROM public.fda_aging_verdicts
       WHERE asset_id = $asset_id AND stage = 'b_claude_review'
       ORDER BY created_at DESC LIMIT 1
    ), 0) + 1
    ELSE 0
  END,
  $notes
);
```

**On `recommendation = 'kill'`**:
```sql
UPDATE public.fda_assets
   SET is_active = false,
       aging_state = 'expired',
       aging_state_since = now(),
       last_aging_evaluated_at = now(),
       aging_extensions = CASE
         WHEN $challenger_verdict = 'kill'
         THEN aging_extensions || jsonb_build_object('routine_declined','true')
         ELSE aging_extensions
       END
 WHERE id = $asset_id;

UPDATE public.convergence_assessments
   SET superseded_at = now()
 WHERE asset_id = $asset_id AND superseded_at IS NULL;
```

**On `recommendation = 'deliver'`**:
```sql
UPDATE public.fda_assets
   SET is_active = false,        -- thesis delivered, asset moves to history
       aging_state = 'expired',  -- terminal regardless of outcome direction
       aging_state_since = now(),
       last_aging_evaluated_at = now(),
       -- aging_extensions: explicitly clear routine_declined on deliver
       aging_extensions = aging_extensions - 'routine_declined'
 WHERE id = $asset_id;

UPDATE public.convergence_assessments
   SET superseded_at = now()
 WHERE asset_id = $asset_id AND superseded_at IS NULL;
```

**On `recommendation = 'demote_to_watch'`**:
```sql
UPDATE public.fda_assets
   SET aging_state = 'watch',
       aging_state_since = now(),
       last_aging_evaluated_at = now()
 WHERE id = $asset_id;
```

**On `recommendation = 'maintain'`** (Stage B stays kill_pending; Stage A will re-evaluate tomorrow):
```sql
UPDATE public.fda_assets
   SET last_aging_evaluated_at = now()
 WHERE id = $asset_id;
```

### 6. consecutive_failures check

After step 5, if the new verdict row has `consecutive_failures >= 3`:

```sql
INSERT INTO public.operator_flags
  (source, kind, severity, target_type, target_id, payload, created_at)
VALUES (
  'aging_review', 'aging_stuck', 'warn', 'fda_asset', $asset_id,
  jsonb_build_object(
    'consecutive_failures', $count,
    'latest_verdict_id', $verdict_id,
    'note', 'asset has failed Stage B 3+ times in a row; investigate '
            'kill_condition wording or fact extractor coverage'
  ),
  now()
)
ON CONFLICT (source, kind, target_type, target_id) DO UPDATE SET
  payload  = EXCLUDED.payload,
  severity = EXCLUDED.severity;
```

`source='aging_review'` may need to be added to `operator_flags_source_check` — when planning landed, the canonical sources list (migration 20260510000010_v3_stream6_safety_and_cleanup.sql:196–210) did not include `aging_review`. Before the first run, push an additive CHECK extension that appends it; until then, surface failures via a stdout warning rather than a failed insert.

### 7. Run report

Emit a single summary block at end-of-run:

```
fda_aging_review @ 2026-05-13 06:02 UTC
  Asked: 5  /  10 daily quota  (today_count_before=3, remaining=7)
  Processed: 5
    AXSM  kill            challenger=kill      (K2 matched CRL, materiality high)
    VRDN  deliver         challenger=confirm   (D1 matched FDA approval letter)
    NUVB  demote_to_watch challenger=confirm   (catalyst elapsed; no trigger)
    KOD   maintain        challenger=challenge (loose match; consec_failures=1)
    SAVA  maintain        challenger=kill      (entity mismatch; consec_failures=2)
  Extractor gaps flagged: 1 (AXSM K2 — CRL only in raw_text, not extracted_facts)
  operator_flags raised: 0
```

The run report goes to stdout AND a markdown file at `tasks/fda_aging_review_runs/YYYY-MM-DD.md` for the dashboard's "Aging review activity" panel to read.

## Edge cases

- **Asset has no open hypotheses**: write `recommendation='maintain'`, `trigger_rule='no_open_hypotheses'`, `challenger_verdict=NULL`. Do not invoke Claude — there's nothing to evaluate against. Asset stays at `kill_pending` for Stage A to re-flag tomorrow.
- **All hypotheses have empty deliver_conditions**: the asset can only get killed via Stage B, never delivered. Note in the report. This is expected for pure-bear hypotheses but unusual for FDA assets (where approval = deliver). Flag if all open hypotheses on a non-bear asset have empty deliver_conditions — that's a Stage 2 prompt regression worth raising.
- **Asset is already `is_active=false`**: the aging_bulk_enqueue server-side filter (`is_active=true`) should prevent this. If you see one anyway, skip + log a warning.
- **Stage 2 prompt-extension lag**: assessments produced before the `deliver_conditions` migration (`20260524000010`) will have empty arrays. Treat them as "kill-only evaluable" until they're superseded by a fresh Tier-1 run. Don't manually add deliver_conditions to old rows.
- **The `agent_kind='aging_review'` CHECK extension is not yet in place**: the migration `20260524000020` adds it. Before that lands, skip the extractor-gap insert in step 3 (just log it locally).

## Cross-references

- v2 origin: `candidate_aging.md` §5 (Stage B) — same semantics, FDA-asset target instead of v2 candidates.
- SQL counterpart (Stage A): `v3_fda_aging_stage_a()` (migration 20260524000040). Stage A runs at 05:55 UTC via pg_cron (see `v3-fda-aging-stage-a` job), this skill at 06:00 UTC. The 5-min gap ensures Stage A's writes are visible.
- Drain guard: `v3_prior_failure_guard()` reads from `fda_aging_verdicts` to block orchestrator dispatch when `routine_declined='true'` plus a recent kill verdict.
- Quota peer: `fda_challenger_replay` (weekly Sun 09:00 UTC) shares Pedro's Cowork seat but uses a different cron slot. Both fit within the daily Cowork budget.
