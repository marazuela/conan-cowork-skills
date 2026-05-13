# INDEX — File map for every skill

Each skill lives at `skills/<skill-name>/`. The shape is always:

```
SKILL.md      ← methodology + frontmatter + worked example
helpers/*.py  ← Python helpers (py_compile-clean as of build state)
outputs/*     ← smoke-test artifacts produced during build (.md + .json)
```

Verification reports (`*_verification_report.md`) and `*_verified_*` artifacts are second-pass validations produced after the initial smoke test.

---

## Phase 1 — Universal core

### U1 — `analyze-candidate-financials/`
- `SKILL.md`
- helpers: `sloan_accruals.py`, `capital_allocation_scorecard.py`, `hidden_value_scanner.py`
- outputs: `RPAY_financial_assessment.md`, `RPAY_metrics.json`, `RPAY_financial_assessment_verified_2026-04-29.md`, `RPAY_metrics_verified_2026-04-29.json`, `RPAY_sloan_verified.json`, `U1_verification_report.md`

### U2 — `compose-thesis-with-discipline/`
- `SKILL.md`
- helpers: *(none — no helpers needed per build plan)*
- outputs: `AXSM_thesis.md`, `AXSM_thesis_verified_2026-04-29.md`, `AXSM_thesis_scenarios_verified_2026-04-29.json`, `TEST_underspecified_thesis_REFUSED.md`, `U2_verification_report.md`

### U4 — `monitor-kill-conditions/`
- `SKILL.md`
- helpers: `sweep_active_dossiers.py`, `dossier_parser.py`, `primary_source_clients.py`, `atomic_write.py`, `kill_checks_merger_arb.py`, `kill_checks_activist_governance.py`, `kill_checks_binary_catalyst.py`, `kill_checks_litigation.py`, `kill_checks_insider.py`
- outputs: `2026-04-29_kill_sweep.md`, `2026-04-29_actions.jsonl`, `U4_verification_report.md`

### P1 — `analyze-fda-approval-prospects/`
- `SKILL.md`
- helpers: `analyze.py`, `fetch_trial_data.py`, `endpoint_integrity_check.py`, `adcom_history_lookup.py`, `probability_synthesizer.py`
- outputs: `AXS-05_approval_analysis.md`, `AXS-05_probability_estimate.json`, `AXS-05_approval_analysis_verified_2026-04-29.md`, `AXS-05_probability_estimate_verified_2026-04-29.json`, `P1_verification_report.md`

### P2 — `research-clinical-class-precedent/`
- `SKILL.md`
- helpers: `analyze.py`, `class_atlas.py`, `fda_class_lookup.py`, `adcom_class_history.py`, `company_fda_history.py`
- outputs: `AXS-05_class_precedent.md`, `AXS-05_class_basrates.json`

---

## Phase 2 — Per-profile counterparty intelligence

### P3 — `research-activist-filer/`
- `SKILL.md`
- helpers: `analyze.py`, `atomic_write.py`, `edgar_filer_history.py`, `campaign_outcome_resolver.py`, `tier1_benchmark_data.py`, `sic_sector_map.py`
- outputs: `forager_fund_l_p_track_record.md`, `forager_fund_l_p_campaigns.json`, `forager_fund_l_p_track_record_verified_2026-04-29.md`, `forager_fund_l_p_campaigns_verified_2026-04-29.json`, `P3_verification_report.md`

### P4 — `research-acquirer-history/`
- `SKILL.md`
- helpers: `analyze.py`, `atomic_write.py`, `acquirer_ma_history.py`, `regulatory_outcome_tracker.py`, `mac_clause_extractor.py`, `tier1_acquirer_benchmark.py`
- outputs: `bawag_group_ag_ma_history.md`, `bawag_group_ag_deals.json`

### P5 — `analyze-litigation-expected-value/`
- `SKILL.md`
- helpers: `analyze.py`, `atomic_write.py`, `case_outcome_tree.py`, `precedent_settlements.py`, `discount_rate_calc.py`, `courtlistener_client.py`
- outputs: `1_24_cv_04563_ev_analysis.md`, `1_24_cv_04563_outcome_tree.json`, `lr_26539_ev_analysis.md`, `lr_26539_outcome_tree.json`

---

## Phase 3 — Methodology infrastructure (calibration trio)

### M1 — `harvest-historical-events/`
- `SKILL.md`
- helpers: `harvest.py`, `atomic_write.py`, `edgar_fulltext_search.py`, `figi_resolver.py`, `event_dedupe.py`, `profile_defaults.py`
- outputs: `merger_arb_merger_arb_2020-01-01_2024-12-31_51947cf8_events.json`, `..._checkpoint.json`

### M2 — `label-outcomes-from-prices/`
- `SKILL.md`
- helpers: `label.py`, `atomic_write.py`, `yfinance_fetch.py`, `corporate_actions.py`, `profile_thresholds.py`
- outputs: `..._outcomes.json`, `..._outcomes_checkpoint.json`, `..._outcomes_summary.md`

### M3 — `extract-event-features/`
- `SKILL.md`
- helpers: `extract.py`, `atomic_write.py`, `feature_extractors_merger_arb.py`, `feature_extractors_activist_governance.py`, `feature_extractors_binary_catalyst.py`, `feature_extractors_insider.py`, `feature_extractors_litigation.py`, `leakage_check.py`, `feature_dictionary_writer.py`
- outputs: `..._features.json`, `merger_arb_feature_dictionary.md`

---

## Phase 4 — Synthesis + forward-looking discovery

### U3 — `compare-to-historical-precedents/`
- `SKILL.md`
- helpers: `analyze.py`, `atomic_write.py`, `knn_distance.py`, `reference_class_aggregator.py`, `sparse_handling.py`
- outputs: `RPAY_activist_governance_precedents.md`, `RPAY_activist_governance_knn.json`, `CERO_TEST_merger_arb_precedents.md`, `CERO_TEST_merger_arb_knn.json`

### P6 — `assess-takeover-vulnerability/`
- `SKILL.md`
- helpers: `analyze.py`, `atomic_write.py`, `ownership_concentration.py`, `board_defenses_extractor.py`, `comp_multiples.py`
- outputs: `PASG_vulnerability.md`, `PASG_acquirer_set.json`, `GIPR_vulnerability.md`, `GIPR_acquirer_set.json`

---

## Top-level archive files

| File | Purpose |
|---|---|
| `README.md` | Master entry point — start here |
| `STATUS.md` | Skill-by-skill ready vs needs-validation assessment |
| `INDEX.md` | This file |
| `HOW_TO_USE.md` | How to invoke or rebuild a skill |
| `skill_build_plan.json` | Immutable plan ratified 2026-04-29 by Pedro |
| `skill_build_state.json` | Per-skill completion record (status, attempts, smoke-test results, notes) |
| `working_folder_CLAUDE_reference.md` | Working folder's CLAUDE.md, kept for historical reference |

**Total**: 13 SKILL.md files + ~70 helper Python files + ~50 output artifacts = 131 files (excluding `__pycache__` / `*.pyc`).
