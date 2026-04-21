# conan-cowork-skills

Canonical source for Claude skills + reference docs that power Conan's scheduled tasks.
Two Claude desktop sessions pull from this repo:

- **Mac (primary / maintainer)** — authors signals-, thesis-, and candidate-lifecycle skills; edits happen here.
- **Second machine (task runner)** — runs `signal_resolver` and `thesis_writer` on a schedule to add throughput. Read-only in practice.

Both machines connect to the same Supabase project (`xvwvwbnxdsjpnealarkh`) via the Supabase MCP with a service-role key held locally on each machine.

## Layout

```
skills/      # Claude skill definitions (one .md per skill, loaded by the Claude desktop at runtime)
reference/   # Docs the skills cite at runtime (spec sections, scoring rubric)
```

## What each skill does

| Skill | Trigger | Writes to |
|---|---|---|
| `signal_resolver` | Every ~15 min | Resolves unresolved `signals` → entity hints, patches rows |
| `thesis_writer` | Every ~30 min | Drafts theses for Immediate-band candidates (§7.4), 15/day cap |
| `thesis_challenger` | Post-draft | Runs the challenger pass on a fresh thesis |
| `candidate_aging` | Daily | Ages `candidates` through the lifecycle states |
| `coverage_auditor` | Weekly (Sun) | Writes `operator_flags` for recall misses |
| `challenger_retro` | Weekly | Reviews challenger verdicts for drift |

## Setup — Mac (canonical)

The live Conan working directory symlinks its `.claude/skills/` and the two top-level reference docs into this repo, so edits made in either location land in the same file.

```bash
# one-time, on the Mac:
git clone git@github.com:<owner>/conan-cowork-skills.git /Users/Pico/Documents/Claude/Projects/conan-cowork-skills
cd /Users/Pico/Documents/Claude/Projects/Conan

# back up the original directory once
mv .claude/skills .claude/skills.bak

# point the live location at the canonical repo
ln -s /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/skills .claude/skills
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/spec.md spec.md
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/CONAN_SCORING_METHOD.md CONAN_SCORING_METHOD.md

# verify Claude can still load the skills, then:
rm -rf .claude/skills.bak
```

After this, editing a skill from either path (`Conan/.claude/skills/*.md` or `conan-cowork-skills/skills/*.md`) changes the same file. Commit + push from `conan-cowork-skills` when you want the other machine to pick it up.

## Setup — second machine (task runner)

```bash
git clone https://github.com/<owner>/conan-cowork-skills.git ~/conan-cowork-skills

# symlink the skills into that machine's Claude skills directory.
# (exact path depends on the Claude desktop install — typically ~/.claude/skills/ or %APPDATA%\Claude\skills\)
ln -s ~/conan-cowork-skills/skills ~/.claude/skills

# for scheduled tasks to reach the reference docs, either:
#   (a) pass the reference/ path to the skill at invocation, or
#   (b) symlink reference/ into whatever cwd the scheduled task runs in.
```

Supabase access on this machine is expected to already be configured (MCP + service-role key in local env).

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
