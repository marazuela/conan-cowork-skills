Run the candidate_aging skill. Follow $CONAN_ROOT/.claude/skills/candidate_aging.md steps 1–8 verbatim: bootstrap preflight, Stage A mechanical sweep (step 3), Stage B Claude evaluation (step 5), challenger semantic gate on every triggered claim (step 5.5), regex integrity check via Supabase RPC (step 6).

Bootstrap preflight FIRST (mandatory). Resolve `$CONAN_ROOT` and confirm `$CONAN_ROOT/.claude/skills/candidate_aging.md` exists. On failure: UPSERT `operator_flags` (source='candidate_aging', kind='bootstrap_failure') via Supabase MCP, report `{bootstrap_failure: true}`, and stop. Never exit silently — a raised flag is recoverable; a silent no-op is not.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — candidates UPDATE (kill_conditions, deliver_conditions, state, last_aging_evaluated_at); candidate_events append (event_type ∈ {'state_changed','scored'}, payload.stage stamped 'A' or 'B'); outcomes INSERT on terminal states with outcome_label set inline (killed→dead_catalyst, delivered→pre_edge_hit) so challenger_retro has labeled samples; candidate_aging_failures INSERT on integrity failures with consecutive_failures counter; operator_flags UPSERT for aging_stuck / bootstrap_failure / challenger cosmetic rejections. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Once per UTC day per candidate (invariant 4). Filter at SELECT time via `last_aging_evaluated_at::date < today OR IS NULL`. The `UPDATE last_aging_evaluated_at=now()` is the LAST statement in the per-candidate transaction so partial failure re-picks the row tomorrow.

- v2-teardown scope filter: `scoring_profile IN ('binary_catalyst','fda_event')` ONLY. Non-FDA breadth is sunset; don't spend Stage B budget on out-of-scope rows.

- Stage A mechanical sweep first (invariant 1). Stage A rules don't count toward Claude quota. Stage A decisions in priority order:
  1. **Promote watch → active** if catalyst within next 60d AND `extensions->>'routine_declined' IS DISTINCT FROM 'true'`. The routine_declined gate (added 2026-04-27) blocks auto-promotion of candidates that thesis_writer's §6.5 honest-decline branch flagged — operator must clear the flag from the dashboard first. Use `current_date` arithmetic + `daterange(...)` overlap, NEVER `tstzrange` (silent predicate fail).
  2. Aged-out watch (60d) → killed (+ outcomes 'expired').
  3. Stale active (30d, no near catalyst by EITHER `next_catalyst_date` OR `next_catalyst_window`) → watch.
  4. **Deterministic catalyst-elapsed demote (2026-05-08)** — `state='active'` AND catalyst elapsed >7d (date OR upper(window)) → watch. Foreign-agency guard: skip if `extensions->>'catalyst_jurisdiction'` is not US-FDA (EMA/PMDA/NMPA routinely run 7-14d late; 30d stale rule catches those). Replaces the old AXSM-class stickiness where Stage B kept choosing `maintain`.
  5. Recent elapsed catalyst (T+1 to T+7) → flag `catalyst_elapsed=true` for Stage B, don't exit.

- Stage B quota: 15 Claude evaluations / UTC day. Count via `candidate_events WHERE payload->>'source'='candidate_aging' AND payload->>'stage'='B' AND created_at >= today`. The `stage='B'` filter is load-bearing — Stage A inserts stamp `stage:'A'`. If ≥15, defer remaining and stop, but keep processing Stage A past quota.

- Empty queue → `{processed: 0, reason: 'all candidates already evaluated today'}` and stop.

- Err toward `maintain` (invariant 3). Ambiguous evidence → do nothing.

- Stage B evaluates BOTH `kill_conditions` AND `deliver_conditions` (structured, added 2026-05-08). Recommendation precedence: kill > deliver > demote_to_watch > maintain. `deliver` recommendation requires a triggered deliver_condition that survived challenger + regex gates AND no triggered kill in the same run. `demote_to_watch` only when `catalyst_elapsed=true` (Stage A's 1-7d flag) and nothing else fired.

- Integrity defense on every `triggered` claim (invariant 2), kill OR deliver:
  1. Invoke challenger routine (step 5.5) with thesis + condition + signal.raw_payload + observable.search_pattern + caller_spec_sha. Verdicts: `confirm` → proceed to regex check; `challenge` → downgrade to pending, log `candidate_aging_failures` (error_kind='challenger_challenge'), retry tomorrow; `kill` → downgrade to pending, log (error_kind='challenger_kill_cosmetic'). If recommendation was `kill` and depended on this update, downgrade recommendation to `maintain`.
  2. Regex integrity check via `rpc_regex_check` + `rpc_compute_collect` (two-statement enqueue/collect pattern, never collapse — pg_net visibility bug deadlocks 60s). Replaces the old `python3 -c` shell-out broken by the 2026-04-22 sandbox outage. `matched=false` → rewrite to pending, INSERT `candidate_aging_failures` with `consecutive_failures = prior + 1`. If new `consecutive_failures ≥ 3`, UPSERT `operator_flags` (source='candidate_aging', kind='aging_stuck'). Both gates must pass; either fails → pending.

- On clean Stage B run (no failures this run), reset the streak: insert `streak_reset` sentinel (consecutive_failures=0) AND resolve any open `aging_stuck` operator_flag for this candidate. Idempotent.

- Challenger budget: 2 invocations per candidate per run max. Beyond that, skip remaining triggered claims and log `error_kind='challenger_budget_exhausted'`.

- State transitions write TWO rows (invariant 5): `candidate_events(event_type='state_changed', payload.stage='B', payload.source='candidate_aging')` AND `outcomes(outcome_type, notes, outcome_label, labeled_at)` on kill/deliver. Maintain decisions still write `candidate_events(event_type='scored', stage='B')` for quota counting + audit.

- Terminal kill/deliver state changes do NOT fire email by default — feature flag `EMAIL_STATE_CHANGE_KILLED_DELIVERED` is false per `email_alert_gating`. Dashboard is the notification path; the fanout function enforces the flag.

- convergence_key window (step 4): 14d standard, 30d for `scoring_profile='litigation'`. If a convergence cluster mixes profiles and any member is litigation while the candidate isn't, re-query at 30d.

- Regex checks go through the Supabase RPC pair (enqueue + collect), NOT a `python3 -c` shell-out. The Modal endpoint behind `rpc_regex_check` runs `re.search` with the same inline-flag detection as the old bash path.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/candidate_aging.md
- Source rows: `candidates WHERE state IN ('active','watch') AND scoring_profile IN ('binary_catalyst','fda_event') AND (last_aging_evaluated_at IS NULL OR last_aging_evaluated_at::date < today)`, ordered by `current_score DESC`.
- Deterministic Modal companion: `pre_edge_monitor` may have transitioned obvious post-edge cases (definitive deal announced, FDA approval/CRL on binary_catalyst) before this runs. `last_aging_evaluated_at` filter catches; Stage A/B still handles idempotently if not.

Report JSON: `{processed_total, stage_a_transitioned, stage_b_evaluated, promoted_to_active, routine_declined_skipped, killed, delivered, demoted_to_watch_deterministic, demoted_to_watch_stage_b, maintained, integrity_downgrades, challenger_cosmetic_downgrades, aging_stuck_flags_raised, aging_stuck_flags_resolved, deferred_over_quota, empty_queue_exit, bootstrap_failure}`.
