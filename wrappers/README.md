# wrappers/ — Cowork scheduled-task prompts

Each `.md` file in this directory is a paste-ready prompt for a Cowork scheduled task. Pair the wrapper with the skill file at `../skills/<name>.md`: the wrapper tells Claude what cadence / guardrails / report-JSON shape; the skill has the step-by-step instructions the wrapper says to "follow verbatim".

## Scheduled tasks

### Conan v2 pipeline (signals → candidates → outcomes)

| Wrapper | Cadence | Skill | Touches |
|---|---|---|---|
| `signal_resolver.md` | every 10 min | `signal_resolver` | drains `thesis_jobs` WHERE status='needs_scoring' — resolve dims, inline-draft on immediate band (FDA-only post-v2-teardown) |
| `thesis_writer.md` | every 6h | `thesis_writer` | drains `thesis_jobs` WHERE status='queued' — §4.5 prefilter, §6.5 flagged-pass, §6.7 discipline gate, §6.8 challenger, §7 syntactic gate |
| `candidate_aging.md` | daily 08:00 CEST | `candidate_aging` | sweeps active + watch candidates — Stage A mechanical + Stage B Claude with challenger semantic gate on every triggered claim |
| `challenger_retro.md` | manual (canonical on JGoror, weekly Sun) | `challenger_retro` | samples labeled outcomes, re-invokes challenger, 4-axis matrix + rolling-30d + per-prefilter precision |
| `coverage_auditor.md` | **manual only** (canonical in Modal `reporting_weekly`) | `coverage_auditor` | recall audit against `catalyst_universe` — SQL-only, spot-check tool |

### Conan v3 pipeline (assets → orchestrator → assessments)

| Wrapper | Cadence | Skill | Touches |
|---|---|---|---|
| `fact_extractor_opus.md` | hourly | `fact_extractor_opus` | structured fact extraction from material `asset_documents` → `extracted_facts` (200/day cap) |
| `asset_linker_backfill.md` | every 30 min | `asset_linker_backfill` | classify `documents` → `asset_documents` links, yield-first ordering (300/day cap) |
| `fda_aging_review.md` | daily 06:30 UTC | `fda_aging_review` | Stage B Claude review on `fda_assets.aging_state='kill_pending'` (10/UTC-day cap) |
| `fda_medical_review.md` | every 2h | `fda_medical_review` | drains `fda_agent_reviews` WHERE agent_kind='medical' (10/UTC-day cap) |
| `fda_regulatory_review.md` | every 2h | `fda_regulatory_review` | drains `fda_agent_reviews` WHERE agent_kind='regulatory' (10/UTC-day cap) |
| `fda_microstructure_review.md` | every 2h | `fda_microstructure_review` | drains `fda_agent_reviews` WHERE agent_kind='microstructure' (10/UTC-day cap) |
| `fda_challenger_replay.md` | weekly Sun 09:00 UTC | `fda_challenger_replay` | Stage 3 replay on labeled v3 outcomes — accuracy_metrics + flags |

### Observability

| Wrapper | Cadence | Skill | Touches |
|---|---|---|---|
| `skill_watchdog.md` | every 2h | `skill_watchdog` | detects recurring skills gone dark via DB side-effect SLA; raises/resolves `operator_flags` |

## Portability: `$CONAN_ROOT`

All five wrappers reference the skill file as `$CONAN_ROOT/.claude/skills/<name>.md` and tell Claude to `cd "$CONAN_ROOT"` before any Python invocation. That env var must be set in the environment that Cowork uses to launch Claude:

- **Mac (Pedro)**: `export CONAN_ROOT=/Users/Pico/Documents/Claude/Projects/Conan` in `~/.zshrc`. On that machine, `.claude/skills/` inside Conan is a symlink into `conan-cowork-skills/skills/` — so the wrapper's skill-file reference resolves there.
- **Windows (JGoror)**: `setx CONAN_ROOT "C:\Users\javie\conan"` (or wherever the `marazuela/conan` clone lives). JGoror will also need to ensure `.claude/skills/` inside his Conan checkout resolves to his `conan-cowork-skills/skills/` clone — either by symlinking, by checking out `conan-cowork-skills` inside the Conan tree, or by adjusting the wrapper's skill-file path to his absolute location.

If the Cowork launcher doesn't inherit shell env (e.g. launchd that doesn't load `~/.zshrc`), set `CONAN_ROOT` system-wide (`launchctl setenv CONAN_ROOT /path` on macOS, or System Properties → Environment Variables on Windows). The wrapper's Bash invocations use `${CONAN_ROOT:?...}` parameter expansion, so an unset var fails fast with a clear error message rather than running from the wrong cwd.

## Why wrappers exist in addition to skills

- **Skills** (`../skills/*.md`) are the authoritative step-by-step: SQL, Python, branching. Long-form content, loaded by Claude on each invocation.
- **Wrappers** (this directory) are the contract with the scheduler: cadence, quotas, how to report results, what to do on empty queue / failure / MCP unreachable. Short-form, machine-specific context that doesn't belong in the skill file.

When a skill's structural behavior changes (e.g. `thesis_writer` adds the short_positioning sub-quota), both files need updating: the skill with the new step, the wrapper with the new guardrail mention + report-JSON field.

## Cowork setup (paste-ready)

On each machine, register each wrapper as a Cowork scheduled task with the cadence from the table above. The Cowork scheduled-task registration surface is NOT visible via the `mcp__scheduled-tasks` MCP — it lives in the Cowork session's own UI, per-account.

Paste the full content of each `.md` file as the task prompt. Cowork will pass it to Claude on every scheduled firing; Claude reads the wrapper, then reads the referenced skill, then executes.

## Editing

Both wrappers and skills are canonical here. Editing happens in this repo; both machines pull. On Pedro's Mac, the wrappers live at `/Users/Pico/Documents/Claude/Projects/conan-cowork-skills/wrappers/` (same canonical location). Updates flow: edit here → `git commit && git push` → JGoror's machine `git pull` → next scheduled firing picks up the new text on re-invocation (Cowork re-reads the task prompt each fire).

## Not included here

- Prompts for ad-hoc Claude work (debugging, one-off drains) — those are session-local.
- Modal-side cron prompts — those live as Python code in `marazuela/conan` (`modal_workers/app.py` dispatchers, `modal_workers/observability.py` probes). Different mechanism entirely.
- Emergency-recovery prompts (reset stuck jobs, reap orphans) — each skill already handles its own step-1 stuck-row reset; no separate wrapper needed.
