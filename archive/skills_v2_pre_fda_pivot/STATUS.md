# STATUS — Skill-by-skill readiness assessment

**As of**: 2026-05-04
**Source of truth**: [`skill_build_state.json`](skill_build_state.json) (build-state notes per skill)

---

## Headline

All 13 skills are **scaffolded, py_compile-clean, and smoke-test-passed**. The build-state file marks every skill as `completed` with `smoke_test_result: passed`. But "smoke-test passed" is doing different work for different skills:

- **7 skills were tested against real data** — concrete artifacts produced, behaviors verified end-to-end.
- **6 skills were tested in offline / illustrative mode** — code paths exercised, but inputs were synthetic. Per CLAUDE.md §1.2 primary-source-discipline, illustrative outputs are not decision-grade and each of these skills needs one live-source validation pass before powering a real bet.

---

## Tier 1 — Ready to use today (validated against real data)

| ID | Skill | Smoke-test evidence | Confidence in production behavior |
|---|---|---|---|
| **U1** | analyze-candidate-financials | RPAY 10-K assessment + metrics.json produced; helpers (Sloan accruals, hidden-value scan, capital-allocation scorecard) executed end-to-end | High |
| **U2** | compose-thesis-with-discipline | AXSM thesis composed, all 6 fields populated, EV +16.55% above gate, recommendation `active_position`. Refusal protocol verified via `TEST_underspecified_thesis_REFUSED.md` | High |
| **U4** | monitor-kill-conditions | Swept all 8 active dossiers; 0 false positives; AXSM AdCom kill condition resolved cleanly via Federal Register API; 6027 + LKQ status drift flagged correctly; HALT_FLAG honored | High |
| **M1** | harvest-historical-events | 20 merger_arb events (DEFM14A + S-4, 2020-2024) harvested with FIGIs; 100% have CIK + filed_at + form_type + ticker; checkpointing + atomic-write verified; out-of-scope short_positioning rejected cleanly | High |
| **M2** | label-outcomes-from-prices | 11 HIT / 9 MISS labelled on M1's batch in 0.15s; all 5 forward-return windows + canonical-window outcome present; idempotent re-run produced 0 deltas; profile-mismatch error path returns `recoverable=false` | High |
| **M3** | extract-event-features | 20 features-matrix rows extracted from M2 output; 100% sidecar match; 0 imputations; all confidence at 0.85; D-097 leakage_verdict=`no_overlap` | High |
| **U3** | compare-to-historical-precedents | RPAY (activist_governance) K=5 neighbours returned (IMAX, EchoStar, Golden Matrix, Viasat, nCino — all HIT), distance 0.17–0.33, hit_rate=1.0, confidence 0.90 caller-provided / 0.54 inline-synthetic. Cross-profile sanity check on CERO (merger_arb) returned K=5 confidence 0.85 | High |

## Tier 2 — Built and smoke-tested, needs live-source validation before production use

These skills' code, helpers, and methodology are complete and py_compile-clean. The smoke tests, however, used **synthetic / illustrative inputs**. Before relying on any of these for a real allocation decision, run one end-to-end pass against a real candidate using primary sources.

| ID | Skill | Smoke-test caveat | What's needed before production use |
|---|---|---|---|
| **P1** | analyze-fda-approval-prospects | AXSM offline-mode produced P(approval)=0.70 (range 0.61–0.79), broadly consistent with the active dossier's 60–70% — but not derived from live ClinicalTrials.gov / FDA / PubMed data | Live-source run on AXSM (or another active PDUFA) — verify trial-data forensics, AdCom history lookup, CMC inspection lookup all hit primary sources |
| **P2** | research-clinical-class-precedent | AXS-05 offline produced approval_rate_class=0.667 (n=2/3 illustrative), Wilson CI 21–94%, AdCom rate 20%, sparse-class flag set | Live-source run via openFDA + Federal Register against a real drug class |
| **P3** | research-activist-filer | Forager Capital Management + RPAY produced 4 illustrative campaigns, success_rate=0.667, tier_classification=`emerging` — campaigns were synthetic, not real EDGAR pulls | Live-source EDGAR full-text search against a real activist filer (Forager is the natural choice — RPAY dossier explicitly flagged Forager track record as needing verification) |
| **P4** | research-acquirer-history | BAWAG/PTSB/IE produced 6 illustrative prior deals, 100% close rate, avg time-to-close 205d, foreign-filer fallback (`acquirer_id_type=name_only`) triggered correctly | Live-source run on a real acquirer in the active book (BAWAG → PTSB is the natural test) |
| **P5** | analyze-litigation-expected-value | LR-26539 auto-archived correctly (no publicly traded defendant); offline-illustrative happy path on synthetic securities-fraud case produced EV=-$5.82M, ev_pct=-0.13%, band=`minimal`, confidence=0.76 | (a) Provision CourtListener API token (Q-017), (b) live-source run on a real resolved securities-fraud / Delaware Chancery / SEC enforcement case |
| **P6** | assess-takeover-vulnerability | PASG (most-recent `takeover_candidate_scanner` signal) produced vulnerability_score=72.9/100, band=HIGH, 3 acquirers ranked. **All inputs imputed; confidence 0.25 floor** (offline-illustrative all-imputed signal) | Live-source run on a real `takeover_candidate_scanner` output with full primary-source pulls for ownership, board defenses, and comp multiples |

---

## How these skills map to the candidate-identification pipeline

The skill set is heavily weighted toward **diligence and synthesis** of candidates already surfaced by scanners — not toward raw discovery.

| Stage in the pipeline | Skills that serve it |
|---|---|
| **Discovery** — surface a name before scanners pick up a definitive event | **P6** only (forward-looking takeover vulnerability) |
| **Diligence** — turn a surfaced name into a defensible position | **U1, P1, P2, P3, P4, P5** (financials + per-profile forensics) |
| **Synthesis** — turn diligence into a decision with kill criteria and base rates | **U2, U3** (thesis discipline + precedent K-NN) |
| **Calibration infrastructure** — improve future scanner ranking and scoring weights via the feedback loop | **M1, M2, M3** (harvest → label → extract features for the 988-event historical ledger) |
| **Capital preservation** — free portfolio attention to absorb new candidates | **U4** (auto-archive dead names) |

Two consequences for tool redesign:

1. **If the redesign goal is more candidates, the discovery surface area is the bottleneck.** P6 is the only genuine discovery skill and is currently the lowest-confidence of the 13. Either harden P6 with live-source validation, or design new discovery skills (e.g., a forward-looking activist-target screen analogous to P6, a forward-looking PDUFA-readout screen, etc.).
2. **The calibration trio (M1/M2/M3) is decoupled from any single candidate** and powers the *quality* of all future candidate identification by feeding the historical ledger. This is the highest-leverage invisible work in the bundle — it's the skill subsystem that makes scanners and scoring weights better over time.

---

## Production-readiness checklist before deleting working folder

- [x] All 13 SKILL.md files mirrored into `03_Skills/skills/`
- [x] All helper Python files mirrored (no `__pycache__` / `*.pyc` noise)
- [x] All smoke-test outputs preserved in `outputs/` per skill
- [x] Build plan + build state preserved at archive root
- [x] Working folder's CLAUDE.md captured as historical reference
- [x] Status assessment written (this file)
- [x] File index written (`INDEX.md`)
- [x] Operational guide written (`HOW_TO_USE.md`)
- [ ] Live-source validation pass on each Tier-2 skill (P1–P6) — **future work, not blocked by archive**
