Run the skill_watchdog skill. Follow `$CONAN_ROOT/.claude/skills/skill_watchdog.md` verbatim, including the per-skill SLA probes, the raise/resolve `operator_flags` pattern, and the mandatory one-line JSON report.

Why this skill exists: the 2026-05-19 transcript audit found ~20–28% of recurring skill runs lost to silent failures (zero-turn launches, mid-run aborts, API ConnectionRefused) that leave no log, no verdict, no trace. A skill that never starts cannot write its own beacon — so this watchdog detects darkness externally, from the absence of each monitored skill's expected database side-effect within its cadence SLA.

Outputs wired into the Conan app via the Supabase MCP (project ref: xvwvwbnxdsjpnealarkh). All persistence must actually happen on that project — `operator_flags` rows with `source='skill_watchdog'`, `kind='skill_dark:<skill>'`, `severity ∈ {warn,critical}`, `evidence` jsonb (skill + checked_at + SLA numbers). If you cannot reach the Supabase MCP, do not fabricate success; report the failure.

Guardrails:

- Read-only against ALL monitored-skill tables (signals, thesis_jobs, candidates, extracted_facts, asset_documents, orchestrator_runs, outcomes). The ONLY write surface is `operator_flags`. No schema changes, no SLA tightening, no re-enabling paused crons, no restarting monitored skills.

- Per-skill SLA enumeration (the skill defines the probes; the wrapper does not re-derive them):
  - `asset_linker_backfill` — every 30 min; DARK if work_pending AND last_link < now() - interval '4 hours'. Severity warn.
  - `fact_extractor_opus` — hourly; DARK if work_pending AND last_fact < now() - interval '6 hours'. Severity warn.
  - `bulk_orchestrator_priority1` — daily ~09:00 UTC; DARK if now()::time > '11:00' AND runs_today = 0. Severity **critical** (Tier-2 write path goes dark).
  - `thesis_writer` — every 6 h; DARK if stuck > 0 (status IN queued/drafting AND coalesce(updated_at, created_at) < now() - interval '8 hours'). Severity warn at stuck ∈ [1,5]; **critical** at stuck > 5 (≥2 missed cron fires; immediate-band signals stranded outside their alerting SLA — thesis_writer is P0).
  - `candidate_aging` — daily; DARK if now()::time > '09:00' AND eligible > 0 AND events_today = 0. Escalate to **critical** if any unresolved `source='candidate_aging' AND kind='bootstrap_failure'` flag exists.
  - `signal_resolver` — every 2 h; DARK if stale > 0 (signals in needs_scoring/scoring older than 6 h). Severity warn at stale ∈ [1,10]; **critical** at stale > 10 (≥3 missed cron fires; immediate-band funnel backed up beyond single-cycle recovery — signal_resolver is P0).

- No false alarms on idle (invariant 2). Every SLA check pairs "no side-effect" with "there was work to do" (a pending-queue probe) or a hard time-of-day expectation. A quiet queue never pages.

- Idempotent raise (invariant 3). Before INSERT, dedup on `source='skill_watchdog' AND kind='skill_dark:<skill>' AND resolved_at IS NULL` — one unresolved flag per dark skill at a time. On recovery (side-effect fresh again on this run), UPDATE the open flag with `resolved_at = now()` and `resolved_note = 'auto-resolved: side-effect fresh again at ' || now()` instead of stacking new ones.

- operator_flags shape conformance (invariant 4): `severity`, `source`, `kind`, `title` NOT NULL; `evidence` jsonb NOT NULL (default `'{}'`). Mirror the conventions used by `asset_linker_hard_halt` / `signal_entity_resolver` rows.

- Silent watchdog is the same bug it exists to catch (invariant 5). Always emit the report line — if everything is healthy, say so explicitly.

Project context:

- Project: Conan v3
- Supabase ref: xvwvwbnxdsjpnealarkh
- Skill file on disk: `$CONAN_ROOT/.claude/skills/skill_watchdog.md`
- Cadence: every 2 hours (re-runs are safe — dedup'd raises, idempotent resolves).

Report JSON: `{checked, dark: [<skills>], raised: [<skill_dark:...>], resolved: [<skill_dark:...>], bootstrap_failure?}`. If `dark`, `raised`, and `resolved` are all empty, say so explicitly — "everything healthy" is the load-bearing output.
