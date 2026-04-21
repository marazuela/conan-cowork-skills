Run the candidate_aging skill. Follow $CONAN_ROOT/.claude/skills/candidate_aging.md steps 1–8 verbatim, including the Stage A mechanical sweep (step 3), Stage B Claude evaluation (step 5), the semantic gate on every triggered claim (step 5.5 challenger pass), and the regex integrity check (step 6).

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — candidates UPDATE (kill_conditions, state, last_aging_evaluated_at); candidate_events append (event_type ∈ {'state_changed','scored'}, payload.stage stamped 'A' or 'B'); outcomes INSERT on terminal states; candidate_aging_failures INSERT on integrity failures with consecutive_failures counter; operator_flags UPSERT for aging_stuck / challenger cosmetic rejections. Returned summary describes writes — does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Once per UTC day per candidate (invariant 4). Filter at SELECT time via last_aging_evaluated_at::date < today OR IS NULL. First write after ANY decision (including maintain) is UPDATE last_aging_evaluated_at=now() — placed LAST in the transaction so a partial failure doesn't silently skip the candidate on the next run.

- Stage A mechanical sweep first (invariant 1). Stage A rules (60d aged-out watch → killed, 30d stale-active with no near catalyst → watch, elapsed catalyst → flag for Stage B) don't count toward Claude quota. Only run Stage B when Stage A didn't decide.

- Elapsed catalyst check uses BOTH next_catalyst_date AND next_catalyst_window (daterange). The candidates_catalyst_exactly_one CHECK permits one of them; ignoring the window demotes candidates with a near quarter-window.

- Stage B quota: 15 Claude evaluations / UTC day (separate from thesis_writer + signal_resolver's 15/day promotion cap). Count via candidate_events WHERE payload->>'source'='candidate_aging' AND payload->>'stage'='B' AND created_at >= today. Stage A inserts stamp stage:'A' and don't count; the stage:'B' filter is load-bearing. If ≥15, defer remaining and stop — but keep processing Stage A decisions even past quota.

- Empty queue (all candidates already evaluated today) → {processed: 0, reason: 'all candidates already evaluated today'} and stop.

- Err toward 'maintain' (invariant 3). Ambiguous evidence → do nothing. Dashboard surfaces maintain events; false-positive kills cost attention, false-negative maintains cost a day of visibility.

- Integrity defense on every 'triggered' claim (invariant 2). Before committing new_status='triggered' on a kill_condition:
  1. Invoke the challenger routine (step 5.5) with candidate thesis + kill_condition + signal.raw_payload + observable.search_pattern. Verdict routing:
     - confirm → proceed to regex check.
     - challenge → downgrade update to pending this run; log candidate_aging_failures (error_kind='other', 'challenger_challenge'). Retry tomorrow.
     - kill → downgrade to pending AND log (error_kind='other', 'challenger_kill_cosmetic'). If recommendation was 'kill' and depended on this update, downgrade recommendation to 'maintain'.
  2. Regex integrity check via Python (step 6): match observable.search_pattern against signal.raw_payload + source_url (case-insensitive unless inline flags). NO_MATCH → rewrite update to pending, INSERT candidate_aging_failures with consecutive_failures (look up prior row + increment). If new consecutive_failures ≥ 3, UPSERT operator_flags (source='candidate_aging', kind='aging_stuck'). Both checks must pass; either fails → pending, never committed as triggered.

- On successful Stage B run (no failures this run), reset the streak: insert zero-count sentinel in candidate_aging_failures AND resolve any open operator_flag (aging_stuck) for this candidate. Idempotent.

- Challenger budget: 2 invocations per candidate per run max. Beyond that, skip remaining triggered claims and log error_kind='other', 'challenger_budget_exhausted'.

- State transitions write TWO rows (invariant 5): candidate_events(event_type='state_changed', payload.stage='B', payload.source='candidate_aging') AND outcomes(outcome_type, notes) on kill/deliver. Maintain decisions still write candidate_events with event_type='scored' + stage='B' for quota counting + audit.

- Terminal kill/deliver state changes do NOT fire email by default — the feature flag EMAIL_STATE_CHANGE_KILLED_DELIVERED is false per email_alert_gating. Dashboard is the notification path. Don't worry about the flag from this skill; the fanout function enforces it.

- convergence_key query pattern (step 4): recent-signals window is 14d standard, 30d for scoring_profile='litigation'. Matches rubric_engine.window_days(). If a convergence cluster mixes profiles and any member is litigation while candidate isn't, re-query at 30d to catch the wider litigation window.

- Call the regex integrity check via Bash (cd to "$CONAN_ROOT", then python3 -c with Python re module). Do NOT rely on SQL regex — Python's re is authoritative per skill step 6.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/candidate_aging.md
- Source rows: candidates WHERE state IN ('active','watch') AND (last_aging_evaluated_at IS NULL OR last_aging_evaluated_at::date < today), ordered by current_score DESC.
- Deterministic Modal companion: pre_edge_monitor may have transitioned obvious post-edge cases before this runs (definitive deal announced, FDA approval/CRL on binary_catalyst). Skip those — last_aging_evaluated_at filter catches; if not, Stage A / B still handles idempotently.

Report JSON: {processed_total, stage_a_transitioned, stage_b_evaluated, killed, delivered, demoted_to_watch, maintained, integrity_downgrades, challenger_cosmetic_downgrades, aging_stuck_flags_raised, aging_stuck_flags_resolved, deferred_over_quota, empty_queue_exit}.
