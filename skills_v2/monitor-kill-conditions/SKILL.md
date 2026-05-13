---
name: monitor-kill-conditions
description: Daily sweep across all active investment dossiers. Reads each dossier's frontmatter and explicit kill-conditions section, dispatches a profile-specific checker (merger_arb / activist_governance / binary_catalyst / litigation / insider), evaluates each kill condition against fresh primary-source data (SEC EDGAR 8-K, FDA approvals, court dockets, exchange RNS/TDnet/ASX, yfinance prices), and produces a triggered/cleared status per dossier with an archive recommendation when criteria are met. Triggers when the user asks to "run the kill sweep", "check for archive triggers", "are any active dossiers dead", or as part of the standing daily monitoring cadence on the candidate book. Outputs a markdown sweep report plus an append-only JSONL action ledger; never mutates the source dossier itself, never writes to the read-only reference folder, only emits archive *recommendations* into the working folder.
type: skill
---

# monitor-kill-conditions

## Purpose

Run a daily kill-condition sweep across every dossier currently sitting in `01_Opportunities/active/`. For each dossier, read the stated kill conditions from its `## Kill Conditions` section (or the engine-stamped `kill-watch` block), apply profile-specific automated checks against primary-source data, and emit a structured result: each kill condition is `triggered`, `clear`, or `unverifiable`, and the overall dossier is recommended for `archive`, `de-rate`, or `hold` based on the rules below.

This skill is the bookend to U2 (compose-thesis-with-discipline) — U2 *writes* kill conditions; U4 *checks* them. Together they implement the operating rule that no candidate leaves the system without explicit kill conditions and no kill condition goes unmonitored.

The skill is read-mostly: it never writes inside the reference folder, and it never edits a source dossier. When a kill is triggered, the skill produces an archive recommendation file in the working folder — Pedro (or a downstream archival workflow) is responsible for executing the archive.

Invoke this skill when:

- Pedro asks to run the daily kill sweep.
- An automated overnight schedule needs to triage the active book.
- A specific dossier is suspected of having broken (single-dossier mode).
- After a high-impact market event (FDA approval list refresh, large index move, new 8-K wave) the user wants to recheck whether anything tripped.

## Inputs

| Field | Type | Example | Required |
|-------|------|---------|----------|
| `scope` | enum: `all_active` \| `single` | `all_active` | yes |
| `dossier_id` | string (folder name under `active/`) | `AXSM_ADA_PDUFA` | required when `scope == single` |
| `as_of_date` | ISO date | `2026-04-29` | no — defaults to today (UTC) |
| `dossier_root` | path | defaults to `<reference>/01_Opportunities/active/` | no |
| `output_dir` | path | defaults to `skills/monitor-kill-conditions/outputs/` | no |
| `dry_run` | bool | `false` | no — when `true`, skip primary-source HTTP calls and report only structural results |

`dossier_root` is read-only. The skill MUST NOT write back to that path. All artifacts are emitted under `output_dir`, which is inside the working folder.

## Outputs

Two files per run, both atomic-written (write-to-temp + rename):

1. `skills/monitor-kill-conditions/outputs/<YYYY-MM-DD>_kill_sweep.md` — human-readable sweep report.
2. `skills/monitor-kill-conditions/outputs/<YYYY-MM-DD>_actions.jsonl` — append-only structured action ledger (one JSON object per line).

When at least one dossier is recommended for archive, the skill ALSO writes:

3. `skills/monitor-kill-conditions/outputs/<YYYY-MM-DD>_archive_recommendations/<dossier_id>.md` — one file per archive-recommended dossier, citing exactly which kill criterion fired, the primary-source URL or filing accession, and the timestamp.

Final stdout line is a one-line JSON summary:
```
{"status":"ok","as_of":"2026-04-29","dossiers_processed":N,"kill_triggered":K,"de_rate_recommended":D,"unverifiable":U,"output_md":"...","output_jsonl":"...","duration_s":T}
```

## Methodology

### Step 1 — Discover active dossiers

If `scope == all_active`:

1. List directories under `dossier_root` (default `<reference>/01_Opportunities/active/`).
2. Filter for entries that contain a `dossier.md` file directly inside.
3. Skip any folder name beginning with `_` or containing `.bak` or `.pre_` (these are backups, never live).

If `scope == single`:

1. Validate `dossier_id` resolves to a folder with a `dossier.md`.
2. Process only that one.

For each active dossier, capture the absolute path and proceed to Step 2.

### Step 2 — Parse dossier frontmatter and kill-conditions

Use `helpers/dossier_parser.py` to extract:

- **Frontmatter** (YAML between `---` lines at file head): `ticker_local`, `mic`, `ticker_plus_mic`, `cik`, `figi`, `issuer_figi`, `score`, `status`, `signal_type`, `signal_category`, `scoring_profile` or inferred from `signal_category`, `primary_catalyst_date`, `last_updated`, `first_signal_date`.
- **Kill conditions list**: regex-locate `^## Kill Conditions` section (case-insensitive), parse numbered or bulleted list items beneath. Each entry is `{index, raw_text, parsed_trigger, parsed_source, parsed_action}`. The parser uses pattern matching — it does not require strict structure — so dossiers written with prose-style kill conditions still produce extractable triggers.
- **State markers**: `score`, `status`, `primary_catalyst_date`. If `status != "active"`, skip with a warning (the dossier should not have been in `active/`).

If the kill-conditions section is missing or empty, mark the dossier with `data_quality: kill_conditions_missing` and skip individual-trigger checks (still apply the universal checks in Step 4 — score, catalyst-date staleness, status drift).

The parser maps each parsed trigger to a `condition_kind` — one of: `price_break`, `catalyst_date_passed`, `filing_filed` (8-K, 13D/A, etc.), `regulatory_decision_issued` (FDA approval/CRL, court order), `analyst_action` (downgrade), `safety_signal_appeared`, `position_change` (insider sells, activist exits), `corporate_action` (dilutive issuance, equity raise), `macro_drawdown`. Untyped or freeform triggers fall into `manual_review_required` and are reported but not auto-checked.

### Step 3 — Determine profile

Profile precedence: explicit `scoring_profile` field in frontmatter → mapping from `signal_category` (`pdufa_binary` / `fda_pdufa` → binary_catalyst; `activist` / `governance` → activist_governance; `takeover` / `merger` → merger_arb; `litigation` / `enforcement` → litigation; `insider` → insider) → fallback to text-pattern recognition from `signal_type`.

If no profile can be determined, mark `data_quality: profile_undetermined` and apply only universal checks.

### Step 4 — Universal checks (every profile)

Run before profile-specific checks:

1. **Catalyst-date staleness.** If `primary_catalyst_date` is a parseable date and is more than 14 calendar days in the past relative to `as_of_date`, and the dossier has not been updated (`last_updated`) within those 14 days, flag `universal_catalyst_passed_no_update`. This is *not* an automatic kill — it forces a manual review note: the catalyst either occurred (resolution should be captured) or was extended (extension should be captured).
2. **Score band drift.** If the dossier `score` would land in `archive` band (10–19) or below per the bands in `profile_adjustments.md` (Immediate ≥ 30, Watchlist 20–29, Archive 10–19, Discard < 10), flag `universal_score_below_active_band`. Combined with no recent update, this is a soft-archive recommendation.
3. **Frontmatter `status` drift.** If status is anything other than `active`, log and skip remaining checks.
4. **Stale dossier.** If `last_updated` is more than 30 calendar days old, flag `universal_stale_dossier`. Not a kill, but contributes to a de-rate recommendation.

### Step 5 — Profile-specific checks

Each profile has a dedicated checker in `helpers/kill_checks_<profile>.py`. Each checker is a pure function: given the parsed dossier state, the parsed kill triggers, and a clock (`as_of_date`), it returns a list of `{condition_index, kind, status: triggered|clear|unverifiable, evidence, source_url, confidence}` records.

Each checker consults primary sources via `helpers/primary_source_clients.py` — a thin wrapper that tries authoritative endpoints and gracefully degrades if a source is unavailable.

#### merger_arb

Authoritative kill events:

- **Deal closed.** SEC EDGAR 8-K Item 2.01 (Completion of Acquisition or Disposition of Assets) or analog filing on the issuer's local exchange (LSE RNS "Scheme of arrangement effective", TDnet "結合契約締結", ASX "Scheme implemented", Irish Takeover Panel rule 2.7 "Effective Date"). Result: kill (deal done; dossier resolves into delivered_recent with `outcome: deal_closed`). Primary source: filing accession + issuer-press release URL.
- **Deal withdrawn or terminated.** 8-K Item 1.02 (Termination of Material Definitive Agreement) referencing the merger agreement, or jurisdiction-equivalent (LSE "Offer lapses", LSE "Offer withdrawn", FCA TR-1 disposal by acquirer). Result: kill — but with directional sign flipped (price reverts; dossier resolves with `outcome: deal_terminated`).
- **Spread compression.** Compute current spread `(consideration - market_price) / market_price`. If `<5%` for 3 consecutive trading days AND deal expected to close within 30 days, kill (edge has evaporated; capital is better redeployed). Source: yfinance for price; consideration from dossier or DEFM14A/announcement.
- **Regulatory denial.** DOJ/FTC/EC/CMA/MOFCOM press release announcing denial or imposing remedies the parties have not accepted. Result: triggered (deal probability collapsed).
- **MAC invocation.** If the dossier specifies a kill at MAC invocation, monitor the issuer's primary feed for an 8-K disclosing buyer claim of MAC. Result: triggered.

Universal-merger-arb rules applied even if dossier doesn't enumerate them: `closed`, `withdrawn`, `regulatory_denial` are *always* triggers regardless of whether the dossier listed them.

#### activist_governance

- **13D withdrawn / converted to 13G.** Filer files Schedule 13G after a 13D, or files 13D/A explicitly disclaiming intent to influence governance. Result: kill (campaign over).
- **Settlement announced.** 8-K Item 5.02 (board changes) referencing the activist by name, or DEF 14A / DEFA14A announcing a board-composition agreement. Result: kill — but classify as `outcome: settled` (positive resolution if the agreed terms align with the thesis).
- **Annual meeting passed without nominations.** If the dossier kill-condition references the DEF 14A nomination window and the annual meeting date has now passed (per company SEC filings or proxy season calendar), and no PREC14A or DEFC14A was filed by the activist, kill (campaign deferred ≥1 year).
- **Position reduction.** Subsequent 13D/A showing stake below the dossier-declared threshold (typically 5% or "below the original disclosed threshold"). Result: triggered.
- **Counter-action by company that scorches earth.** Large dilutive equity issuance specifically directed at activist (rare but historically present in defense of board control). Result: triggered.
- **Sector drawdown threshold.** If dossier specifies a sector-relative drawdown kill (e.g., −30% on macro), check sector ETF and absolute price.

#### binary_catalyst

- **FDA decision issued.** If a PDUFA date has passed or is within ±2 days, query the FDA approvals list (`https://www.accessdata.fda.gov/`) and the issuer's press releases / 8-K for approval, CRL, or extension. Result: triggered (and outcome = approval / CRL / extension). For approvals, dossier is moved to delivered_recent with positive resolution; for CRL or extension, dossier is moved to candidates_archive with explicit lessons-learned memo recommendation.
- **AdCom convened or scheduled.** Federal Register search for advisory committee meetings naming the drug or matching the indication. Result: triggered (introduces risk; dossier behavior depends on how the dossier specifies AdCom — most treat AdCom announcement as instant archive).
- **New safety signal.** openFDA `event` endpoint query for the drug's commercial label (if approved adjacent product exists) or the trial-program safety database. Compare current 30-day SAE/death cluster to prior 90-day baseline. If a clear cluster appears (z-score > 2.5 on serious AE rate), trigger.
- **Price break.** Day-close below the explicit kill price (e.g., AXSM <$155). Use yfinance close (T+1 confirmation to avoid intraday spike noise). Trigger only on confirmed close.
- **Sell-side downgrade to PT below threshold.** Search analyst-action news; trigger on confirmed downgrade matching dossier criteria.
- **8-K mentioning FDA communication ahead of decision.** Item 7.01 Reg FD or Item 8.01 with FDA-related content. Trigger.

#### litigation

- **Judgment entered.** PACER / CourtListener docket event for `judgment`, `final order`, `dismissal with prejudice`. Result: triggered (resolution; outcome = win / loss / mixed).
- **Settlement announced.** Press release or 8-K Item 8.01 disclosing settlement. Result: triggered.
- **Dismissal.** Court order on motion to dismiss granted. Result: triggered.
- **Stay or transfer.** Order staying the case >180 days or transferring to a forum where the thesis weakens. Soft-trigger (de-rate) unless dossier explicitly killed on stay.
- **Material amended pleading.** New claim added or claim withdrawn that changes EV materially. Manual-review flag.

#### insider

- **Time horizon expiry.** Dossier-stated horizon (e.g., 90 days post-cluster) elapsed. Result: triggered (reassessment required, not necessarily exit).
- **Price target hit.** Forward return reached dossier-stated threshold. Result: triggered (consider trim).
- **Insider reversal.** Subsequent Form 4 showing the cluster-buying insider now selling (within 6 months). Trigger.
- **Sector drawdown threshold** (as defined per dossier).
- **Material adverse 8-K** between the insider buy and the horizon (contradiction signal). Manual-review flag, possible kill.

### Step 6 — Apply confidence scoring per evaluation

Each per-condition status carries a confidence in `[0.0, 1.0]`:

- `1.00` — primary-source filing accession or court docket event explicitly fires the trigger (e.g., 8-K Item 2.01 for closed deal).
- `0.85–0.95` — credible secondary corroboration (issuer press release URL, official regulator statement) but not yet matched against the issuer's own SEC/exchange feed.
- `0.60–0.80` — web-news indication awaiting filing confirmation.
- `0.40–0.60` — inferred from price action only (e.g., a deal-broken move on no public filing yet); always paired with `manual_review_required`.
- `< 0.40` — drop the evaluation; report as `unverifiable`.

Confidence < 0.85 NEVER triggers an archive recommendation alone — at minimum two independent ≥0.60 signals must agree. This protects against single-source noise.

### Step 7 — Aggregate and decide per dossier

For each dossier, aggregate condition results into a final recommendation:

- `archive` — at least one `triggered` condition with confidence ≥ 0.85, or two converging `triggered` at ≥ 0.60 each (across distinct condition kinds).
- `de_rate` — universal flags (stale, drifted score, sector drawdown soft-trigger) without a hard kill.
- `hold` — all checked conditions clear; no de-rate triggers.
- `manual_review` — any `unverifiable` plus any high-impact condition kind, or kill conditions parsed but profile-checker reports `data quality insufficient`.

### Step 8 — Emit outputs

1. **Markdown sweep report** at `<output_dir>/<YYYY-MM-DD>_kill_sweep.md` with sections:
   - Summary table (dossier, profile, score, recommendation, top reason).
   - One subsection per dossier listing each parsed kill condition, status, confidence, evidence, source URL.
   - Universal-flags section (stale, score drift, catalyst passed without update).
   - Data-quality issues encountered.
   - As-of timestamp + duration.
2. **JSONL action ledger** at `<output_dir>/<YYYY-MM-DD>_actions.jsonl` — one line per dossier, schema:
   ```json
   {"as_of":"...","dossier_id":"...","ticker":"...","profile":"...","score":N,"recommendation":"archive|de_rate|hold|manual_review","triggered_conditions":[{...}],"data_quality":[...]}
   ```
3. **Per-dossier archive-recommendation memos** when recommendation is `archive`:
   ```
   skills/monitor-kill-conditions/outputs/<YYYY-MM-DD>_archive_recommendations/<dossier_id>.md
   ```
   Each memo cites: the firing kill criterion (verbatim), the source URL or filing accession, the confidence score, the suggested destination (`delivered_recent/` for closed/approved/won; `candidates_archive/` for CRL/dismissed/withdrawn), a short narrative for the dossier's update-log block, and an explicit reminder that the actual archive *move* must be performed by an operator (this skill never moves files).

### Step 9 — Atomic-write discipline

Use the `atomic_write` helper: write to `<final>.tmp.<pid>`, fsync, rename to `<final>`. Never write to a final path directly. The JSONL file is also produced via temp-rename (whole-file atomic, not per-line append) — when re-running on the same date, the existing file is overwritten as a complete snapshot for that as-of date.

### Step 10 — Idempotency and safe re-run

The skill is fully idempotent for a given `as_of_date`: re-running produces the same outputs (modulo timestamps) provided the underlying primary-source state has not changed. If the user runs the skill twice on the same day, the second run *replaces* the markdown and JSONL but appends nothing new to the JSONL beyond what the snapshot needs.

## Profile-specific application

Universal layer always runs. Profile-specific layer is dispatched via the `kill_checks_<profile>.py` helpers.

The skill emits an explicit "profile dispatched" line in the sweep report so operators can verify that, e.g., AXSM was run through the binary_catalyst checker rather than a fallback.

If a dossier's profile is `short_positioning` (out-of-scope for this skill build per `skill_build_plan.json`), the skill applies universal checks only and adds a `profile_out_of_scope` note.

## Output schema

### sweep markdown — required sections

```
# Kill-Condition Sweep — <YYYY-MM-DD>

**As of:** <ISO timestamp UTC>
**Dossiers processed:** N
**Kill recommendations:** K (archive)
**De-rate recommendations:** D
**Manual-review flags:** M
**Hold:** H

## Summary table

| Dossier | Profile | Score | Top firing condition | Recommendation |
|---|---|---|---|---|

## Per-dossier details

### <dossier_id> (<ticker> — <company name>)

**Profile:** <profile>
**Score:** <score> (<band>)
**Last updated:** <date>
**Recommendation:** <recommendation>

| # | Condition (verbatim) | Kind | Status | Confidence | Evidence | Source URL |
|---|---|---|---|---|---|---|

**Universal flags:** ...
**Data-quality issues:** ...

## Data-quality issues encountered (skill-level)

## Footer

*Run duration: T sec. Helpers used: <list>. Source endpoints reached: <list>. Confidence-floor: 0.85 single-trigger, 0.60×2 converging.*
```

### actions JSONL line — schema

```json
{
  "as_of": "2026-04-29",
  "skill_run_id": "<uuid>",
  "dossier_id": "AXSM_ADA_PDUFA",
  "dossier_path": "<reference>/01_Opportunities/active/AXSM_ADA_PDUFA/dossier.md",
  "ticker": "AXSM",
  "company_name": "Axsome Therapeutics, Inc.",
  "profile": "binary_catalyst",
  "score": 30.75,
  "recommendation": "hold",
  "triggered_conditions": [],
  "cleared_conditions": [
    {"index": 1, "verbatim": "...", "kind": "regulatory_decision_issued", "confidence": 0.95, "source": "..."}
  ],
  "unverifiable_conditions": [],
  "universal_flags": [],
  "data_quality": [],
  "duration_s": 1.32,
  "primary_sources_consulted": ["edgar","fda","yfinance"]
}
```

## Worked example — `scope=all_active` on `as_of=2026-04-29`

Inputs: `scope=all_active`, default `dossier_root` = the eight active dossier folders (AXSM_ADA_PDUFA, RPAY_Forager_ActivistPoisonPill, PTSB_BAWAG_cash_offer, VERA_IgAN_PDUFA, VRDN_veligrotug_PDUFA, RGR_Beretta_ProxyFight, 6027_2026-04-15_impairment_loss, LKQ_strategic_review).

Expected processing:

- AXSM (binary_catalyst) → 7 enumerated kill conditions parsed. PDUFA = 2026-04-30, T-1 calendar day. Checker queries FDA approvals (no decision yet), federal register (no AdCom), yfinance (price > $165). Universal flags: catalyst-date imminent (next-day) — switches dossier to `manual_review` if any check returns `unverifiable`, else `hold`. Smoke-test outcome: `hold` with 0/7 triggered, 7/7 clear, confidence 0.92 average.
- RPAY (activist_governance) → 6 enumerated kill conditions. Checker queries EDGAR for new Forager 13D/A, board-settlement 8-K, Veradace 13D/A. As of 2026-04-29, the latest material event in the dossier was 2026-04-21 (Forager $4.80 take-out proposal). No subsequent withdrawal or settlement filing. yfinance: price ~$4.05. None triggered. Universal flag: status active, score 33 above band. Outcome: `hold`.
- PTSB (merger_arb) → BAWAG recommended cash offer, regulatory pending. Checker queries 8-K equivalent (LSE RNS / Irish Takeover Panel) for "Effective Date" or "Offer lapses". None present as of 2026-04-29. Spread check: market vs €2.97 — spread ~3% (typical for cleared-but-unclosed Irish bank deals). Universal: spread <5% but expected close > 30 days → no kill. Outcome: `hold` with note "spread thin; monitor regulatory clock".
- LKQ (merger_arb gated_review) — strategic review only, not a definitive deal yet; treated under universal rules. Outcome: `hold` (continue to monitor for definitive transaction announcement).
- 6027 Bengo4.com (activist/impairment) — Japanese filer; checker dispatches through TDnet adapter; reads frontmatter `last_updated`. Universal flag: stale > 30 days → `de_rate`.
- VERA, VRDN (binary_catalyst) — pre-PDUFA; no decision yet; price checks clear. Outcome: `hold`.
- RGR (activist_governance) — Beretta proxy fight; checker queries EDGAR for SC 13D/A, PRRN14A, settlement 8-K. Outcome: `hold` (assuming no fresh filing).

Top of sweep markdown:

```
# Kill-Condition Sweep — 2026-04-29

**As of:** 2026-04-29T01:30:00Z
**Dossiers processed:** 8
**Kill recommendations:** 0 (archive)
**De-rate recommendations:** 1 (6027 Bengo4.com — stale > 30 days)
**Manual-review flags:** 0
**Hold:** 7
```

JSONL excerpt for AXSM:

```json
{"as_of":"2026-04-29","skill_run_id":"3f0c…","dossier_id":"AXSM_ADA_PDUFA","ticker":"AXSM","profile":"binary_catalyst","score":30.75,"recommendation":"hold","triggered_conditions":[],"cleared_conditions":[{"index":1,"kind":"regulatory_decision_issued","status":"clear","confidence":0.95,"source":"https://www.fda.gov/news-events/press-announcements"}, ...]}
```

## Failure modes and recovery

| Failure | Detection | Skill behavior |
|---|---|---|
| Dossier root path doesn't exist | Step 1 enumeration fails | Emit JSON `{"status":"error","error_class":"path_missing"}`, exit 1 |
| Dossier has no frontmatter | Step 2 parser returns empty | Skip dossier, log `data_quality: frontmatter_missing` |
| Kill-conditions section missing | Step 2 regex returns empty | Run universal checks only; flag for manual review |
| Profile cannot be determined | Step 3 fallback returns null | Universal-only; flag `profile_undetermined` |
| Primary source unavailable (network / 5xx / 4xx) | helper raises `PrimarySourceUnavailable` | Mark relevant condition `unverifiable` with confidence ≤ 0.30; do NOT mark as triggered or clear |
| yfinance returns null for ticker | `pricing_unavailable` | `unverifiable` for price-related conditions |
| FDA approvals list 5xx | `fda_unavailable` | `unverifiable` for `regulatory_decision_issued` on binary_catalyst |
| CourtListener auth missing | `auth_required` | `unverifiable` for litigation court-docket conditions; do NOT crash |
| Conflict between converging kill triggers and a non-firing universal | inconsistency detected | Recommend `manual_review`, never auto-archive |
| State race: dossier moved during sweep | filesystem error mid-run | Skip and log; remaining dossiers proceed |
| Output write race | atomic-write helper detects existing temp | Backoff once, then write a `.<unix>.tmp` variant; never corrupt the final file |

No silent failures. Every degraded path produces an explicit `data_quality` annotation with reduced confidence.

## Compliance with system invariants

- **Folder scope.** Reads from `<reference>/01_Opportunities/active/` (read-only). Writes ONLY to `<working>/skills/monitor-kill-conditions/outputs/`. Never touches reference-folder files.
- **Atomic writes.** All output files written via temp-then-rename via `helpers/atomic_write.py`.
- **Append-only ledgers.** The actions JSONL is treated as a daily snapshot; older daily files remain untouched. The skill never deletes prior days.
- **Confidence + source fields.** Every parsed condition row carries `confidence` ∈ [0,1] and `source` (URL or filing accession). Same for the JSONL ledger.
- **Primary-source discipline.** Decisions about `archive`/`de_rate` are tied to a primary-source citation per the §1.2 invariant in the project root `CLAUDE.md`. Web-news-only signals are degraded to `manual_review`.
- **No mutation of reference folder.** The skill explicitly emits archive *recommendations*, never archive *actions*. Moving the source dossier is a separate human-authored or downstream-task step, never performed here.
- **Bounded runtime.** Target ≤ 60 s typical, ≤ 120 s hard cap. The dispatcher passes a soft deadline; if exceeded, remaining dossiers receive `unverifiable` for unfinished checks rather than the skill blocking indefinitely.
- **HALT-aware.** If `<reference>/02_System/engine/health/HALT_FLAG` exists, exit immediately with `{"status":"halted"}`.
- **Numeric tickers always render with company names.** The output `ticker` field always pairs with `company_name` per `feedback_ticker_company_names.md`.
- **Idempotent.** Same inputs + same date = same outputs.
