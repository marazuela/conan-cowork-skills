"""Per-profile defaults for harvest-historical-events.

Each profile maps to a list of filing-types and a primary source. Used when
the orchestrator is invoked without an explicit `filing_types` argument.

Per skill_build_plan.json M1 spec and CLAUDE.md §2 in-scope_profiles list.
"""

from __future__ import annotations

PROFILE_DEFAULTS = {
    "merger_arb": {
        "source": "edgar_fulltext",
        "filing_types": ["DEFM14A", "S-4", "S-4/A", "SC TO-T", "SC TO-I", "SC 13E3"],
        "bucket": "ma",
    },
    "activist_governance": {
        "source": "edgar_fulltext",
        "filing_types": ["SC 13D", "SC 13D/A", "PRRN14A"],
        "bucket": "activist",
    },
    "insider": {
        "source": "edgar_submissions",
        "filing_types": ["4"],
        "bucket": "insider",
    },
    "binary_catalyst": {
        "source": "fda_drugsfda",
        "filing_types": ["NDA", "BLA", "sNDA", "sBLA"],
        "bucket": "biotech",
    },
    "litigation": {
        "source": "courtlistener",
        "filing_types": ["securities", "patent", "antitrust", "breach", "337", "ptab", "delaware-chancery"],
        "bucket": "litigation",
    },
}


def defaults_for(profile: str) -> dict:
    """Return the default config for a profile.

    Raises KeyError on unknown / out-of-scope profiles (e.g., short_positioning).
    """
    if profile not in PROFILE_DEFAULTS:
        raise KeyError(
            f"Unknown or out-of-scope profile: {profile!r}. "
            f"In-scope: {list(PROFILE_DEFAULTS.keys())}"
        )
    return PROFILE_DEFAULTS[profile]


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(json.dumps(PROFILE_DEFAULTS, indent=2))
    else:
        print(json.dumps(defaults_for(sys.argv[1]), indent=2))
