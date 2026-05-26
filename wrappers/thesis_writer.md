Run the thesis_writer skill. Follow $CONAN_ROOT/.claude/skills/thesis_writer.md steps 1–9 verbatim, including the §4.5 fast-decline pre-filter, the §6.5 honest-decline flagged-pass, the §6.7 discipline gate (shadow/active per `internal_config.discipline_gate_enabled`), the §6.8 semantic gate (challenger pass), and the §8f dispatch table.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — thesis_jobs status transitions (queued → drafting → promoted | gate_failed_retrying | dlq); candidates UPSERT on (ticker, mic) with kill_conditions JSONB and catalyst (date, window) pair; candidate_events append with event_type ∈ {'created','thesis_drafted_by_claude'}; thesis_drafting_failures ONLY on true DLQ paths (syntactic-fail-2, challenger-kill, challenger-challenge-exhausted) — declines and discipline-misses do NOT land there anymore (see §8c-flagged); dossier markdown PUT to Storage bucket 'candidates/<YYYY>/<MM>/<ticker>_<signal_id>.md'. Returned JSON summarizes writes — does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'drafting' rows >30 min back to 'queued' first (skill step 1). Then load a generous queued window (LIMIT 50) with signal + scanner config so you can rank short_positioning separately.

- Quota checks before building the batch (skill step 2): the 15/day promotion cap across thesis_writer + signal_resolver (flagged-pass rows are excluded via `gate_reasons=['routine_declined_flagged']`), AND the short_positioning sub-quota from scanners.config.daily_promotion_limit (default 5 for esma_short_scanner). Non-short rows stay FIFO; short rows rank by score_with_bonus DESC NULLS LAST, scan_date DESC, created_at ASC. Overflow short rows stay status='queued' with gate_reasons=['profile_deferred_short_limit']. Take at most 5 jobs total per run.

- Empty working batch → {processed: 0, deferred_short_jobs: N, empty_queue_exit: true} and stop.

- Process one row at a time, serially — drafted_thesis + all_drafts + discipline_verdict are stateful per attempt.

- Only Immediate-band signals produce candidates (invariant 1). Don't touch needs_scoring / scoring rows — those belong to signal_resolver.

- **§4.5 fast-decline pre-filter (cheap, fail-safe).** Before research / draft / challenger / gate, run three SQL heuristics against the loaded context: H1 repeat-decline within 30d on (entity_id, scoring_profile), H2 megacap-on-broad-class ($20B+ mcap × {takeover_candidate, short_positioning, merger_arb, binary_catalyst} × information_asymmetry≤2), H3 stale-catalyst-only (timeline dim=1 in {binary_catalyst, activist_governance, merger_arb, litigation} with no forward-catalyst date). First hit wins; synthesize a stub thesis + decline_verdict (with `prefilter_check`='H1'|'H2'|'H3') and jump to §6.5 → §8c-flagged. No Claude calls, no retry budget consumed. False positives cost a real candidate; when in doubt, fall through to step 5.

- **§6.5 honest-decline flagged-pass (replaces DLQ-on-decline).** If draft sets `confidence:'low'` OR `insufficient_signal:true`, skip both gates and §6.7, jump to §8c-flagged. The candidate UPSERTs `state='watch'` with `extensions.routine_declined=true` + `extensions.decline_verdict={verdict:'decline', reasons, strongest_counter≥100ch, caller_spec_sha}`. Job closes `status='promoted'` with `gate_reasons=['routine_declined_flagged']` (excluded from the daily-15 quota; gates auto-promotion to `active` in candidate_aging Stage A). NO thesis_drafting_failures row on this branch — the flagged candidate IS the audit record. Fanout edge function skips events where `payload->>'routine_declined'='true'`.

- **§6.7 discipline gate (6-field check, before challenger).** Read `internal_config.discipline_gate_enabled`. Validate `variant_perception`(≥80ch), `preconditions`(≥40ch), `kill_criteria` (derived from ≥3 `structured_kill_conditions[]`), `return_distribution`(≥60ch + must contain a digit), `time_horizon`(≥60ch; synthesize from `next_catalyst_date` if absent), `sizing_inputs`(≥40ch, FDA-specific). Classify each as present / too_short / missing. Append `all_drafts[last].discipline_verdict={verdict, missing_fields, present_but_too_short, min_chars_required, shadow, caller_spec_sha}`.
  - `'false'` (default) → **shadow mode**: write verdict with `shadow:true`, do NOT change routing, proceed to §6.8.
  - `'true'` → **active**: all present → §6.8; any **missing** → §8c-flagged with `gate_reasons += ['routine_declined_flagged'] + ['discipline_missing_<field>' …]` (no retry); only **too_short** → ONE §8b-style retry (`status='gate_failed_retrying'`, increment `attempt_count`) with an "amend, don't redraft" corrective prompt. If `attempt_count >= 2` already → §8c-flagged with `gate_reasons += ['discipline_retry_budget_exhausted']`.
  - Skip §6.7 entirely if §6.5 already short-circuited.

- **§6.8 semantic gate, BOTH authoritative (invariant 2).** Challenger runs BEFORE the syntactic gate (§7). Verdict routing per §8f:
  - confirm + syntactic pass → promote (§8a).
  - confirm + syntactic fail (1st) → retry syntactic (§8b). 2nd fail → DLQ with final_reasons=['syntactic_fail', ...gate_reasons].
  - challenge (1st) → amend once addressing challenger.strongest_counter + required_fixes (§8d). 2nd challenge → DLQ with final_reasons=['challenger_challenge_exhausted', ...].
  - kill → DLQ immediately, no retry, no syntactic gate, final_reasons=['challenger_kill', ...challenger.reasons]. Kill is terminal.

- Two independent retry budgets (invariant 8): `attempt_count` (max 2 drafts — shared by syntactic retries AND discipline-too_short retries) and `challenge_count` (max 2 challenges). Increment challenge_count BEFORE invoking the challenger. Exceeding either → DLQ (or §8c-flagged if discipline-exhausted).

- all_drafts shape: array of {draft, discipline_verdict?, gate_verdict, challenge_verdict | decline_verdict} per attempt. Written to candidate_events.payload.drafts on promotion AND thesis_drafting_failures.all_drafts on true DLQ.

- candidate_events.event_type discipline (invariant 6): 'created' on xmax=0, 'thesis_drafted_by_claude' on UPSERT update. Never 'thesis_updated' — not in the fanout email-trigger set.

- Catalyst date → (date, window) pair per §8a.2. Exactly one of (next_catalyst_date, next_catalyst_window) non-NULL per candidates_catalyst_exactly_one CHECK. Daterange literal: '[YYYY-MM-DD,YYYY-MM-DD]'::daterange.

- UPSERT sets kill_conditions, catalyst, thesis_approved_at, last_aging_evaluated_at on first creation. Render dossier via `rpc_render_candidate_markdown_v2` and PUT to Storage before UPSERT (§8a.3). Flagged-pass dossiers include the decline_verdict block as a footer.

- Boilerplate banned: "scanner classified signal_type", "tdnet filed", "auto-generated by", "placeholder thesis", "no thesis yet", "to be researched" auto-fail the syntactic gate. Tags ([verified]/[inferred]/[speculated]): ≥5 across situation+why_underpriced+steelman, ≥1 [verified], >2 untagged load-bearing sentences = hard fail.

- Research budget ≤6 WebSearch queries (skill step 5): 1-2 primary-source, 1-2 market-context, 1-2 disconfirming. ≥1 web_research entry with lean ≠ 'strengthening'.

- Call `rpc_assess_thesis` via the Supabase MCP with the two-statement enqueue/collect pattern (single-call form deadlocks 60s on pg_net visibility). Dollar-quote every JSON payload (`$json$...$json$`). The challenger is a Claude routine — invoke via your own session and record verdict JSON verbatim in `all_drafts[i].challenge_verdict`.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/thesis_writer.md
- Queue source: thesis_jobs WHERE status='queued' (reactor enqueues only band_with_bonus='immediate' signals; signal_resolver can also promote inline).
- Short-positioning scanners feeding the queue: esma_short_scanner (daily_promotion_limit=5).
- Discipline-gate feature flag: `SELECT value FROM public.internal_config WHERE key='discipline_gate_enabled'` ('false' = shadow, 'true' = active).

Report JSON: {processed, promoted, retried_and_passed, challenger_retried_and_passed, discipline_retried_and_passed, dlq_syntactic, dlq_challenger_kill, dlq_challenger_challenge_exhausted, flagged_declined, discipline_decline, prefilter_decline_h1, prefilter_decline_h2, prefilter_decline_h3, deferred_short_jobs, skipped_over_quota, empty_queue_exit, discipline_gate_mode: 'shadow'|'active'}.
