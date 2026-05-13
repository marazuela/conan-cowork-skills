"""Reference-class aggregator for K-NN neighbors.

Computes outcome distribution, hit rate, similarity-weighted hit rate,
median + percentile returns, and synthetic-return imputation handling
when neighbors lack concrete return_pct.
"""
from __future__ import annotations

from typing import Any


SYNTHETIC_RETURN_BY_LABEL: dict[str, float] = {
    "HIT": 0.20,
    "MISS": -0.10,
    "PARTIAL": 0.05,
}


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def aggregate_neighbors(neighbors: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate K-NN neighbors into base-rate statistics.

    Each neighbor must have: outcome_label, similarity_score, optionally return_pct.
    """
    if not neighbors:
        return {
            "outcome_distribution": {},
            "hit_rate": None,
            "hit_rate_similarity_weighted": None,
            "median_return": None,
            "mean_return_similarity_weighted": None,
            "return_pct_p10": None,
            "return_pct_p90": None,
            "n_with_return": 0,
            "synthetic_return_imputation": False,
        }

    # Outcome distribution
    distribution: dict[str, int] = {}
    for n in neighbors:
        lbl = n.get("outcome_label", "UNKNOWN")
        distribution[lbl] = distribution.get(lbl, 0) + 1

    n_total = len(neighbors)
    hits = distribution.get("HIT", 0)
    hit_rate = hits / n_total if n_total else None

    # Similarity-weighted hit rate
    sim_total = sum(n.get("similarity_score", 0.0) for n in neighbors)
    sim_hit = sum(
        n.get("similarity_score", 0.0)
        for n in neighbors
        if n.get("outcome_label") == "HIT"
    )
    hit_rate_sim = sim_hit / sim_total if sim_total > 0 else None

    # Returns
    concrete_returns: list[float] = []
    concrete_returns_with_sim: list[tuple[float, float]] = []
    for n in neighbors:
        r = n.get("return_pct")
        if r is not None:
            try:
                rf = float(r)
                concrete_returns.append(rf)
                concrete_returns_with_sim.append((rf, float(n.get("similarity_score", 0.0))))
            except (TypeError, ValueError):
                pass

    n_with_return = len(concrete_returns)
    synthetic_imputation = False
    if n_with_return < 0.4 * n_total:
        # >60% missing — fall back to synthetic returns by label
        synthetic_imputation = True
        synth_returns: list[float] = []
        synth_with_sim: list[tuple[float, float]] = []
        for n in neighbors:
            r = n.get("return_pct")
            if r is not None:
                try:
                    rf = float(r)
                except (TypeError, ValueError):
                    rf = SYNTHETIC_RETURN_BY_LABEL.get(n.get("outcome_label", ""), 0.0)
            else:
                rf = SYNTHETIC_RETURN_BY_LABEL.get(n.get("outcome_label", ""), 0.0)
            synth_returns.append(rf)
            synth_with_sim.append((rf, float(n.get("similarity_score", 0.0))))
        return_set = synth_returns
        return_set_with_sim = synth_with_sim
    else:
        return_set = concrete_returns
        return_set_with_sim = concrete_returns_with_sim

    # Sort for percentiles
    return_set_sorted = sorted(return_set)
    median_return = _percentile(return_set_sorted, 0.5)
    p10 = _percentile(return_set_sorted, 0.10)
    p90 = _percentile(return_set_sorted, 0.90)

    # Similarity-weighted mean return
    if return_set_with_sim:
        wsum = sum(sim for _, sim in return_set_with_sim)
        if wsum > 0:
            mean_w = sum(r * sim for r, sim in return_set_with_sim) / wsum
        else:
            mean_w = sum(r for r, _ in return_set_with_sim) / len(return_set_with_sim)
    else:
        mean_w = None

    return {
        "outcome_distribution": distribution,
        "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
        "hit_rate_similarity_weighted": (
            round(hit_rate_sim, 4) if hit_rate_sim is not None else None
        ),
        "median_return": round(median_return, 4) if median_return is not None else None,
        "mean_return_similarity_weighted": round(mean_w, 4) if mean_w is not None else None,
        "return_pct_p10": round(p10, 4) if p10 is not None else None,
        "return_pct_p90": round(p90, 4) if p90 is not None else None,
        "n_with_return": n_with_return,
        "synthetic_return_imputation": synthetic_imputation,
    }


if __name__ == "__main__":
    # Self-test
    sample = [
        {"outcome_label": "HIT", "similarity_score": 0.8, "return_pct": 0.18},
        {"outcome_label": "HIT", "similarity_score": 0.7, "return_pct": 0.41},
        {"outcome_label": "MISS", "similarity_score": 0.5, "return_pct": -0.12},
        {"outcome_label": "HIT", "similarity_score": 0.4, "return_pct": None},
        {"outcome_label": "PARTIAL", "similarity_score": 0.3, "return_pct": 0.05},
    ]
    import json
    print(json.dumps(aggregate_neighbors(sample), indent=2))
