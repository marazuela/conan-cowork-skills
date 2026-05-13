"""Phase 0d acceptance gate: validate that v2 skill path conventions resolve.

Run from any cwd; uses _shared/env_resolver to derive expected paths and reports
which roots, reference subdirs, and per-skill output dirs exist vs are missing.

Exit code 0 = all required roots resolve and required reference content exists.
Exit code 1 = at least one required path is missing; remediation hints printed.

Usage:
    cd /Users/Pico/Documents/Claude/Projects/conan-cowork-skills
    python3 skills_v2/_meta/path_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make _shared importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import env_resolver  # noqa: E402


# --- Required reference content (Phase 0c porting checklist) ---

REQUIRED_FRAMEWORK_FILES = [
    "profile_merger_arb.md",
    "profile_activist_governance.md",
    "profile_binary_catalyst.md",
    "profile_litigation.md",
    "profile_insider.md",
    "candidate_template.md",
    "profile_adjustments.md",
]

REQUIRED_TRAINING_FILES = [
    "historical_events_ledger.json",
    "iteration_4_merger_arb_features.json",
    "iteration_4_activist_features.json",
    "iteration_4_insider_features.json",
    "iteration_4_biotech_prospective_features.json",
    "scorecard_iteration_4.md",
]

REQUIRED_STRATEGY_FILES = [
    "lit_federal.md",
    "lit_chancery.md",
    "lit_itc.md",
    "lit_ptab.md",
    "lit_sec.md",
]

REQUIRED_DOCS_FILES = [
    "INSTRUCTIONS.md",
    "feedback_primary_source_discipline.md",
    "feedback_ticker_company_names.md",
    "feedback_edit_tool_null_padding.md",
    "feedback_folder_write_scope.md",
]

# --- Active dossiers (Pedro's 8 live positions) ---

REQUIRED_ACTIVE_DOSSIERS = [
    "RPAY", "AXSM", "PTSB", "VERA", "VRDN", "RGR", "6027", "LKQ",
]

# --- v2 skill names (per skill_build_plan.json) ---

V2_SKILLS = [
    "analyze-candidate-financials",
    "compose-thesis-with-discipline",
    "monitor-kill-conditions",
    "analyze-fda-approval-prospects",
    "research-clinical-class-precedent",
    "research-activist-filer",
    "research-acquirer-history",
    "analyze-litigation-expected-value",
    "assess-takeover-vulnerability",
    "harvest-historical-events",
    "label-outcomes-from-prices",
    "extract-event-features",
    "compare-to-historical-precedents",
]


def _check(label: str, path: Path) -> bool:
    if path.exists():
        print(f"  OK   {label}: {path}")
        return True
    print(f"  MISS {label}: {path}")
    return False


def main() -> int:
    print("=== v2 path validation ===")
    print()
    print("Roots:")
    for k, v in env_resolver.describe_environment().items():
        exists = Path(v).exists()
        marker = "OK  " if exists else "MISS"
        print(f"  {marker} {k}: {v}")

    failures = 0

    print()
    print("Reference framework/ files:")
    for name in REQUIRED_FRAMEWORK_FILES:
        if not _check(name, env_resolver.find_reference_root() / "framework" / name):
            failures += 1

    print()
    print("Reference training/ files:")
    for name in REQUIRED_TRAINING_FILES:
        if not _check(name, env_resolver.find_reference_root() / "training" / name):
            failures += 1

    print()
    print("Reference strategies/ files:")
    for name in REQUIRED_STRATEGY_FILES:
        if not _check(name, env_resolver.find_reference_root() / "strategies" / name):
            failures += 1

    print()
    print("Reference docs/ files:")
    for name in REQUIRED_DOCS_FILES:
        if not _check(name, env_resolver.find_reference_root() / "docs" / name):
            failures += 1

    print()
    print("HALT_FLAG (presence-only):")
    halt = env_resolver.find_halt_flag()
    print(f"  {'EXISTS' if halt.exists() else 'absent'} {halt}")
    # HALT_FLAG absence is fine; presence means halted

    print()
    print("Active dossiers:")
    for ticker in REQUIRED_ACTIVE_DOSSIERS:
        if not _check(ticker, env_resolver.find_dossier(ticker)):
            failures += 1

    print()
    print("v2 skill bundles + outputs dirs:")
    cowork_root = env_resolver.find_cowork_root()
    for skill in V2_SKILLS:
        skill_dir = cowork_root / "skills_v2" / skill
        if not _check(skill, skill_dir / "SKILL.md"):
            failures += 1
        # outputs dir is auto-created by find_skill_outputs_dir; no fail if missing

    print()
    if failures:
        print(f"FAIL: {failures} required path(s) missing.")
        print("Remediation: see Phase 0c in plan; port the missing files from")
        print("Pedro's offline workspace or set the corresponding env var.")
        return 1
    print("PASS: all required paths resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
