Run the fda_regulatory_review skill. Follow $CONAN_ROOT/.claude/skills/fda_regulatory_review.md steps 1–10 verbatim.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — fda_agent_reviews status transitions (queued → running → completed | failed), fda_event_evidence INSERT on success (source='agent_regulatory'), failed_reactor_events + operator_flags INSERT on schema-validation failure. The returned JSON summarizes those writes — it does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'running' rows >30 min back to 'queued' first (skill step 1). Then claim the oldest queued row in agent_kind='regulatory' (skill step 3).

- Quota check before claim (skill step 2): 10 completed reviews / UTC day. Per-kind, separate from medical and microstructure quotas.

- Schema validation is authoritative (skill step 7). Never write fda_agent_reviews(status='completed') without first calling fda_agent_validator.validate('regulatory', payload) and getting valid=true.

- Bounds are double-enforced. evidence_confidence_boost is bounded ±0.40 in the schema AND clamped again in compose_features.

- Cite primary sources, ≥3 (schema enforces min_items: 3). FDA briefing books (`fda.gov/media/<id>/download`), Federal Register notices, AdCom transcripts, public CRL letters where available. The modal_workers.providers.federal_register adapter exposes search() / get_document() if you need to surface a notice the operator hasn't loaded yet.

- Honest decline > hedged prose. insufficient_signal: true when regulatory record is thin.

- One row per event per snapshot. (event_id, agent_kind, snapshot_hash) unique.

- On failure, write BOTH failed_reactor_events (DLQ — payload.source='fda_agent_review') AND operator_flags (kind='schema_validation_failed', source='fda_agent_review', severity='warn').

- Process serially, up to 5 reviews per run.
