# conan-cowork-skills

Canonical source for Claude skills + reference docs that power Conan's scheduled tasks.
Two Claude desktop sessions pull from this repo:

- **Mac (primary / maintainer)** — authors signals-, thesis-, and candidate-lifecycle skills; edits happen here.
- **Second machine (task runner)** — runs `signal_resolver` and `thesis_writer` on a schedule to add throughput. Read-only in practice.

Both machines connect to the same Supabase project (`xvwvwbnxdsjpnealarkh`) via the Supabase MCP with a service-role key held locally on each machine.

## Quick links

- 📊 [AI_TASKS_OVERVIEW.md](AI_TASKS_OVERVIEW.md) — how every AI task intertwines (v2 + v3, data flows, quotas, failure surfaces).
- 🛠️ [SETUP.md](SETUP.md) — fresh-machine bootstrap for Mac (authoring) and Windows (runner) with per-task smoke tests.

## Layout

```
skills/      # Claude skill definitions (one .md per skill, loaded by the Claude desktop at runtime)
wrappers/    # Paste-ready Cowork scheduled-task prompts (one .md per scheduled task)
reference/   # Docs the skills cite at runtime (spec sections, scoring rubric)
```

See [`wrappers/README.md`](wrappers/README.md) for the scheduled-task registration pattern and `$CONAN_ROOT` conventions.

## What each skill does

| Skill | Trigger | Writes to |
|---|---|---|
| `signal_resolver` | Every ~10 min | Resolves unresolved `signals` → entity hints, patches rows |
| `thesis_writer` | Hourly :00 UTC | Drafts theses for Immediate-band candidates (§7.4), 15/day cap |
| `thesis_challenger` | Post-draft | Runs the challenger pass on a fresh thesis |
| `candidate_aging` | Daily 06:00 UTC | Ages `candidates` through the lifecycle states |
| `coverage_auditor` | Inside Modal `reporting_weekly` cron (Sun 12:00 UTC) | Writes `operator_flags` for recall misses (NOT a Cowork task) |
| `challenger_retro` | Weekly Sun 09:00 UTC | Reviews challenger verdicts for drift |
| `fda_medical_review` / `fda_regulatory_review` / `fda_microstructure_review` | Hourly :15 / :30 / :45 UTC | Drains `fda_agent_reviews` queues; decision-support payloads |
| `bulk_orchestrator_run` | Daily 09:00 UTC + weekly Mon (v3 Tier 2) | Tier-2 sweep over `fda_assets.watch_priority`; escalates to Tier 1 |

> **v3 Tier-1 sub-agents** (`literature_reviewer`, `regulatory_history`, `competitive_landscape`) live in [`conan-fda-orchestrator-plugin/skills/`](../Conan/conan-fda-orchestrator-plugin/skills/) inside the `marazuela/conan` repo, **not in this repo**. They're Cowork plugin skills with `context: fork` and MCP tool lists — orchestrated from `modal_workers/orchestrator_app.py`, not run as Cowork scheduled tasks. See [AI_TASKS_OVERVIEW.md §3](AI_TASKS_OVERVIEW.md#3-v3-data-flow).

## Setup — Mac (canonical)

The live Conan working directory symlinks its `.claude/skills/` and the two top-level reference docs into this repo, so edits made in either location land in the same file.

```bash
# one-time, on the Mac:
git clone git@github.com:marazuela/conan-cowork-skills.git /Users/Pico/Documents/Claude/Projects/conan-cowork-skills
cd /Users/Pico/Documents/Claude/Projects/Conan

# back up the original directory once
mv .claude/skills .claude/skills.bak

# point the live location at the canonical repo
ln -s /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/skills .claude/skills
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/spec.md spec.md
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/CONAN_SCORING_METHOD.md CONAN_SCORING_METHOD.md

# set CONAN_ROOT so `cd "$CONAN_ROOT"` in skills resolves
echo 'export CONAN_ROOT=/Users/Pico/Documents/Claude/Projects/Conan' >> ~/.zshrc

# verify Claude can still load the skills, then:
rm -rf .claude/skills.bak
```

After this, editing a skill from either path (`Conan/.claude/skills/*.md` or `conan-cowork-skills/skills/*.md`) changes the same file. Commit + push from `conan-cowork-skills` when you want the other machine to pick it up.

## Setup — second machine (task runner)

The task-runner machine needs **both** this repo and `marazuela/conan` checked out, because three skills (`signal_resolver`, `candidate_aging`, `thesis_writer`) shell out to local Python in the Conan tree (`modal_workers.shared.rubric_engine.rescore_with_dims`, `modal_workers.shared.candidate_gate.assess_thesis_v2`).

```bash
# 1. clone both repos
git clone https://github.com/marazuela/conan-cowork-skills.git ~/conan-cowork-skills
git clone https://github.com/marazuela/conan.git ~/conan

# 2. symlink skills into the Claude skills directory
#    path depends on install — typically ~/.claude/skills or %APPDATA%\Claude\skills
ln -s ~/conan-cowork-skills/skills ~/.claude/skills

# 3. set CONAN_ROOT persistently so `cd "$CONAN_ROOT"` in skills resolves
#    (bash/zsh — add to ~/.bashrc, ~/.zshrc, or equivalent shell rc)
echo 'export CONAN_ROOT=$HOME/conan' >> ~/.bashrc
#    (Windows/PowerShell)
#    setx CONAN_ROOT "C:\Users\<you>\conan"
```

### Requirements on the task-runner machine

- **Python 3** with `requests`, `httpx`, any deps referenced by `modal_workers/shared/*.py`. A minimal `pip install requests httpx` covers the hot path.
- **Supabase MCP** configured with the `xvwvwbnxdsjpnealarkh` project and a local service-role key — skills call MCP for all DB reads/writes.
- **`CONAN_ROOT` env var** pointing at the `marazuela/conan` checkout. Skills reference it as `cd "${CONAN_ROOT:?...}"` and will fail with a clear error if unset.
- Fresh `git pull` in both `~/conan-cowork-skills` and `~/conan` before each scheduled-task window (a simple cron can handle this).

## Sync cadence

- Pedro edits on Mac → commits + pushes from `conan-cowork-skills`.
- Second machine pulls before each scheduled-task window (or on a timer).
- Never edit from the second machine.

## Secrets policy

**No secret values ever land in this repo.** That includes the Supabase service-role key, Modal tokens, OpenDART / OpenFIGI / SEC UA strings that carry identifying info, or anything similar.

Skills reference secrets by variable name only (e.g. `$SUPABASE_SERVICE_ROLE_KEY`). Values are held in each machine's local env / secret store.

If a brief or handoff markdown needs to reference a secret, reference it by name and location — not by value. The earlier `MODAL_SCANNER_HEALTH_REPORT_2026-04-21.md` leak (OPENDART_KEY pasted in plaintext) is the failure mode to avoid.

## Not included on purpose

Everything else that lives in the main Conan working directory — `modal_workers/`, `dashboard/`, `ui_v2/`, `unified_system/`, migrations, tests — is **not** needed by a task-runner Claude. Supabase schema is live-readable via MCP; scanners run on Modal and aren't invoked from a Claude desktop session. Keeping this repo narrow reduces blast radius and keeps diffs small.
