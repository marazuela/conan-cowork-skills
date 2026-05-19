# Setup

Fresh-machine bootstrap for the two Cowork machines that run Conan's AI tasks. Companion to [AI_TASKS_OVERVIEW.md](AI_TASKS_OVERVIEW.md) (data-flow map) and [README.md](README.md) (repo orientation).

**Last revised:** 2026-05-19 (Stream 2 now scheduled via pg_cron — see status
table; `anthropic-orchestrator` secret status unverified, left as-is). Prior:
2026-05-08 (post v3 Phase 0 merge — D-115 → D-123).

Two roles:

- **Mac (authoring, primary):** Pedro edits skills, drives Modal deploys, runs ad-hoc skills. Has all three repos.
- **Windows (JGoror, runner):** unattended runner for the v2 Cowork scheduled tasks. Read-only in practice.

---

## 1. Prerequisites (both machines)

### Software

- **Python 3.11+** with `pip install requests httpx anthropic supabase modal`.
- **Git** with a working `gh` CLI logged into the `marazuela` org.
- **Claude Desktop** with Cowork enabled.

### Env vars (set persistently)

| Variable                | Purpose                                                  | Required        |
|-------------------------|----------------------------------------------------------|-----------------|
| `CONAN_ROOT`            | Path to `marazuela/conan` checkout                       | yes             |
| `CONAN_COWORK_ROOT`     | Path to `marazuela/conan-cowork-skills` checkout         | yes             |

### Connections

- **Supabase MCP** configured against project `xvwvwbnxdsjpnealarkh` with a local service-role key. Skills do every DB read/write through MCP.
- **Anthropic API key** (for skills that call the SDK directly outside the Cowork session — e.g. nothing today, but D-123 routines will).
- **Modal CLI auth** (`modal token new`) — Mac only is fine; Windows runner does not invoke Modal directly.

---

## 2. Mac (authoring) bootstrap

```bash
# 1. Clone all three repos under one parent dir (pick what you like; this is Pedro's layout)
mkdir -p /Users/Pico/Documents/Claude/Projects
cd /Users/Pico/Documents/Claude/Projects

git clone git@github.com:marazuela/conan.git Conan
git clone git@github.com:marazuela/conan-cowork-skills.git
git clone git@github.com:marazuela/conan-dashboard.git   # optional on Mac; needed if you'll edit dashboard
```

```bash
# 2. Symlink the canonical skills repo into Conan/.claude/skills (hardlink-equivalent because both
#    point at the same inode set on disk). This is what makes "edit either path, both update" true.
cd /Users/Pico/Documents/Claude/Projects/Conan

# back up once, in case there's anything local we haven't pushed
[ -d .claude/skills ] && [ ! -L .claude/skills ] && mv .claude/skills .claude/skills.bak

ln -s /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/skills .claude/skills
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/spec.md spec.md
ln -sf /Users/Pico/Documents/Claude/Projects/conan-cowork-skills/reference/CONAN_SCORING_METHOD.md CONAN_SCORING_METHOD.md

# verify Cowork still loads skills, then drop the backup
ls -la .claude/skills    # should show the symlink target in conan-cowork-skills
[ -d .claude/skills.bak ] && rm -rf .claude/skills.bak
```

```bash
# 3. Set env vars in ~/.zshrc (run once, then `source ~/.zshrc`)
cat >> ~/.zshrc <<'EOF'
export CONAN_ROOT="/Users/Pico/Documents/Claude/Projects/Conan"
export CONAN_COWORK_ROOT="/Users/Pico/Documents/Claude/Projects/conan-cowork-skills"
EOF
source ~/.zshrc
```

```bash
# 4. Install the v3 FDA orchestrator plugin so Cowork sees the three sub-agent skills
#    (literature_reviewer, regulatory_history, competitive_landscape).
#    The plugin lives inside the Conan repo at conan-fda-orchestrator-plugin/.
#    Exact registration command depends on your Cowork plugin path — check first:
claude plugin list

#    Then either:
#    a) symlink the plugin into the user plugins dir Cowork shows, OR
#    b) add the path to settings.json plugin search paths (preferred — no symlink to break).
#    Sub-agent skills appear with `context: fork` + MCP tool lists; orchestrator dispatches them.
```

```bash
# 5. Modal auth + secrets (run once on the Mac that drives deploys)
modal token new

# Stream 2 currently aliases the orchestrator key to scanner-secrets per D-123 fallback.
# If scanner-secrets already has ANTHROPIC_API_KEY, skip this. Otherwise:
modal secret create scanner-secrets ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY

# anthropic-orchestrator is the future home (D-123) but is intentionally NOT created yet —
# Stream 2 falls back gracefully to scanner-secrets and emits [auto-fallback] in logs.
```

```bash
# 6. Validate setup
test -L .claude/skills && readlink .claude/skills | grep -q "conan-cowork-skills/skills$" && echo "skills symlink OK" || echo "skills symlink BROKEN"
test -n "${CONAN_ROOT:-}" -a -d "$CONAN_ROOT" && echo "CONAN_ROOT OK" || echo "CONAN_ROOT BROKEN"
```

---

## 3. Windows (JGoror, runner) bootstrap

The runner needs **both** repos because three skills shell out to local Python in the Conan tree (`modal_workers.shared.rubric_engine`, `modal_workers.shared.candidate_gate`).

```powershell
# 1. Clone both repos into C:\Users\JGoror\
cd C:\Users\JGoror
git clone https://github.com/marazuela/conan-cowork-skills.git
git clone https://github.com/marazuela/conan.git
```

```powershell
# 2. Set env vars persistently (close + reopen terminal for them to apply)
setx CONAN_ROOT          "C:\Users\JGoror\conan"
setx CONAN_COWORK_ROOT   "C:\Users\JGoror\conan-cowork-skills"
```

```powershell
# 3. Symlink the canonical skills dir into the Cowork skills path.
#    Confirm the path that Cowork actually reads from:
claude plugin list      # check output for "skill paths" or similar
# Most installs use %APPDATA%\Claude\skills.

# Run as Administrator (mklink /D requires elevation):
mklink /D "%APPDATA%\Claude\skills" "C:\Users\JGoror\conan-cowork-skills\skills"
```

```powershell
# 4. Validate
if (Test-Path "$env:APPDATA\Claude\skills") { "skills symlink OK" } else { "skills symlink BROKEN" }
if (Test-Path "$env:CONAN_ROOT")             { "CONAN_ROOT OK" }     else { "CONAN_ROOT BROKEN" }
```

### Cowork scheduled tasks on the runner

The three v2 tasks live **in Pedro's Cowork session itself**, not in any external scheduler — they are NOT visible via `mcp__scheduled-tasks` MCP. Configure them inside Claude Desktop's Cowork scheduled tasks UI:

| Task              | Cron                                          | Phrase                              |
|-------------------|-----------------------------------------------|-------------------------------------|
| `signal_resolver` | every 10 min                                  | "drain signal_resolver queue"       |
| `thesis_writer`   | hourly at :00 UTC                             | "drain queued theses"               |
| `candidate_aging` | daily 06:00 UTC                               | "run candidate aging sweep"         |
| `challenger_retro`| weekly Sun 09:00 UTC                          | "run challenger retro"              |

The hourly FDA review trio (`fda_medical_review`, `fda_regulatory_review`, `fda_microstructure_review`) is also Cowork-scheduled — typically only on the runner if Pedro has signed off on the FDA volume:

| Task                          | Cron               | Phrase                                  |
|-------------------------------|--------------------|-----------------------------------------|
| `fda_medical_review`          | hourly :15 UTC     | "drain queued FDA medical reviews"      |
| `fda_regulatory_review`       | hourly :30 UTC     | "drain queued FDA regulatory reviews"   |
| `fda_microstructure_review`   | hourly :45 UTC     | "drain queued FDA microstructure reviews" |

`coverage_auditor` is **not** a Cowork task — it runs as the first step of the Modal `reporting_weekly` cron (`0 12 * * 0` UTC). Don't schedule it on the runner.

### Pull cadence

Wrap each task with a `git pull` first so the runner stays current:

```powershell
# Suggested pre-task one-liner that the Cowork prompt opens with:
cd %CONAN_COWORK_ROOT% ; git pull --ff-only ; cd %CONAN_ROOT% ; git pull --ff-only
```

Pedro edits on Mac → commits + pushes from `conan-cowork-skills` → JGoror's pre-task pull picks it up. **Never edit from the runner.**

### Skip on the runner

- v3 FDA orchestrator plugin install — the Tier-1 orchestrator runs on Modal, not Cowork. Tier-2 (`bulk_orchestrator_run`) is currently Mac-only.
- Modal CLI auth — runner doesn't deploy or invoke Modal.
- Anthropic SDK direct calls — all v2 skills go through Cowork session credentials.

---

## 4. Modal-side wiring (Pedro, Mac, run once)

Status as of 2026-05-19:

| Component                    | Status                                                                           |
|------------------------------|----------------------------------------------------------------------------------|
| reactor v12                  | ✅ deployed (D-122) — `asset_documents` branch + FDA short-circuit on `signals` |
| fanout v8                    | ✅ deployed (D-122) — entry-point D for `convergence_assessments` immediate band|
| `conan-v3-feedback-loop` app | ✅ deployed (D-123) — scheduled via pg_cron (below)                              |
| Stream 2 daily kickoff       | ✅ **scheduled** — pg_cron `v3-feedback-loop-daily` @ 02:00 UTC (migration `20260518000000_v3_feedback_loop_pg_cron.sql`) posts `feedback_loop_kickoff` to the app. Bypasses the Modal 5-cron cap entirely — no v2 cron retired. |
| `anthropic-orchestrator` secret | ⏳ not created — Stream 2 falls back to `scanner-secrets` and logs `[auto-fallback]` |

Stream 2 scheduling — **done** (no Modal cron slot used; pg_cron drives it):

```bash
# Migration applied: supabase/migrations/20260518000000_v3_feedback_loop_pg_cron.sql
# pg_cron 'v3-feedback-loop-daily' @ 02:00 UTC posts
#   {"action":"feedback_loop_kickoff","args":{}} to the compute_v3 multiplex,
#   which fans out post_mortem_runner → nightly_calibration_refit → rollback_monitor.
# Rollback:  select cron.unschedule('v3-feedback-loop-daily');
```

To create the dedicated secret when ready:

```bash
modal secret create anthropic-orchestrator ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
# After this, post_mortem_runner stops emitting [auto-fallback] in logs.
```

---

## 5. Per-task smoke tests

Run from the Mac with the Supabase MCP wired up (read-only smoke tests are safe; the `INSERT` ones use `dry_run=true` flags or scoped test data so they don't pollute production). **Confirm with Pedro before running any INSERT against `xvwvwbnxdsjpnealarkh`** — most of these are described as procedures rather than batteries to run unattended.

### 5.1 `signal_resolver`

```sql
-- Find an existing needs_scoring row first; do not invent fake signal IDs.
SELECT signal_id, scoring_profile, score
FROM signals
WHERE score IS NULL
  AND scoring_profile IN ('activist_governance', 'merger_arb', 'litigation')
ORDER BY created_at DESC
LIMIT 5;
```

Then trigger the Cowork phrase "drain signal_resolver queue" on the runner and re-query — `score` should populate within ~10 min.

### 5.2 `thesis_writer`

```sql
-- Find a queued thesis_jobs row that's already enqueued by the reactor:
SELECT signal_id, status, created_at
FROM thesis_jobs
WHERE status = 'queued'
ORDER BY created_at DESC
LIMIT 5;
```

Trigger Cowork phrase "drain queued theses". Within an hour expect `status='promoted'` and a matching row in `candidates` with `state='watch'`. DLQ to check on failure: `thesis_drafting_failures` (filter by `signal_id`).

### 5.3 `candidate_aging`

```sql
-- Pick a watch-state candidate that hasn't been evaluated today:
SELECT candidate_id, state, last_aging_evaluated_at
FROM candidates
WHERE state IN ('watch', 'active')
  AND (last_aging_evaluated_at IS NULL OR last_aging_evaluated_at::date < CURRENT_DATE)
ORDER BY last_aging_evaluated_at NULLS FIRST
LIMIT 5;
```

Trigger Cowork phrase "run candidate aging sweep". Expect `last_aging_evaluated_at = now()` and (if any kill_condition matched) a fresh `candidate_events` row with `event_type='state_changed'`.

### 5.4 v3 reactor + orchestrator-runs enqueue

```sql
-- Find an FDA asset and seed an asset_documents row (verify 'documents' has a real doc_id):
SELECT a.fda_asset_id, a.asset_label, d.document_id
FROM fda_assets a
JOIN documents d ON d.entity_id = a.entity_id
WHERE a.is_active = true
ORDER BY d.fetched_at DESC
LIMIT 3;
```

`INSERT INTO asset_documents (fda_asset_id, document_id, ...)` for one of those rows (pair with Pedro). The reactor v12 webhook should fire on the INSERT and produce an `orchestrator_runs` row with `trigger_type IN ('new_doc', 'cross_source')`. Watch the queue:

```sql
SELECT * FROM orchestrator_runs ORDER BY enqueued_at DESC LIMIT 5;
```

To then drain the queue manually:

```bash
modal run modal_workers/orchestrator_app.py::orchestrator_drain_queue --max-per-run=1
```

Stage 10 → `convergence_assessments` requires the orchestrator to fully execute (Tier-1 ensemble + sub-agents). Gate this test on Tier-1 readiness rather than running it cold.

### 5.5 v3 Stream 2 (`post_mortem_runner` dry run)

```bash
# Validates the drainer logic without writing to post_mortem_queue:
modal run modal_workers/feedback_loop_app.py::post_mortem_drain_dry_run --batch-size=10
modal run modal_workers/feedback_loop_app.py::rollback_monitor_dry_run --window-days=30
```

Expect a JSON return showing how many rows would have been processed and the gate decision (passed/failed) without persistence.

### 5.6 fanout (v3 immediate email)

Don't trigger this with synthetic data — it dispatches real email through Resend. Wait for the next live `convergence_assessments` insert with `band='immediate'` and confirm:

```sql
-- Audit Storage upload + delivery row:
SELECT id, assessment_id, channel, dispatched_at
FROM alert_deliveries
WHERE assessment_id IS NOT NULL
ORDER BY dispatched_at DESC
LIMIT 5;
```

---

## 6. Troubleshooting

| Symptom                                                 | Likely cause                                                        | Fix                                                                                  |
|---------------------------------------------------------|---------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| Cowork: "skill X not found"                             | Symlink broken or skills dir not registered                         | `ls -la .claude/skills` — re-create the symlink; restart Claude Desktop              |
| skill says `cd "$CONAN_ROOT"` then errors               | `CONAN_ROOT` unset on the runner                                    | `setx CONAN_ROOT ...` (Win) or `export CONAN_ROOT=...` (Mac), then restart Claude    |
| Stream 2 logs `[auto-fallback]` on every run            | `anthropic-orchestrator` secret not created                         | `modal secret create anthropic-orchestrator ANTHROPIC_API_KEY=...`                   |
| `failed_reactor_events` filling up, `payload->>'source' = 'signal_resolver'` | Reactor preflight failing on rescore         | Check `modal_workers/shared/rubric_engine.py` and the Modal RPC URL                  |
| Runner stops draining queues silently                   | `git pull` failed mid-window (merge conflict, auth, etc.)           | Add a wrapper that logs `git pull` exit codes; re-clone if persistent                |
| Tier-2 escalation never fires                           | `fda_assets.watch_priority` set wrong, OR D-128 thresholds not met  | Verify `watch_priority` rows + `dashboard_signal_rows` view                          |

---

## 7. Sync cadence (recap)

- Pedro edits on Mac (in `conan-cowork-skills/skills/` or `Conan/.claude/skills/` — same files).
- Pedro commits + pushes from `conan-cowork-skills` directly.
- JGoror's pre-task `git pull --ff-only` picks up changes before each window.
- **Never edit from the runner.** It's read-only by convention; treating it as a write surface drifts the canonical repo.
