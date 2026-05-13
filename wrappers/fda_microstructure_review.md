Run the fda_microstructure_review skill. Follow $CONAN_ROOT/.claude/skills/fda_microstructure_review.md steps 1–10 verbatim.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — fda_agent_reviews status transitions (queued → running → completed | failed), fda_event_evidence INSERT on success (source='agent_microstructure'), failed_reactor_events + operator_flags INSERT on schema-validation failure. The returned JSON summarizes those writes — it does not replace them. If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Reset stuck 'running' rows >30 min back to 'queued' first (skill step 1). Then claim the oldest queued row in agent_kind='microstructure' (skill step 3).

- Quota check before claim (skill step 2): 10 completed reviews / UTC day. Per-kind.

- Schema validation is authoritative (skill step 7). Never write fda_agent_reviews(status='completed') without first calling fda_agent_validator.validate('microstructure', payload) and getting valid=true.

- Polygon wins when present. compose_features only uses your options_liquidity_score / implied_move_pct overrides when Polygon-derived values are null. If existing 'polygon' source evidence already covers it, set insufficient_signal: true and provide narrative redflags only.

- Implied move magnitudes are non-negative. The schema rejects negative implied_move_pct. Move size is a magnitude.

- Borrow cost is informational. It does not move score. Capture it for the dashboard, but don't bake it into other modifiers.

- Cite primary sources, ≥3 (schema enforces min_items: 3). Existing Polygon snapshots (cite the API URL), FINRA short interest (regsho.finra.org or nasdaq), IBKR/Schwab borrow data, 13F overlap (whalewisdom/dataroma).

- Honest decline > hedged prose. insufficient_signal: true when chain is thin or borrow data unavailable.

- One row per event per snapshot. (event_id, agent_kind, snapshot_hash) unique.

- On failure, write BOTH failed_reactor_events (DLQ — payload.source='fda_agent_review') AND operator_flags (kind='schema_validation_failed', source='fda_agent_review', severity='warn').

- Process serially, up to 5 reviews per run.
