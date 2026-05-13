"""tier1_acquirer_benchmark.py — Hardcoded tier-1 acquirer reference tables.

Used by P4 (research-acquirer-history) for tier classification and benchmark
comparison. Approximate prior-deal counts and close rates; numbers are
order-of-magnitude reference points, not authoritative — they are intended
only to flag whether an acquirer is in the well-known M&A universe.

Maintenance: when a tier-1 acquirer's profile materially changes (e.g., a
strategic acquirer winds down its M&A program, a PE shop's close rate is
materially revised by a sector study), update the entry and bump LAST_UPDATED.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

LAST_UPDATED = "2026-04-29"

TIER_1_STRATEGIC: List[Dict] = [
    {
        "canonical_name": "Microsoft Corporation",
        "aliases": ["Microsoft Corporation", "Microsoft", "MSFT"],
        "approx_deals": 30,
        "approx_close_rate": 0.92,
        "tier": 1,
        "kind": "strategic",
        "notes": "Tech rollup; high close rate; selective on regulatory risk (Activision proved exception)",
    },
    {
        "canonical_name": "Berkshire Hathaway Inc.",
        "aliases": ["Berkshire Hathaway Inc.", "Berkshire Hathaway", "BRK", "BRK.A", "BRK.B", "Warren Buffett"],
        "approx_deals": 80,
        "approx_close_rate": 0.95,
        "tier": 1,
        "kind": "strategic",
        "notes": "Friendly-only acquirer; record close rate; cash-funded balance-sheet deals",
    },
    {
        "canonical_name": "JPMorgan Chase & Co.",
        "aliases": ["JPMorgan Chase & Co.", "JPMorgan Chase", "JP Morgan Chase", "JPMorgan", "JPM"],
        "approx_deals": 40,
        "approx_close_rate": 0.85,
        "tier": 1,
        "kind": "strategic",
        "notes": "Financial-services consolidator; FRB / OCC familiarity",
    },
    {
        "canonical_name": "Constellation Software Inc.",
        "aliases": ["Constellation Software Inc.", "Constellation Software", "CSU"],
        "approx_deals": 200,
        "approx_close_rate": 0.90,
        "tier": 1,
        "kind": "strategic",
        "notes": "Vertical-software roll-up, hundreds of bolt-ons",
    },
    {
        "canonical_name": "Roper Technologies, Inc.",
        "aliases": ["Roper Technologies, Inc.", "Roper Technologies", "Roper Industries", "ROP"],
        "approx_deals": 60,
        "approx_close_rate": 0.88,
        "tier": 1,
        "kind": "strategic",
        "notes": "Diversified industrials roll-up",
    },
    {
        "canonical_name": "Danaher Corporation",
        "aliases": ["Danaher Corporation", "Danaher", "DHR"],
        "approx_deals": 40,
        "approx_close_rate": 0.85,
        "tier": 1,
        "kind": "strategic",
        "notes": "Industrial/life-science conglomerate",
    },
    {
        "canonical_name": "Unilever PLC",
        "aliases": ["Unilever PLC", "Unilever", "UL", "ULVR"],
        "approx_deals": 30,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "strategic",
        "notes": "CPG roll-up; cross-border familiarity",
    },
    {
        "canonical_name": "LVMH Moet Hennessy Louis Vuitton",
        "aliases": ["LVMH Moet Hennessy Louis Vuitton", "LVMH", "Moet Hennessy"],
        "approx_deals": 25,
        "approx_close_rate": 0.85,
        "tier": 1,
        "kind": "strategic",
        "notes": "Luxury-goods roll-up",
    },
]

TIER_1_PE: List[Dict] = [
    {
        "canonical_name": "Kohlberg Kravis Roberts & Co.",
        "aliases": ["Kohlberg Kravis Roberts & Co.", "KKR & Co. Inc.", "KKR", "KKR & Co"],
        "approx_deals": 100,
        "approx_close_rate": 0.85,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Blackstone Inc.",
        "aliases": ["Blackstone Inc.", "Blackstone Group", "Blackstone", "BX"],
        "approx_deals": 100,
        "approx_close_rate": 0.85,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Apollo Global Management",
        "aliases": ["Apollo Global Management", "Apollo Global Management Inc.", "Apollo Management", "APO"],
        "approx_deals": 80,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "The Carlyle Group",
        "aliases": ["The Carlyle Group", "Carlyle Group", "Carlyle", "CG"],
        "approx_deals": 80,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "TPG Inc.",
        "aliases": ["TPG Inc.", "TPG Capital", "TPG", "Texas Pacific Group"],
        "approx_deals": 70,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Bain Capital",
        "aliases": ["Bain Capital", "Bain Capital LP", "Bain Capital Partners"],
        "approx_deals": 60,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Clayton, Dubilier & Rice",
        "aliases": ["Clayton, Dubilier & Rice", "Clayton Dubilier & Rice", "CD&R"],
        "approx_deals": 50,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Advent International",
        "aliases": ["Advent International", "Advent International Corporation"],
        "approx_deals": 50,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
    {
        "canonical_name": "Permira",
        "aliases": ["Permira", "Permira Advisers"],
        "approx_deals": 40,
        "approx_close_rate": 0.80,
        "tier": 1,
        "kind": "pe",
    },
]

TIER_1_STRATEGIC_MIN_DEALS = 10
TIER_1_STRATEGIC_MIN_CLOSE_RATE = 0.85
TIER_1_PE_MIN_DEALS = 20
TIER_1_PE_MIN_CLOSE_RATE = 0.80

# heuristic markers in acquirer name suggesting PE / financial sponsor structure
PE_NAME_MARKERS = [
    " lp", " l.p.", " partners", " capital", " fund", " advisors",
    " holdings", " sponsor", "private equity", " investment management",
]


def _normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s or "")).strip().lower()


def is_pe_acquirer(name: str) -> bool:
    n = (name or "").lower()
    return any(marker in n for marker in PE_NAME_MARKERS)


def match_tier_1(acquirer_name: str, aliases: Optional[List[str]] = None) -> Optional[Dict]:
    """Return the matching tier-1 entry (strategic OR PE) if acquirer_name (or any alias)
    corresponds to a known tier-1 acquirer. Strict full-name match required.
    """
    candidates = [acquirer_name]
    if aliases:
        candidates.extend(aliases)
    cand_norm = {_normalize(c) for c in candidates if c}

    for entry in TIER_1_STRATEGIC + TIER_1_PE:
        for alias in entry["aliases"]:
            if _normalize(alias) in cand_norm:
                return entry
    return None


def classify(
    acquirer_name: str,
    n_prior_deals: int,
    close_rate: Optional[float],
    current_target_in_list: bool,
    aliases: Optional[List[str]] = None,
    force_kind: Optional[str] = None,
) -> Dict:
    """Apply tier classification decision tree from P4 SKILL.md Step 9.

    Returns: {"tier_classification": ..., "tier_match": ..., "rationale": ..., "kind": ...}
    """
    tier_match = match_tier_1(acquirer_name, aliases)
    if tier_match:
        return {
            "tier_classification": "tier_1_" + tier_match["kind"],
            "tier_match_canonical_name": tier_match["canonical_name"],
            "tier_match_approx_deals": tier_match["approx_deals"],
            "tier_match_approx_close_rate": tier_match["approx_close_rate"],
            "kind": tier_match["kind"],
            "rationale": f"Acquirer name matches tier-1 {tier_match['kind']} acquirer "
            f"'{tier_match['canonical_name']}' on canonical alias",
        }

    kind = force_kind or ("pe" if is_pe_acquirer(acquirer_name) else "strategic")
    cr = close_rate if close_rate is not None else 0.0

    if kind == "strategic" and n_prior_deals >= 5 and cr >= 0.80:
        return {
            "tier_classification": "established_strategic",
            "tier_match_canonical_name": None,
            "kind": "strategic",
            "rationale": f"n_prior_deals={n_prior_deals} ≥ 5 AND close_rate={cr:.2f} ≥ 0.80 — established strategic acquirer",
        }
    if kind == "pe" and n_prior_deals >= 10 and cr >= 0.75:
        return {
            "tier_classification": "established_pe",
            "tier_match_canonical_name": None,
            "kind": "pe",
            "rationale": f"n_prior_deals={n_prior_deals} ≥ 10 AND close_rate={cr:.2f} ≥ 0.75 — established PE sponsor",
        }
    if n_prior_deals >= 1:
        return {
            "tier_classification": "emerging",
            "tier_match_canonical_name": None,
            "kind": kind,
            "rationale": f"n_prior_deals={n_prior_deals} (<5/<10 or close_rate below threshold) — emerging acquirer",
        }
    if n_prior_deals == 0 and current_target_in_list:
        return {
            "tier_classification": "first_time",
            "tier_match_canonical_name": None,
            "kind": kind,
            "rationale": "No prior deals — first-time acquirer signal",
        }
    return {
        "tier_classification": "unknown",
        "tier_match_canonical_name": None,
        "kind": kind,
        "rationale": "No prior M&A activity resolved",
    }


def benchmark_vs_tier_1(n_prior_deals: int, close_rate: Optional[float], kind: str = "strategic") -> Dict:
    """Compare an acquirer's metrics against tier-1 thresholds."""
    cr = close_rate if close_rate is not None else 0.0
    if kind == "pe":
        min_deals = TIER_1_PE_MIN_DEALS
        min_cr = TIER_1_PE_MIN_CLOSE_RATE
    else:
        min_deals = TIER_1_STRATEGIC_MIN_DEALS
        min_cr = TIER_1_STRATEGIC_MIN_CLOSE_RATE

    pass_deals = n_prior_deals >= min_deals
    pass_cr = cr >= min_cr

    if pass_deals and pass_cr:
        verdict = "at_or_above_threshold"
    elif not pass_deals and pass_cr:
        verdict = "below_deal_count_threshold"
    elif pass_deals and not pass_cr:
        verdict = "below_close_rate_threshold"
    else:
        verdict = "below_threshold"

    return {
        "tier_1_strategic_minimum_deals": TIER_1_STRATEGIC_MIN_DEALS,
        "tier_1_strategic_minimum_close_rate": TIER_1_STRATEGIC_MIN_CLOSE_RATE,
        "tier_1_pe_minimum_deals": TIER_1_PE_MIN_DEALS,
        "tier_1_pe_minimum_close_rate": TIER_1_PE_MIN_CLOSE_RATE,
        "this_acquirer_n_prior_deals": n_prior_deals,
        "this_acquirer_close_rate": close_rate,
        "this_acquirer_kind": kind,
        "this_acquirer_vs_tier_1": verdict,
    }


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser()
    p.add_argument("--acquirer", required=True)
    p.add_argument("--n-prior-deals", type=int, default=0)
    p.add_argument("--close-rate", type=float, default=0.0)
    p.add_argument("--current-target-in-list", action="store_true")
    p.add_argument("--kind", choices=["strategic", "pe"], default=None)
    args = p.parse_args()

    cls = classify(args.acquirer, args.n_prior_deals, args.close_rate, args.current_target_in_list, force_kind=args.kind)
    bench = benchmark_vs_tier_1(args.n_prior_deals, args.close_rate, kind=cls["kind"])
    print(json.dumps({"classification": cls, "benchmark": bench}, indent=2))
