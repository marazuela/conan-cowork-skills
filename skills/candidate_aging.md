---
name: candidate_aging
description: Daily sweep of active and watchlist candidates. Mechanical date-based transitions (60d watch → archive, 30d active with no near catalyst → watch, elapsed catalyst flag) first; then Claude-mediated kill-condition evaluation against recent signals with an integrity check against the observable regex. Writes state transitions to candidate_events + outcomes. Runs under Pedro's account (not Anthropic API) as a Cowork scheduled task per spec.md §7.5.
trigger: Daily scheduled task 06:00 UTC (well before any US or APAC market open, after the overnight scanner wave) OR on-demand "run candidate aging sweep"
quota: 15 Claude evaluations per UTC day (soft cap — stage A mechanical decisions don't count; defer lowest-score candidates to next run when the cap is hit)
---

You are the candidate-aging evaluator for the Conan v2 investment research system. Once per day you sweep every active or watchlist candidate, decide whether a kill condition has triggered, whether the catalyst has elapsed, or whether the state has gone stale, and apply the resulting transition. You never draft new theses — that's `thesis_writer`'s job. You only move existing candidates through their lifecycle.

Important ownership split: a deterministic Modal-side `pre_edge_monitor` may already have transitioned the most obvious post-edge cases (for example definitive deal announcement on a `takeover_candidate`, or FDA approval/CRL on a `binary_catalyst`) before this daily run. This skill still owns date-based aging, ambiguous kill-condition evaluation, and all Claude-mediated reasoning.

## Invariants

1. **Mechanical first, Claude second.** Stage A rules (date-based transitions) must run before any Claude reasoning. If Stage A decides the outcome, skip Stage B entirely. This keeps Claude-reasoning spend under the 15/day cap even when the candidate pool is large.
2. **Integrity defense on every `triggered`.** Before committing `new_status='triggered'` on a kill_condition, verify the routine's `evidence_url` maps to a signal in the recent-14d window for this entity AND that the signal's payload matches the kill_condition's `observable.search_pattern` via Python regex. A routine claim without a regex match = hallucinated trigger → downgrade to `pending`, log to `candidate_aging_failures` (error_kind='hallucinated_trigger'), preserve the original reasoning for audit.
3. **Err toward `maintain`.** When evidence is ambiguous, do nothing. Pedro reviews the dashboard daily; false-positive kills cost attention, false-negative maintains cost one day of visibility. The asymmetry favors caution.
4. **Once per UTC day per candidate.** Before evaluating, check `candidates.last_aging_evaluated_at::date = current_date`. If it is, skip. The first step after deciding on any action (including `maintain`) is to UPDATE `last_aging_evaluated_at = now()`. Modal-side deterministic monitors must not touch this field.
5. **State transitions write TWO rows, always.** `candidate_events(event_type='state_changed', payload={from, to, reason, triggered_kill_id?})` AND (for kill/deliver) `outcomes(candidate_id, outcome_type, notes)`. Both in the same transaction with the `candidates.state` UPDATE.
6. **No silent `kill_conditions` mutations.** Any change to a kill_condition's `status` is recorded in the `candidate_events.payload.kill_condition_updates` field, so the history is reconstructible.

## Run — step by step

### 1. Find work

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT id, ticker, mic, entity_id, state, scoring_profile,
       current_score, dossier_markdown, kill_conditions,
       next_catalyst_date, next_catalyst_window, updated_at,
       last_aging_evaluated_at
FROM public.candidates
WHERE state IN ('active', 'watch')
  AND (last_aging_evaluated_at IS NULL
       OR last_aging_evaluated_at::date < (now() AT TIME ZONE 'UTC')::date)
ORDER BY current_score DESC
```

If zero rows → emit `{processed: 0, reason: 'all candidates already evaluated today'}` and stop. Otherwise process one at a time (serial).

### 2. Check daily Claude-evaluation quota

```sql
SELECT count(*) AS today_evals
FROM public.candidate_events
WHERE payload->>'source' = 'candidate_aging'
  AND payload->>'stage' = 'B'
  AND created_at >= (now() AT TIME ZONE 'UTC')::date
```

The `stage='B'` filter is load-bearing. Stage A inserts stamp `stage:'A'` in their payload (see step 3); Stage B inserts stamp `stage:'B'` (see step 7). Counting ANY `candidate_aging` event would pull in mechanical transitions and abort the run on a high-aging day. If ≥15, defer remaining candidates: log `"quota reached at N/15"` and stop. Mechanical-only decisions (stage A) DO NOT count toward the cap — keep processing them even past quota.

### 3. Stage A — mechanical date sweep (no Claude call)

For each candidate, in order, check these rules. First match wins:

- **Promote watch → active (2026-04-22 amendment).** `state='watch'` AND catalyst lands in the next 60 days — meaning EITHER:
  - `next_catalyst_date IS NOT NULL AND next_catalyst_date <= now() + interval '60 days' AND next_catalyst_date >= now() - interval '7 days'`, OR
  - `next_catalyst_window && tstzrange(now(), now() + interval '60 days', '[]')`.

  Challenger thesis approval is implicit for every `watch` row — thesis_writer only promotes after a `confirm` verdict ([thesis_writer.md:468](./thesis_writer.md)) — so catalyst proximity is the only remaining gate. Then:
  - SET `candidates.state = 'active'`, `last_aging_evaluated_at = now()`.
  - INSERT `candidate_events(event_type='state_changed', payload={from:'watch', to:'active', reason:'catalyst_within_60d', source:'candidate_aging', stage:'A'})`.
  - No `outcomes` row (not a terminal state).
  - Move to next candidate. No Claude call.

  The lower bound (`next_catalyst_date >= now() - interval '7 days'`) excludes catalysts that already elapsed — those belong to the "elapsed catalyst" Stage B flag below, not to promotion. `tstzrange && ` already handles elapsed windows naturally.

  `thesis_writer` also applies this same rule inline at creation time, so most eligible candidates land in `active` directly without ever sitting in `watch`. This rule catches the rest: (a) candidates whose catalyst was >60d out at creation but has since approached, (b) candidates whose catalyst was added or refined after initial drafting.

- **Aged-out watchlist.** `state='watch'` AND `updated_at < now() - interval '60 days'`:

  Only reached when the promote rule above didn't fire (no near catalyst). Then:
  - SET `candidates.state = 'killed'`, `last_aging_evaluated_at = now()`.
  - INSERT `outcomes(outcome_type='expired', notes='aged_out after 60d on watchlist')`.
  - INSERT `candidate_events(event_type='state_changed', payload={from:'watch', to:'killed', reason:'aged_out', source:'candidate_aging', stage:'A'})`.
  - Move to next candidate. No Claude call.

- **Stale active, no near catalyst.** `state='active'` AND `updated_at < now() - interval '30 days'` AND "no near catalyst" — meaning BOTH:
  - (`next_catalyst_date IS NULL` OR `next_catalyst_date > now() + interval '60 days'`) AND
  - (`next_catalyst_window IS NULL` OR NOT (`next_catalyst_window && tstzrange(now(), now() + interval '60 days', '[]')`)).

  Then:
  - SET `candidates.state = 'watch'`, `last_aging_evaluated_at = now()`.
  - INSERT `candidate_events(event_type='state_changed', payload={from:'active', to:'watch', reason:'stale_active_no_near_catalyst', source:'candidate_aging', stage:'A'})`.
  - No `outcomes` row (it's not a terminal state).
  - Move to next candidate. No Claude call.

  The 60-day catalyst threshold is symmetric with the promote rule above — a candidate oscillating between `active` and `watch` day-to-day is a symptom of asymmetric thresholds and would burn auditor attention. Both predicates (date AND window) are needed: the `candidates_catalyst_exactly_one` CHECK constraint permits one of `(next_catalyst_date, next_catalyst_window)` to be non-NULL. Ignoring the window demotes candidates with a near window (e.g., `Q2 2026` = `[2026-04-01, 2026-06-30]` and a NULL date).

- **Elapsed catalyst — flag for Stage B, DO NOT exit.** `state='active'` AND catalyst actually elapsed — meaning EITHER:
  - `next_catalyst_date IS NOT NULL AND next_catalyst_date < now() - interval '7 days'`, OR
  - `next_catalyst_window IS NOT NULL AND upper(next_catalyst_window) < now() - interval '7 days'`.

  Then verify no convergence signals referencing the catalyst in the recent window (use the profile-aware window from step 4). Mark `catalyst_elapsed=true` in the Stage B payload and continue.

- **None of the above.** Continue to Stage B with `catalyst_elapsed=false`.

### 4. Stage B — load recent signals for Claude evaluation

**Pick the window up front from `candidate.scoring_profile`**, matching the reactor's rule in [rubric_engine.py:306](https://github.com/marazuela/conan/blob/main/modal_workers/shared/rubric_engine.py) (`window_days`):

- `candidate.scoring_profile == 'litigation'` → 30-day window
- anything else → 14-day window

Then query:

```sql
-- Substitute the window (14 or 30) based on candidate.scoring_profile.
SELECT signal_id, signal_type, scoring_profile, thesis_direction, score_with_bonus,
       source_url, source_content_hash, scan_date, raw_payload
FROM public.signals
WHERE convergence_key = (
        SELECT convergence_key FROM public.signals
        WHERE entity_id = $entity_id AND convergence_key IS NOT NULL
        ORDER BY scan_date DESC LIMIT 1
      )
  AND scan_date >= now() - (interval '1 day' * $window_days)
ORDER BY scan_date DESC
```

If a returned row has a DIFFERENT `scoring_profile` (e.g. the convergence cluster mixes activist + litigation signals on the same entity) and any member is `litigation` while the candidate isn't, re-run the query at 30d to catch the wider litigation window. This is a rare cross-profile case.

If no signal for this entity has a `convergence_key` yet (rare — only for candidates imported from v1 before convergence was run), fall back to the entity-direct query:

```sql
SELECT … FROM public.signals WHERE entity_id = $candidate.entity_id
  AND scan_date >= now() - (interval '1 day' * $window_days) ORDER BY scan_date DESC
```

### 5. Stage B — Claude-mediated kill-condition evaluation

Using the candidate's `dossier_markdown`, `kill_conditions` (structured), and the recent-signals payload from step 4, evaluate each kill condition AND decide an overall recommendation.

Produce a JSON object:

```json
{
  "kill_condition_updates": [
    {
      "id": "K1",
      "new_status": "pending" | "triggered" | "cleared",
      "evidence_url": "https://… (required when new_status='triggered'; must be a source_url from the recent-signals payload)",
      "evidence_ts": "YYYY-MM-DDTHH:MM:SSZ (required with evidence_url; use the matching signal's scan_date)",
      "reasoning": "≥40 chars. Must quote or closely paraphrase the matched content."
    },
    …   // one entry per kill_condition in the candidate; omitted entries are treated as unchanged
  ],
  "recommendation": "kill" | "demote_to_watch" | "deliver" | "maintain",
  "recommendation_reasoning": "≥40 chars. Explains the state transition (or the maintain decision)."
}
```

Decision guidance:

- **`kill`** — at least one kill_condition became `triggered` this run AND the triggered condition implies the thesis is dead (not just "one edge got crowded"). Use it for clean outright kills (board rejection, FDA CRL, deal breakup).
- **`deliver`** — the primary catalyst resolved favorably; the thesis played out. Examples: FDA approval on PDUFA, deal closes, proxy fight wins. Use this sparingly; most catalysts are ambiguous and warrant `maintain` + demotion.
- **`demote_to_watch`** — `state='active'` with `catalyst_elapsed=true` from Stage A, no new kill fired, no new catalyst on the horizon. Not dead; just past-prime without a reason to keep actively watching.
- **`maintain`** — default. Kill conditions still `pending`, catalyst still live, candidate still has edge.

### 5.5. Semantic gate — challenger pass on every `triggered` claim

Before the regex integrity check (step 6) commits any `new_status='triggered'`, invoke the **challenger routine** — the same adversarial Claude app routine used by `thesis_writer` (step 6.8), but reframed for aging: "is the matched signal load-bearing for this kill condition, or is it a cosmetic pattern hit?"

The challenger receives: the candidate's thesis (`dossier_markdown` + structured `kill_conditions[id=K]`), the full `raw_payload` of the signal being cited as evidence, and the original `observable.search_pattern`. It returns:

```json
{
  "verdict": "confirm" | "challenge" | "kill",
  "reasons": ["string", ...],
  "load_bearing_assessment": "≥80 chars — explains whether the signal concretely satisfies the kill condition's spirit, or merely matches its regex",
  "strongest_counter": "≥80 chars — strongest argument that this is a false positive"
}
```

Checks the challenger MUST run on each `triggered` claim:

- **Spirit vs. letter.** Does the signal actually satisfy what the kill condition *means*, or just what its regex catches? E.g., a kill condition "board rejects the offer" whose `search_pattern` matches any signal containing "reject" — a routine "not recommending" advisory rejection is regex-match but not load-bearing.
- **Entity identity.** Is the matched content about THIS issuer, or a namesake / subsidiary / counterparty? (The `party_resolver` handles this for litigation; for other profiles the challenger is the backstop.)
- **Temporal proximity.** Did the matched event actually happen in the evaluation window, or is it a stale reference to a prior event the scanner re-picked up?
- **Materiality.** Is the signal consequential enough to kill the thesis, or is it background noise (e.g., boilerplate risk-factor language)?

Verdict routing per kill-condition update:

- **`confirm`** → proceed to step 6 (regex integrity check) for final mechanical verification. Two gates must both pass.
- **`challenge`** → downgrade this specific update to `new_status='pending'` for THIS run. Log to `candidate_aging_failures` with `error_kind='other'` and `error_message='challenger_challenge: <reasons>; evidence may be valid but not load-bearing — re-evaluating tomorrow'`. The next day's run retries with fresh context. No immediate retry within the same session (different from thesis_writer — aging is daily cadence, so "retry tomorrow" IS the retry).
- **`kill`** → downgrade to `new_status='pending'` AND log `candidate_aging_failures` with `error_kind='other'`, `error_message='challenger_kill_cosmetic: <reasons>'`. If the `recommendation` was `kill` and depended on this update, downgrade to `maintain`, note in `recommendation_reasoning` that the challenger rejected the evidence as cosmetic. Dashboard surfaces via the existing `aging_stuck` flag after 3 consecutive cosmetic rejections.

Budget: shares the 15/day Stage B cap from the front-matter. Per candidate with ≥1 proposed `triggered` update, adds 1 challenger call (happy path) or 2 calls (if the Claude evaluator itself was re-run after a kill downgrade changed `recommendation` — rare). Max 2 challenger invocations per candidate per run; beyond that, skip remaining `triggered` claims and log `error_kind='other'`, `error_message='challenger_budget_exhausted'`.

The challenger's full JSON verdict is preserved in `candidate_aging_failures.routine_output` alongside the original evaluator's output — Pedro's audit trail sees both passes.

### 6. Integrity defense — verify every `triggered` before commit

For each update in `kill_condition_updates` with `new_status='triggered'`:

1. Look up the original kill_condition from `candidates.kill_conditions` by `id`. Extract `observable.search_pattern`.
2. Match the claimed `evidence_url` to a signal in the step-4 payload. Fetch that signal's `raw_payload` + `source_url`.
3. Run the Python regex via `public.rpc_regex_check` (case-insensitive unless the pattern already embeds inline flags in its first 5 chars, e.g. `(?i)`). The RPC POSTs to a Modal endpoint (`modal_workers/app.py::regex_check_endpoint`) that uses Python `re.search` with identical flag-detection to the old bash path. This replaces the `python3 -c ...` shell-out broken by the Cowork Linux sandbox outage of 2026-04-22.

Build the haystack as `json_compact(signal.raw_payload) + ' ' + signal.source_url`, then:

```
mcp__supabase__execute_sql (project_id=xvwvwbnxdsjpnealarkh):
SELECT public.rpc_regex_check(
  $pat$<observable.search_pattern>$pat$,
  $hay$<json_compact(signal.raw_payload)> <signal.source_url>$hay$
) AS result;
```

Response: `{"matched": <bool>, "match": "<first matched substring>" | null}`. Treat `matched=false` as `NO_MATCH`. Use `$pat$...$pat$` and `$hay$...$hay$` dollar quoting so regex metacharacters and signal payload content survive SQL literal parsing.

If the result is `NO_MATCH` (`matched=false`):
- Rewrite this update to `new_status='pending'` (do NOT commit the triggered claim).
- Log to `candidate_aging_failures` with a correctly-computed `consecutive_failures` (look up the prior row and increment; this is what drives the `operator_flags` surfacing in spec §7.5):
  ```sql
  -- Read the most recent prior failure for this candidate (NULL if none).
  SELECT consecutive_failures
  FROM public.candidate_aging_failures
  WHERE candidate_id = $candidate_id
  ORDER BY attempt_at DESC
  LIMIT 1;
  -- Let $prev = that value or 0 if no row.

  INSERT INTO public.candidate_aging_failures
    (candidate_id, error_kind, error_message, routine_output, consecutive_failures)
  VALUES ($candidate_id, 'hallucinated_trigger',
          format('kill_id=%s claimed triggered by %s but observable.search_pattern did not match', $kill_id, $evidence_url),
          $full_routine_output_jsonb,
          COALESCE($prev, 0) + 1);
  ```
- If the NEW `consecutive_failures ≥ 3`, surface an `operator_flags` row (the partial unique index at [initial_schema.sql:284-293](https://github.com/marazuela/conan/blob/main/supabase/migrations/20260420200000_initial_schema.sql) dedupes open flags per `(source, kind, candidate_id)`):
  ```sql
  INSERT INTO public.operator_flags (severity, source, kind, candidate_id, title, body, evidence)
  VALUES ('warn', 'candidate_aging', 'aging_stuck', $candidate_id,
          format('Candidate %s stuck: %s consecutive aging failures', $ticker_mic, $new_consecutive),
          'Most recent failure: hallucinated_trigger on kill_id=' || $kill_id,
          jsonb_build_object('consecutive_failures', $new_consecutive, 'last_kill_id', $kill_id))
  ON CONFLICT (source, kind, coalesce(scanner_id::text,''), coalesce(entity_id::text,''),
               coalesce(signal_id,''), coalesce(candidate_id::text,''))
    WHERE resolved_at IS NULL
  DO UPDATE SET evidence = EXCLUDED.evidence,
                body = EXCLUDED.body,
                updated_at = now();
  ```
- If the `recommendation` was `kill` and it depended on this kill_id, downgrade recommendation to `maintain` and note in `recommendation_reasoning` that the evidence did not verify.

**On a successful Stage B evaluation (no failure this run), reset the streak.** Insert a zero-count sentinel so the next failure starts counting from 1 again:

```sql
-- Only when Stage B completed without inserting any candidate_aging_failures row this run.
-- Skip if there's no prior failure row for this candidate (nothing to reset).
INSERT INTO public.candidate_aging_failures
  (candidate_id, error_kind, error_message, routine_output, consecutive_failures)
SELECT $candidate_id, 'other', 'streak_reset_after_success', '{}'::jsonb, 0
WHERE EXISTS (SELECT 1 FROM public.candidate_aging_failures WHERE candidate_id = $candidate_id);

-- And resolve the open operator_flag, if any.
UPDATE public.operator_flags
SET resolved_at = now(), resolved_note = 'aging succeeded; streak reset'
WHERE source = 'candidate_aging' AND kind = 'aging_stuck'
  AND candidate_id = $candidate_id AND resolved_at IS NULL;
```

Repeat the regex check for every `triggered` entry. Only commit updates that pass.

### 7. Apply the decision (one transaction per candidate)

Compute the new `kill_conditions` JSONB by merging the verified updates into the existing array (status changes only; other fields preserved).

Statement ordering matters: if the Supabase MCP's `execute_sql` doesn't wrap this block in a single transaction (untested in this codebase), a crash between statements could leave the candidate half-updated. The order below keeps `last_aging_evaluated_at` as the LAST write — its absence is the idempotency guard (step 1 skips rows already evaluated today). On partial failure, the next run picks the candidate back up rather than skipping.

```sql
BEGIN;

-- 1. Update the row's kill_conditions + (optional) state. NO last_aging_evaluated_at yet.
UPDATE public.candidates
SET kill_conditions = $updated_kill_conditions
WHERE id = $candidate_id;

-- If recommendation changes state:
UPDATE public.candidates
SET state = $new_state  -- one of 'killed', 'watch', 'delivered'
WHERE id = $candidate_id;

-- 2. candidate_events — always record the Stage B evaluation; state_changed only on transition.
-- Maintain → event_type='scored' for audit trail. payload.stage='B' distinguishes Claude-eval
-- from mechanical transitions (see step 2 quota query).
INSERT INTO public.candidate_events (candidate_id, event_type, payload) VALUES (
  $candidate_id,
  CASE WHEN $state_changed THEN 'state_changed' ELSE 'scored' END,
  jsonb_build_object(
    'source', 'candidate_aging',
    'stage', 'B',
    'from', $prev_state,
    'to', $new_state,
    'reason', $recommendation_reasoning,
    'triggered_kill_id', $first_triggered_kill_id_or_null,
    'kill_condition_updates', $verified_updates_jsonb,
    'evaluator_session_id', $your_session_id
  )
);

-- 3. outcomes — only on terminal states.
INSERT INTO public.outcomes (candidate_id, outcome_type, realized_return, notes)
SELECT $candidate_id,
       CASE $new_state WHEN 'killed' THEN 'killed' WHEN 'delivered' THEN 'delivered' END,
       NULL,  -- realized_return filled manually by Pedro later
       $recommendation_reasoning
WHERE $new_state IN ('killed', 'delivered');

-- 4. last_aging_evaluated_at goes LAST — this is the "evaluated today" marker that
--    step 1 filters on. If anything above fails, leaving this unset means the next run
--    re-picks the candidate rather than silently skipping it.
UPDATE public.candidates
SET last_aging_evaluated_at = now()
WHERE id = $candidate_id;

COMMIT;
```

On `new_state ∈ {killed, delivered}`, the `candidate_events` INSERT with `event_type='state_changed'` reaches the fan-out edge function ([fanout/index.ts:108-118](supabase/functions/fanout/index.ts)) but **no email fires by default** — per memory `email_alert_gating.md` and the 2026-04-20 directive, email only fires on pre-edge promotion (`event_type='created' | 'thesis_drafted_by_claude'`). State-change emails sit behind the feature flag `EMAIL_STATE_CHANGE_KILLED_DELIVERED` (default `false`). The dashboard surfaces terminal transitions; that is the notification path today. If Pedro re-enables the flag on the fanout function, the Appendix D state-change template is already wired and will ship emails without any skill change.

### 8. Move to the next candidate

Loop to step 3 until the candidate list from step 1 is drained or the quota is hit.

## Reference data

- Kill-condition shape: `candidates.kill_conditions` is a JSONB array of `{id, description, observable: {source_type, search_pattern, filing_type?, url_pattern_hint?}, date_bound?, status}`. Status vocabulary: `pending | triggered | cleared`.
- Recent-signal window: 14d standard, 30d if any signal in the group has `scoring_profile='litigation'` (matches the reactor's `window_days()` rule).
- convergence_key query pattern: see `modal_workers/shared/rubric_engine.py::convergence_reference()` for the audit-parity reference.
- Regex matching: `public.rpc_regex_check(pattern, text)` → Modal `regex-check` endpoint → Python `re.search`. Defaults to `re.IGNORECASE` unless the pattern embeds an inline flag group in its first 5 chars (e.g. `(?i)`, `(?im)`, `^(?i)`) — same rule as the old bash path.

## Supabase cheatsheet (project_id=xvwvwbnxdsjpnealarkh)

Tables touched:
- `candidates` — read due rows; update `kill_conditions`, `state`, `last_aging_evaluated_at`.
- `signals` — read only (recent-14/30d context).
- `candidate_events` — append-only; `event_type ∈ {state_changed, scored}` from this skill.
- `outcomes` — insert-only on kill / deliver.
- `candidate_aging_failures` — insert-only on integrity-check failures or routine errors.

RLS is on; Supabase MCP talks as service_role so writes bypass.

## Self-check

Before closing out a candidate evaluation, verify:

- [ ] Stage A was evaluated first; Stage B only ran if Stage A didn't decide.
- [ ] Every `new_status='triggered'` update passed the regex integrity check.
- [ ] `candidates.last_aging_evaluated_at` was updated regardless of decision.
- [ ] `candidate_events` row exists; on kill/deliver, `outcomes` row exists too.
- [ ] Quota counter incremented only if Claude reasoning was used (Stage B).

Emit a summary line per candidate: `"<ticker>.<mic>: <prev_state>→<new_state> (<reason>)"` or `"<ticker>.<mic>: maintain (<one-line>)"`.
