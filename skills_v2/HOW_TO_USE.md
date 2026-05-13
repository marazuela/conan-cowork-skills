# HOW_TO_USE — Operational guide for the skill bundle

Audience: Pedro, or any future Claude/Claude Code session that needs to invoke, modify, or rebuild one of these skills.

---

## What a "skill" is in this bundle

Each skill is a **methodology + helpers** package, not a runnable script you invoke from a command prompt. The intended runtime is a Claude session (or scheduled task) that:

1. Reads the `SKILL.md` to load the methodology and required inputs.
2. Calls the helper Python scripts in `helpers/` as building blocks.
3. Writes results to the skill's `outputs/` directory.

This mirrors the `skills/` convention used elsewhere in the Cowork environment (see `C:\Users\javie\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin\...\skills\` for the Anthropic-provided skills).

---

## Invoking a skill from a Claude session

**Pattern**:
1. Identify the skill (see [`INDEX.md`](INDEX.md) for the full list).
2. Read its `SKILL.md` first — it specifies required inputs, primary sources, methodology, output schema, and a worked example.
3. Gather required inputs (ticker / CIK / case ID / etc. — varies per skill, listed in `skill_build_plan.json` under `required_inputs`).
4. Execute the methodology, calling helpers as needed.
5. Write outputs to `skills/<skill-name>/outputs/` per the schema in `SKILL.md`.

**Example** — running U1 (analyze-candidate-financials) on a new candidate:
```
Read:    skills/analyze-candidate-financials/SKILL.md
Inputs:  ticker, cik, profile
Helpers: helpers/sloan_accruals.py, capital_allocation_scorecard.py, hidden_value_scanner.py
Output:  skills/analyze-candidate-financials/outputs/<ticker>_financial_assessment.md
         skills/analyze-candidate-financials/outputs/<ticker>_metrics.json
```

---

## Dependency graph (run order matters)

Several skills depend on others. The build plan locks in this dependency chain:

```
U1 (financials) ─────────────┐
                             ├──► U2 (compose-thesis-with-discipline)
P1/P2/P3/P4/P5/P6 (per-profile) ──┘

P2 ──► P1   (class precedent feeds FDA approval analysis)
P4 ──► P6   (acquirer history feeds takeover vulnerability)

M1 (harvest) ──► M2 (label outcomes) ──► M3 (extract features) ──► U3 (K-NN precedents)

U3 ──► U2   (precedent base rates inform sizing)
```

**Practical implication**: when building a fresh dossier on a new candidate, the canonical sequence is:

1. Run **U1** for universal financials.
2. Run the per-profile skill (one of P1, P3, P4, P5; or P6 for pre-edge takeover).
3. Run **U3** to anchor against historical precedents (requires M3 features to exist).
4. Run **U2** to compose the thesis with all six required fields populated.
5. **U4** runs daily as a sweep, independent of dossier creation.
6. **M1 → M2 → M3** runs as scheduled calibration tasks, independent of any single dossier.

---

## Profile coverage

| Profile | Diligence skill | Counterparty skill | Discovery skill |
|---|---|---|---|
| merger_arb | U1 | P4 (acquirer history) | P6 (takeover vulnerability) |
| activist_governance | U1 | P3 (activist filer) | — |
| binary_catalyst | U1 | P1 + P2 (FDA + class precedent) | — |
| litigation | U1 | P5 (litigation EV) | — |
| insider | U1 | *(no dedicated counterparty skill — handled in U2 inputs)* | — |
| short_positioning | **out of scope** per build plan | — | — |

---

## Path conventions

Helpers and `SKILL.md` files reference paths relative to `Investment tool backup/`. Examples found in the SKILL.md files:

- `Investment tool backup/02_System/engine/framework/profile_merger_arb.md`
- `Investment tool backup/01_Opportunities/active/RPAY/dossier.md`
- `Investment tool backup/02_System/engine/training/historical_events_ledger.json`

These all still resolve correctly because this archive (`03_Skills/`) is itself a sibling of `01_Opportunities/` and `02_System/` inside `Investment tool backup/`. **If this archive is ever moved**, those references must be updated.

Output paths in `SKILL.md` are written as `skills/<skill-name>/outputs/...`. When invoking, set the working directory to `03_Skills/` (or use absolute paths to `03_Skills/skills/<skill-name>/outputs/`) so writes land in the right place.

---

## Re-running a smoke test

Each skill's smoke test inputs are recorded in `skill_build_plan.json` under `test_candidate`. To re-validate a skill against its original test case:

1. Read the `test_candidate` block for that skill in `skill_build_plan.json`.
2. Run the skill methodology with those inputs.
3. Compare against the existing `outputs/` artifacts (which are the originals from the 2026-04-29 build run).

Example for U1: `test_candidate.ticker = "RPAY"` → expected outputs match `outputs/RPAY_financial_assessment.md` + `outputs/RPAY_metrics.json` shape.

---

## Promoting a Tier-2 skill (P1–P6) to production-ready

The Tier-2 skills (per `STATUS.md`) all passed smoke tests with **synthetic / illustrative** inputs. Per CLAUDE.md §1.2 primary-source-discipline, this is not yet decision-grade.

**Validation protocol** for each Tier-2 skill:

1. Pick a real candidate from `01_Opportunities/active/` (or a real signal from `02_System/engine/signals/`).
2. Run the skill end-to-end with primary-source pulls (no offline / illustrative mode).
3. Compare the live output against the offline smoke-test output — significant divergence is expected and acceptable; what matters is that every numeric / qualitative claim resolves to a primary-source URL.
4. Append a verification artifact alongside the original output, e.g. `outputs/<test_case>_verified_YYYY-MM-DD.md`. (U1, U2, U4, P1, P3 already have this pattern from initial verification passes.)
5. Update `skill_build_state.json` with a note describing the live-source validation result.

**Recommended order** for Tier-2 promotion (highest leverage first):
1. **P3** on Forager (RPAY dossier flagged this as needing verification — direct alpha unlock).
2. **P4** on BAWAG/PTSB (active merger_arb dossier — direct alpha).
3. **P1+P2** on AXSM (active PDUFA — direct alpha; also unblocks the binary_catalyst calibration loop).
4. **P5** once Q-017 CourtListener token lands.
5. **P6** on a real `takeover_candidate_scanner` output — most important for *new* candidate generation.

---

## Modifying a skill

The `skill_build_plan.json` is **immutable post-ratification** (anti-drift lock). Modifications to skills should:

- Be made by editing `SKILL.md` and helper files in place.
- Append a note to `skill_build_state.json` under that skill's entry describing the change and the new smoke-test result.
- Append a `D-NNN` entry to `02_System/engine/docs/DECISIONS.md` for any change that materially alters the methodology (e.g., new source, changed threshold, dependency change).

---

## Packaging as a Claude Code plugin

Per the original `skills/README.md` note: "Once all 13 skills are built and smoke-tested, this folder will be packaged into a `.plugin` for Claude Code portability." All 13 are built and smoke-tested, so the `skills/` subfolder is plugin-ready in shape. Use the cowork-plugin-management skill (`create-cowork-plugin`) to package it when desired.

---

## What changed at the time of archiving

- The standalone working folder `Investment tool backup skills/` is being deleted.
- All 131 files are now mirrored here under `03_Skills/skills/`.
- The build plan and build state are preserved at the archive root.
- Path references inside the SKILL.md files were not modified — they still resolve because `03_Skills/` is a sibling of `01_Opportunities/` and `02_System/` inside `Investment tool backup/`.
