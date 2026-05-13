"""Single source of truth for v2 path resolution.

All v2 skills resolve filesystem paths through these helpers, not via hardcoded
strings. The four roots are configurable via env vars; defaults assume the
canonical conan-cowork-skills layout.

Env vars (set on each runner machine; documented in conan-cowork-skills/README.md):

    CONAN_COWORK_ROOT     base of the conan-cowork-skills checkout
    CONAN_REFERENCE_ROOT  reference content (framework/, training/, docs/, ...)
                          default: ${CONAN_COWORK_ROOT}/reference/v2
    CONAN_DOSSIERS_ROOT   active + archived dossiers
                          default: ${CONAN_COWORK_ROOT}/dossiers
    CONAN_OUTPUTS_ROOT    per-skill output dir (gitignored)
                          default: ${CONAN_COWORK_ROOT}/outputs

Skills should never hardcode `Investment tool backup/...` or absolute paths.
Use these accessors so the same skill body runs on Pedro's Mac, JGoror's
Windows, and any future runner.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathResolutionError(RuntimeError):
    """Raised when a required env var is unset and no default works."""


def _from_env(name: str, default: Path | None) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value).expanduser().resolve()
    if default is not None:
        return default.resolve()
    raise PathResolutionError(
        f"Env var {name} is unset and no default could be derived. "
        "Set it in your shell rc per conan-cowork-skills/README.md."
    )


def find_cowork_root() -> Path:
    """Return $CONAN_COWORK_ROOT or the parent of this file's package."""
    env = os.environ.get("CONAN_COWORK_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # Fallback: this file lives at <root>/skills_v2/_shared/env_resolver.py
    return Path(__file__).resolve().parents[2]


def find_reference_root() -> Path:
    """Return $CONAN_REFERENCE_ROOT or ${cowork_root}/reference/v2."""
    return _from_env("CONAN_REFERENCE_ROOT", find_cowork_root() / "reference" / "v2")


def find_dossiers_root() -> Path:
    """Return $CONAN_DOSSIERS_ROOT or ${cowork_root}/dossiers."""
    return _from_env("CONAN_DOSSIERS_ROOT", find_cowork_root() / "dossiers")


def find_outputs_root() -> Path:
    """Return $CONAN_OUTPUTS_ROOT or ${cowork_root}/outputs."""
    return _from_env("CONAN_OUTPUTS_ROOT", find_cowork_root() / "outputs")


def find_skill_outputs_dir(skill_name: str) -> Path:
    """Return ${outputs_root}/<skill_name>/, creating it if absent."""
    path = find_outputs_root() / skill_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_dossier(ticker: str, *, scope: str = "active") -> Path:
    """Return ${dossiers_root}/<scope>/<ticker>/dossier.md."""
    return find_dossiers_root() / scope / ticker / "dossier.md"


def find_halt_flag() -> Path:
    """Return ${reference_root}/health/HALT_FLAG (presence-only file)."""
    return find_reference_root() / "health" / "HALT_FLAG"


def is_halted() -> bool:
    """True iff HALT_FLAG exists. Skills check at orchestrator entry."""
    return find_halt_flag().exists()


def find_profile_doc(profile: str) -> Path:
    """Return ${reference_root}/framework/profile_<profile>.md."""
    return find_reference_root() / "framework" / f"profile_{profile}.md"


def find_training_file(name: str) -> Path:
    """Return ${reference_root}/training/<name> (e.g. historical_events_ledger.json)."""
    return find_reference_root() / "training" / name


def find_strategy_doc(name: str) -> Path:
    """Return ${reference_root}/strategies/<name> (e.g. lit_federal.md)."""
    return find_reference_root() / "strategies" / name


def describe_environment() -> dict[str, str]:
    """Diagnostic helper for path_validation.py and skill startup logs."""
    return {
        "CONAN_COWORK_ROOT": str(find_cowork_root()),
        "CONAN_REFERENCE_ROOT": str(find_reference_root()),
        "CONAN_DOSSIERS_ROOT": str(find_dossiers_root()),
        "CONAN_OUTPUTS_ROOT": str(find_outputs_root()),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(describe_environment(), indent=2))
