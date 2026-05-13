"""tier1_benchmark_data.py — Hardcoded tier-1 activist reference table.

Used by P3 (research-activist-filer) for tier classification and benchmark
comparison. Approximate campaign counts and success rates; numbers are
order-of-magnitude reference points, not authoritative — they are intended
only to flag whether a filer is in the well-known activist universe.

Maintenance: when a tier-1 activist's profile materially changes (e.g.,
firm winds down, success rate revised by sector study), update the entry
and bump LAST_UPDATED.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

LAST_UPDATED = "2026-04-29"

TIER_1_ACTIVISTS: List[Dict] = [
    {
        "canonical_name": "Elliott Investment Management",
        "aliases": [
            "Elliott Investment Management",
            "Elliott Management",
            "Elliott Associates",
            "Elliott International",
            "Paul Singer",
        ],
        "approx_campaigns": 200,
        "approx_success_rate": 0.65,
        "tier": 1,
        "notes": "Largest activist by AUM and campaign count; multi-strategy including credit",
    },
    {
        "canonical_name": "Icahn Enterprises",
        "aliases": [
            "Icahn Enterprises",
            "Carl Icahn",
            "Icahn Capital",
            "High River Limited Partnership",
            "Hopper Investments",
        ],
        "approx_campaigns": 150,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "Decades-long campaign history; known for confrontational tactics",
    },
    {
        "canonical_name": "Starboard Value",
        "aliases": ["Starboard Value", "Starboard Value LP", "Jeffrey Smith"],
        "approx_campaigns": 100,
        "approx_success_rate": 0.60,
        "tier": 1,
        "notes": "Operationally-focused activist; common in tech and consumer",
    },
    {
        "canonical_name": "ValueAct Capital",
        "aliases": ["ValueAct Capital", "ValueAct Holdings", "Mason Morfit"],
        "approx_campaigns": 60,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "Constructive activist; frequently takes board seats",
    },
    {
        "canonical_name": "Trian Fund Management",
        "aliases": ["Trian Fund Management", "Trian Partners", "Nelson Peltz"],
        "approx_campaigns": 30,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "Concentrated portfolio; long holding periods",
    },
    {
        "canonical_name": "Pershing Square Capital Management",
        "aliases": [
            "Pershing Square Capital Management",
            "Pershing Square",
            "Bill Ackman",
            "William Ackman",
        ],
        "approx_campaigns": 25,
        "approx_success_rate": 0.50,
        "tier": 1,
        "notes": "Concentrated, high-profile; mixed historical record",
    },
    {
        "canonical_name": "Jana Partners",
        "aliases": ["Jana Partners", "JANA Partners", "Barry Rosenstein"],
        "approx_campaigns": 50,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "ESG-focused activist plays in recent years",
    },
    {
        "canonical_name": "Engaged Capital",
        "aliases": ["Engaged Capital", "Glenn Welling"],
        "approx_campaigns": 30,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "Small/mid-cap focused",
    },
    {
        "canonical_name": "Blue Harbour Group",
        "aliases": ["Blue Harbour Group", "Blue Harbour", "Clifton Robbins"],
        "approx_campaigns": 25,
        "approx_success_rate": 0.55,
        "tier": 1,
        "notes": "Wound down 2019; legacy reference for older campaigns",
    },
    {
        "canonical_name": "Cevian Capital",
        "aliases": ["Cevian Capital", "Christer Gardell"],
        "approx_campaigns": 30,
        "approx_success_rate": 0.50,
        "tier": 1,
        "notes": "Europe-focused",
    },
]

TIER_1_MIN_CAMPAIGNS = 10
TIER_1_MIN_SUCCESS_RATE = 0.55


def _normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s or "")).strip().lower()


def match_tier_1(filer_name: str, aliases: Optional[List[str]] = None) -> Optional[Dict]:
    """Return the matching tier-1 entry if filer_name (or any alias) corresponds
    to a known tier-1 activist. Strict full-name match required to avoid
    false positives like "Elliott Capital LLC" → Elliott Investment Management.
    """
    candidates = [filer_name]
    if aliases:
        candidates.extend(aliases)
    cand_norm = {_normalize(c) for c in candidates if c}

    for entry in TIER_1_ACTIVISTS:
        for alias in entry["aliases"]:
            if _normalize(alias) in cand_norm:
                return entry
    return None


def classify(filer_name: str, n_campaigns: int, success_rate: float, current_target_in_list: bool, aliases: Optional[List[str]] = None) -> Dict:
    """Apply the tier classification decision tree from P3 SKILL.md Step 7.

    Returns: {"tier_classification": ..., "tier_match": ..., "rationale": ...}
    """
    tier_match = match_tier_1(filer_name, aliases)

    if tier_match:
        return {
            "tier_classification": "tier_1",
            "tier_match_canonical_name": tier_match["canonical_name"],
            "tier_match_approx_campaigns": tier_match["approx_campaigns"],
            "tier_match_approx_success_rate": tier_match["approx_success_rate"],
            "rationale": f"Filer name matches tier-1 activist '{tier_match['canonical_name']}' on canonical alias",
        }

    if n_campaigns >= 5 and (success_rate is not None and success_rate >= 0.50):
        return {
            "tier_classification": "established",
            "tier_match_canonical_name": None,
            "rationale": f"n_campaigns={n_campaigns} ≥ 5 AND success_rate={success_rate:.2f} ≥ 0.50 — established activist",
        }

    if n_campaigns >= 1:
        return {
            "tier_classification": "emerging",
            "tier_match_canonical_name": None,
            "rationale": f"n_campaigns={n_campaigns} (<5 or success_rate<0.50) — emerging activist",
        }

    if n_campaigns == 1 and current_target_in_list:
        return {
            "tier_classification": "first_time",
            "tier_match_canonical_name": None,
            "rationale": "Only one campaign — the current target — first-time activist signal",
        }

    return {
        "tier_classification": "unknown",
        "tier_match_canonical_name": None,
        "rationale": "No prior 13D campaigns resolved",
    }


def benchmark_vs_tier_1(n_campaigns: int, success_rate: Optional[float]) -> Dict:
    """Compare a filer's metrics against tier-1 thresholds."""
    sr = success_rate if success_rate is not None else 0.0
    pass_campaigns = n_campaigns >= TIER_1_MIN_CAMPAIGNS
    pass_success = sr >= TIER_1_MIN_SUCCESS_RATE
    return {
        "tier_1_minimum_campaigns": TIER_1_MIN_CAMPAIGNS,
        "tier_1_minimum_success_rate": TIER_1_MIN_SUCCESS_RATE,
        "this_filer_n_campaigns": n_campaigns,
        "this_filer_success_rate": success_rate,
        "this_filer_vs_tier_1": "at_or_above_threshold" if (pass_campaigns and pass_success) else "below_threshold",
    }


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser()
    p.add_argument("--filer", required=True)
    p.add_argument("--n-campaigns", type=int, default=0)
    p.add_argument("--success-rate", type=float, default=0.0)
    p.add_argument("--current-target-in-list", action="store_true")
    args = p.parse_args()

    cls = classify(args.filer, args.n_campaigns, args.success_rate, args.current_target_in_list)
    bench = benchmark_vs_tier_1(args.n_campaigns, args.success_rate)
    print(json.dumps({"classification": cls, "benchmark": bench}, indent=2))
