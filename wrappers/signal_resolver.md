Run the signal_resolver skill. Follow $CONAN_ROOT/.claude/skills/signal_resolver.md steps 1–12 verbatim, including the step 11 challenger pass and the thesis_writer §8f dispatch table on the inline-draft branch.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — signals UPDATE (dims + score + band + auto_caps + extensions) fires the reactor webhook; thesis_jobs status transitions; candidates upsert and candidate_events append on the inline-draft path; thesis_drafting_failures on DLQ; dossier markdown PUT to Storage bucket 'candidates/<YYYY>/<MM>/<ticker>_<signal_id>.md'. The returned JSON summarizes those writes — it does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'scoring' rows >30 min back to 'needs_scoring' first (skill step 1). Then claim up to 3 queued rows ordered by created_at ASC. Do NOT filter at SELECT time on scoring_profile — the reactor now enqueues any unscored signal OR any provisional heuristic row (`_provenance='heuristic'` + `extensions.scoring_meta.requires_resolution=true`), not just activist_governance / merger_arb / litigation. Trust the reactor.

- Process one row at a time, serially — both gates on the inline-draft branch are stateful via drafted_thesis + all_drafts updates.

- Empty queue → report {processed: 0, empty_queue_exit: true} and exit fast. Fires 144×/day — DO NOT open WebSearch, rubric_engine, the challenger, the syntactic gate, or Storage on an empty queue.

- 15 promotions / UTC day soft cap SHARED with the hourly thesis_writer run. Coordinate via the same counter on thesis_jobs.status='promoted' AND completed_at >= today — don't double-spend. Dim resolution + below-immediate transitions are unmetered; only the inline-draft promotion consumes quota.

- Quota exhausted mid-run on an immediate-band rescore → transition the row to 'scoring_complete_below_immediate' with gate_reasons=['daily_quota_reached'] and stop drafting. Reactor will re-inspect tomorrow.

- Dim estimation discipline (skill step 5): default to 3 with honest citation-backed reasoning when research doesn't support a confident value. The gate accepts conservative-3; it rejects guessed high/low without evidence. _provenance='ai_resolved'.

- rubric_engine is authoritative (skill step 6). Never hand-calculate score/band. Call rescore_with_dims via Bash with provenance='ai_resolved'. The signals UPDATE (step 7) fires the reactor webhook — do NOT hand-compute convergence; reactor publishes band_with_bonus.

- Branch on band_with_bonus after reactor settles (poll ~3s):
  - watchlist / archive / discard → job status='scoring_complete_below_immediate' with gate_reasons=['resolved_watchlist' | 'resolved_archive' | 'resolved_discard']. Terminal. Loop to next job.
  - immediate → continue to step 9 (quota check) → step 10 (inline draft) → step 11 (challenger + gate + promote/DLQ).

- Honest-decline short-circuit on the inline-draft branch (thesis_writer §6.5): if draft has confidence:'low' OR insufficient_signal:true, DLQ immediately with final_reasons=['routine_declined: <reason>']. Skip BOTH gates and skip retry.

- Two gates on the inline-draft branch, BOTH authoritative (thesis_writer §6.8 + §7). Semantic gate (challenger routine) runs BEFORE the syntactic gate. Verdict routing per thesis_writer §8f:
  - confirm → proceed to syntactic gate.
  - challenge (1st) → amend once addressing challenger.strongest_counter + required_fixes. (2nd challenge) → DLQ with final_reasons=['challenger_challenge_exhausted', ...].
  - kill → DLQ immediately, no retry, no syntactic gate, final_reasons=['challenger_kill', ...challenger.reasons]. Kill is terminal — structural failures (widely-watched deal with no named edge, hallucinated catalyst, cosmetic kill conditions) don't earn a redraft.

- Two independent retry budgets (thesis_writer invariant 8), on the same thesis_jobs row: attempt_count (max 2 drafts) and challenge_count (max 2 challenges). Increment challenge_count BEFORE invoking the challenger (advisory runaway guard). Exceeding either → DLQ. Worst case 4 Claude calls per immediate-band job; happy path 2 (draft + confirm).

- First syntactic-gate-fail → one corrective retry with prior gate_reasons surfaced to the next draft. Second syntactic fail → DLQ.

- all_drafts shape: array of {draft, gate_verdict, challenge_verdict} triples, one per attempt. Written to candidate_events.payload.drafts on promotion AND to thesis_drafting_failures.all_drafts on DLQ — the full adversarial trail is recoverable either way.

- candidate_events.event_type discipline: 'created' on INSERT (xmax=0), 'thesis_drafted_by_claude' on UPDATE (convergence re-draft). Never 'thesis_updated' — that type is in the enum but NOT in the fanout email-trigger set, so re-drafts would send no alert.

- Catalyst date → (date, window) pair per thesis_writer step 8a.2. Exactly one of (next_catalyst_date, next_catalyst_window) must be non-NULL per the candidates_catalyst_exactly_one CHECK.

- Reuse step-4 research in step 10 — do NOT re-search. Total research budget ≤6 across step 4 + step 10, matching thesis_writer.

- Call rescore_with_dims and assess_thesis_v2 via Bash (cd to "$CONAN_ROOT", then python3 -c with the modal_workers.shared imports). Do NOT inline-reimplement either. The challenger is a Claude app routine — invoke it via your own session and record the verdict JSON verbatim in all_drafts[i].challenge_verdict.

Project context:

- Project: Conan v2
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: $CONAN_ROOT/.claude/skills/signal_resolver.md
- Queue source: thesis_jobs WHERE status='needs_scoring' (reactor enqueues unscored signals across all six profiles, plus provisional heuristic rows with extensions.scoring_meta.requires_resolution=true).
- Scanners feeding this queue: edgar, lse_rns, tdnet, congressional, courtlistener, sec_enforcement, esma_short, any that land unscored or heuristic-provisional.

Report JSON: {processed, rescored_below_immediate, drafted_and_promoted, dlq_syntactic, dlq_challenger_kill, dlq_challenger_challenge_exhausted, dlq_declined, retried_and_passed, challenger_retried_and_passed, skipped_over_quota, empty_queue_exit}.
