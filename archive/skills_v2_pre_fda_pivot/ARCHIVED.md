# skills_v2 — pre-FDA-pivot bundles (archived 2026-05-13)

This directory holds the May 5 v2 analyst-toolkit scaffolding that was abandoned
when Conan pivoted to FDA-only depth on 2026-05-06.

**Three bundles were promoted** out of `skills_v2/` and now live under `skills/`:

- `assess-fda-binary-catalyst/` — single-shot FDA convergence assessment
- `analyze-fda-approval-prospects/` (P1) — trial forensics + AdCom/label/CMC risk
- `compare-to-historical-precedents/` (U3) — K-NN over historical FDA outcomes

The bundles preserved here are kept for reference only. Nothing in the live tree
calls them. The `_shared/env_resolver.py` and `_meta/path_validation.py` helpers
were specific to the abandoned 4-root v2 environment topology
(`CONAN_REFERENCE_ROOT`, `CONAN_DOSSIERS_ROOT`, `CONAN_OUTPUTS_ROOT`) and are
no longer set on either runner.

The original v2 design state files preserved alongside this notice:
- `STATUS.md` — May 5 build status of the 14 bundles
- `INDEX.md`, `HOW_TO_USE.md`, `README.md` — original v2 docs
- `skill_build_plan.json`, `skill_build_state.json` — build-tracker artifacts

If you need to resurrect any of these bundles, expect to:
1. Re-port their helper imports (most reference `_shared/env_resolver`).
2. Re-decide `outputs/` mirroring (Supabase Storage was the v2 plan).
3. Update `reference/v2/` content paths — the v2 reference tree was never
   created in this repo; v1 reference content lives at the repo's
   `reference/spec.md` + `reference/CONAN_SCORING_METHOD.md`.
