"""discount_rate_calc.py — NPV computation for litigation outcome tree.

Pure-functional, no I/O. Given a list of branch dicts each with
`probability`, `magnitude_to_issuer_usd`, and `time_to_resolution_days`,
compute the discounted expected value to the issuer's equity.

Default discount rate is 0.10 annualized (issuer cost of equity proxy).

Usage:
    from discount_rate_calc import compute_npv
    out = compute_npv(branches, discount_rate=0.10, materiality_denominator_usd=4_500_000_000)
    # out -> {"ev_usd": ..., "ev_pct_of_ev": ..., "branches": [...with npv...], ...}
"""

from __future__ import annotations

from typing import Dict, List, Optional


def compute_npv(
    branches: List[Dict],
    discount_rate: float = 0.10,
    materiality_denominator_usd: Optional[float] = None,
) -> Dict:
    """Compute per-branch NPV + aggregate EV.

    Returns the input branches enriched with `npv_to_issuer_usd` and
    `ev_contribution_usd`, plus aggregate `ev_usd`, `ev_pct_of_ev`, and
    `band` per profile_litigation.md Dimension-1 mapping.
    """
    enriched: List[Dict] = []
    ev_usd = 0.0
    time_weighted_days = 0.0
    weight_sum = 0.0
    for b in branches:
        p = float(b.get("probability", 0.0))
        mag = float(b.get("magnitude_to_issuer_usd", 0.0))
        t_days = float(b.get("time_to_resolution_days", 365.0))
        years = t_days / 365.0
        if 1.0 + discount_rate <= 0:
            disc_factor = 1.0
        else:
            disc_factor = (1.0 + discount_rate) ** years
        npv = mag / disc_factor if disc_factor != 0 else mag
        contribution = p * npv
        ev_usd += contribution
        # Weighted timeline: use absolute magnitude as a proxy weight to
        # surface the dominant resolution timeline; falls back to probability
        # only when all magnitudes are zero.
        weight = p * abs(mag) if abs(mag) > 0 else p
        time_weighted_days += weight * t_days
        weight_sum += weight

        enriched.append(
            {
                **b,
                "npv_to_issuer_usd": round(npv, 2),
                "ev_contribution_usd": round(contribution, 2),
            }
        )

    overall_time_days = round(time_weighted_days / weight_sum, 1) if weight_sum > 0 else None

    # Materiality band
    band = "n_a_no_denominator"
    ev_pct = None
    if materiality_denominator_usd is not None and materiality_denominator_usd > 0:
        ev_pct = abs(ev_usd) / materiality_denominator_usd
        if ev_pct > 0.20:
            band = "very_high"
        elif ev_pct > 0.10:
            band = "high"
        elif ev_pct > 0.05:
            band = "moderate"
        elif ev_pct > 0.02:
            band = "low"
        else:
            band = "minimal"
    elif all(b.get("magnitude_to_issuer_usd", 0.0) == 0.0 for b in branches):
        band = "n_a_no_public_party"

    return {
        "ev_usd": round(ev_usd, 2),
        "ev_pct_of_ev": round(ev_pct, 4) if ev_pct is not None else None,
        "band": band,
        "discount_rate": discount_rate,
        "time_to_resolution_median_days_overall": overall_time_days,
        "branches": enriched,
    }


if __name__ == "__main__":
    import json as _json
    import sys

    sample = [
        {"branch": "settled", "probability": 0.65, "magnitude_to_issuer_usd": -5e7, "time_to_resolution_days": 60},
        {"branch": "litigated_settled", "probability": 0.20, "magnitude_to_issuer_usd": -7.5e7, "time_to_resolution_days": 540},
        {"branch": "litigated_judgment_for_sec", "probability": 0.10, "magnitude_to_issuer_usd": -1.5e8, "time_to_resolution_days": 900},
        {"branch": "dismissed", "probability": 0.05, "magnitude_to_issuer_usd": 0.0, "time_to_resolution_days": 380},
    ]
    rate = float(sys.argv[1]) if len(sys.argv) > 1 else 0.10
    denom = float(sys.argv[2]) if len(sys.argv) > 2 else 4.5e9
    print(_json.dumps(compute_npv(sample, rate, denom), indent=2, default=str))
