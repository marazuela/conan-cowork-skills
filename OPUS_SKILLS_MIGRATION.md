# Opus Skills Migration — 2026-05-13

Two AI jobs moved off the Anthropic API (paid per token) onto the Claude.app subscription (flat-rate). Net effect: **~$42/week API spend killed**, same outputs, zero new infrastructure.

| Job | Before | After |
|-----|--------|-------|
| asset_linker pass-1 | Modal Sonnet · `v3-asset-linker-pass1` pg_cron `*/15` · ~$42/wk | Cowork Opus · `asset_linker_backfill` skill · $0 |
| fact_extractor | Modal Sonnet · `v3-fact-extractor` pg_cron `:20 hourly` · ~$0.45/wk | Cowork Opus · `fact_extractor_opus` skill · $0 |

Both pg_cron jobs are now `active = false` (disabled, not unscheduled — reversible).

---

## 1. What's in this migration

### 1a. Production state changes (already done by Pedro)

```sql
-- Already applied. For audit / rollback only.
SELECT cron.alter_job(8,  active := false);  -- v3-asset-linker-pass1
SELECT cron.alter_job(12, active := false);  -- v3-fact-extractor
```

### 1b. Canonical skills updated

| Skill file | Status |
|------------|--------|
| `skills/asset_linker_backfill.md` | **Upgraded** — adds marker-stamping (`linker_classified_at | result | asset_set_hash` triple), hard-halt check, oldest-first claiming, asset-set-hash cache invalidation. Same `extraction_method='cowork_backfill'` provenance. |
| `skills/fact_extractor_opus.md` | **New** — Opus-driven fact extraction from material `asset_documents` into `extracted_facts`. `extraction_model='claude-opus-4-7'`. |

Both run in a Cowork session against Supabase MCP. No Modal calls, no Anthropic API key needed.

### 1c. Migrations required

`asset_documents.extraction_method` and `asset_linker_runs.pass` CHECK constraints must accept `'cowork_backfill'`. Verify:

```sql
-- Should return 'YES' for both.
SELECT 'cowork_backfill'::text ~ (
  SELECT pg_get_expr(conbin, conrelid)
  FROM pg_constraint
  WHERE conname LIKE '%asset_documents%extraction_method%'
) AS extraction_method_ok;
```

If you get `check_violation` on first INSERT, the bundling migration `20260522000010_asset_linker_cowork_backfill_extraction_method.sql` hasn't been applied — push it before enabling the schedule.

---

## 2. Setup on JGoror's Windows runner

Both skills live in `$CONAN_COWORK_ROOT/skills/` and are auto-discovered by Cowork once the canonical repo is up to date. JGoror only needs to (a) git pull, (b) register the two scheduled tasks in Cowork's settings.

### 2a. Pull the latest skills

```powershell
cd C:\Users\JGoror\conan-cowork-skills
git pull origin main
# Confirm the two skill files exist
dir skills\asset_linker_backfill.md
dir skills\fact_extractor_opus.md
```

### 2b. Register the schedules

In **Claude Desktop → Settings → Scheduled tasks → New**, add each task. Cowork will instantiate a session per fire and load the named skill.

**Task 1 — asset_linker_backfill**

| Field | Value |
|-------|-------|
| Task name | `asset_linker_backfill` |
| Schedule | `0,30 * * * *` (every :00 and :30) |
| Skill | `asset_linker_backfill` |
| Initial prompt | `Run the asset_linker_backfill skill. Process up to 25 docs this tick.` |
| Tool permissions | `mcp__supabase__execute_sql` (auto-approve) |

**Task 2 — fact_extractor_opus**

| Field | Value |
|-------|-------|
| Task name | `fact_extractor_opus` |
| Schedule | `15 * * * *` (every :15 hourly) |
| Skill | `fact_extractor_opus` |
| Initial prompt | `Run the fact_extractor_opus skill. Process up to 10 material docs this tick.` |
| Tool permissions | `mcp__supabase__execute_sql` (auto-approve) |

### 2c. Pre-approve the Supabase MCP tool

After creating each task, click **Run now once** so the first session pre-approves `mcp__supabase__execute_sql` for future scheduled fires. Without this, the first scheduled run pauses on permission prompt and silently misses its tick.

---

## 3. Verification

### 3a. Confirm Cowork is producing the rows

After both tasks have fired once:

```sql
-- asset_linker: should see new rows with cowork_backfill
SELECT extraction_method, COUNT(*) AS rows, MAX(created_at) AS latest
FROM public.asset_documents
WHERE created_at > now() - interval '2 hours'
GROUP BY extraction_method
ORDER BY rows DESC;
```

```sql
-- fact_extractor: should see new rows with claude-opus
SELECT extraction_model, COUNT(*) AS rows, MAX(extracted_at) AS latest
FROM public.extracted_facts
WHERE extracted_at > now() - interval '2 hours'
GROUP BY extraction_model
ORDER BY rows DESC;
```

### 3b. Confirm the marker is being stamped

Critical for the asset_linker migration — without this stamp, docs get re-claimed forever and the queue never drains:

```sql
SELECT
  COUNT(*)                                                            AS total_docs,
  COUNT(linker_classified_at)                                         AS stamped,
  COUNT(*) - COUNT(linker_classified_at)                              AS unstamped,
  COUNT(*) FILTER (WHERE linker_classified_asset_set_hash IS NOT NULL) AS with_hash
FROM public.documents;
```

`unstamped` should trend toward zero. `with_hash` should grow at ~50 docs/hour after the Cowork tasks pick up.

### 3c. Confirm cost is gone

After 7 days, the v_cost_24h_by_worker view + asset_linker_runs aggregate should show zero spend on asset_linker pass-1:

```sql
SELECT pass, model, SUM(cost_usd) AS usd_7d
FROM public.asset_linker_runs
WHERE started_at > now() - interval '7 days'
GROUP BY pass, model
ORDER BY usd_7d DESC NULLS LAST;
```

Only `pass='pass2'` (Haiku) should appear, and at ~$0.03/wk. The Cowork-emitted rows write to `asset_linker_runs` with `cost_usd=0` per the skill's step 8.

---

## 4. Rollback

If either Opus task misbehaves (low yield, schema errors, rate-limit failures), flip the pg_cron back on:

```sql
SELECT cron.alter_job(8,  active := true);  -- v3-asset-linker-pass1
SELECT cron.alter_job(12, active := true);  -- v3-fact-extractor
```

Then disable the Cowork tasks in Claude Desktop (toggle "enabled" off). The Modal worker will resume from `WHERE linker_classified_at IS NULL OR linker_classified_asset_set_hash <> <current>` so any stamping the Cowork tasks did is honored — no double work.

Both directions are reversible. The migration doesn't drop or rename anything.

---

## 5. What didn't move (and why)

| Job | Stays on API | Reason |
|-----|-------------:|--------|
| `v3-orchestrator-drain` | yes | 10-stage pipeline w/ N=7 ensemble — needs parallel calls that don't fit a single subscription session |
| `v3-asset-linker-pass2` | yes | Haiku, $0.03/wk — not worth migrating |
| `v3-feedback-loop-daily` | yes | scipy.isotonic, no LLM |

The next migration candidate is **Stage 10 IC memo synthesis** (operator-facing final memo, async-tolerant, $1–2/wk). That requires a small change to the orchestrator pipeline to externalize Stage 10 — out of scope for this migration.

---

## 6. Audit trail

```
Cowork repo:
  skills/asset_linker_backfill.md      upgraded (marker-stamping, hard-halt, asset_set_hash)
  skills/fact_extractor_opus.md        new

Production DB (Supabase project xvwvwbnxdsjpnealarkh):
  cron.job 8  (v3-asset-linker-pass1)  active: true → false
  cron.job 12 (v3-fact-extractor)      active: true → false

Personal scheduled tasks (Pico Mac, ~/.claude/scheduled-tasks/):
  asset-linker-opus    new — runs same logic with extraction_method='opus_scheduled_v1'
                       (kept distinct from cowork_backfill for provenance audit)
  fact-extractor-opus  existing — already running on Mac since 2026-05
```
