Run the fda_medical_review skill. Follow $CONAN_ROOT/.claude/skills/fda_medical_review.md steps 1–10 verbatim.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — fda_agent_reviews status transitions (queued → running → completed | failed), fda_event_evidence INSERT on success (source='agent_medical'), failed_reactor_events + operator_flags INSERT on schema-validation failure. The returned JSON summarizes those writes — it does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'running' rows >30 min back to 'queued' first (skill step 1). Then claim the oldest queued row in agent_kind='medical' (skill step 3).

- Quota check before claim (skill step 2): 10 completed reviews / UTC day. Per-kind, separate from thesis_writer's 15/day promotion budget.

- Schema validation is authoritative (skill step 7). Never write fda_agent_reviews(status='completed') without first calling fda_agent_validator.validate('medical', payload) and getting valid=true.

- Bounds are double-enforced. fair_probability_modifier is bounded ±0.10 in the schema AND clamped again in compose_features. Don't push values beyond ±0.10.

- Cite primary sources, ≥3 (schema enforces min_items: 3). ClinicalTrials.gov, PubMed/NEJM/Lancet/JAMA for trial data, FDA briefing books for staff review. Never fabricate URLs or quotes.

- Honest decline > hedged prose. insufficient_signal: true is allowed when evidence is thin. Set fair_probability_modifier: 0.0 and confidence: 0.3 in that case. The bridge will use the row but won't over-weight it.

- One row per event per snapshot. The unique constraint (event_id, agent_kind, snapshot_hash) prevents duplicates. Use snapshot_hash from the queued row.

- On failure (schema invalid OR unrecoverable error), write BOTH failed_reactor_events (DLQ — payload.source='fda_agent_review') AND operator_flags (kind='schema_validation_failed', source='fda_agent_review', severity='warn'). Two-row write, matches the existing pattern.

- Process serially, up to 5 reviews per run. Loop back to step 2 after each. Stop at quota or empty queue.
