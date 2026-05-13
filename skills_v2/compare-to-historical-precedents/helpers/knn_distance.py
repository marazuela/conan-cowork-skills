"""Profile-weighted K-NN distance over iter-4 features.

The distance function is weighted Euclidean over standardized numeric features
plus weighted 0/1 mismatch over token features. Numeric standardization uses
historical mean/std from the reference universe so raw-USD scale doesn't
saturate distance vs. binary one-hots (the RAW_SCALE_DROP_FROM_DESIGN lesson
from iter-4).

Public API:
    standardize_universe(features_per_event) -> (means, stds)
    compute_distance(candidate, neighbor, means, stds, weights) -> float
    profile_weights(profile, feature_keys) -> dict[str, float]
"""
from __future__ import annotations

import math
from typing import Any


# Learned-coefficient weights from scorecard_iteration_4.md "Top-3 learned signals
# per newly-lifted profile". Used as |coefficient| for distance weighting.
PROFILE_LEARNED_WEIGHTS: dict[str, dict[str, float]] = {
    "activist_governance": {
        "sector_is_financial": 0.150,
        "sector_is_healthcare": 0.068,
        "form_is_13d_amendment": 0.062,
    },
    "insider": {
        "is_role_multi": 0.150,
        "is_role_director": 0.145,
        "is_officer": 0.128,
    },
    # short_positioning is documented but out-of-scope per skill_build_plan
    "short_positioning": {
        "short_vol_to_avg_ratio": 0.150,
        "avg_volume_log10": 0.091,
        "sector_is_consumer": 0.089,
    },
    # merger_arb / binary_catalyst / litigation: no top-3 published in iter-4
    # doc; uniform fallback applies (handled by profile_weights()).
}

# Tokens get explicit-zero weight when the same dimension is also one-hot encoded
# (avoid double-counting). Token defaults otherwise = 0.5.
TOKEN_DOUBLE_COUNT_GUARD: set[str] = {"target_sector_token", "sector_token", "role_token"}


def is_numeric(v: Any) -> bool:
    if isinstance(v, bool):
        return True  # treat 0/1 as numeric for distance purposes
    return isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float, bool))


def _to_float(v: Any) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v)


def standardize_universe(
    features_per_event: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute mean/std for every numeric feature across the universe.

    Token features (str values) are skipped — they are not standardized.
    Returns (means, stds). std clamped to >= 1e-6 to avoid divide-by-zero.
    """
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    if not features_per_event:
        return means, stds

    # Collect values by feature
    values: dict[str, list[float]] = {}
    for row in features_per_event:
        for k, v in row.items():
            if isinstance(v, str):
                continue
            if k.startswith("_"):
                continue
            if not _is_num(v):
                continue
            values.setdefault(k, []).append(_to_float(v))

    for k, vs in values.items():
        if not vs:
            continue
        m = sum(vs) / len(vs)
        var = sum((x - m) ** 2 for x in vs) / max(len(vs), 1)
        s = math.sqrt(var)
        means[k] = m
        stds[k] = max(s, 1e-6)

    return means, stds


def profile_weights(
    profile: str,
    numeric_feature_keys: list[str],
    token_feature_keys: list[str],
) -> dict[str, float]:
    """Return feature -> weight for distance computation.

    For numeric features:
      - If a learned weight exists (PROFILE_LEARNED_WEIGHTS), use |coef|.
      - Else use uniform 1.0 / len(numeric_feature_keys).
    For token features:
      - 0.0 if in TOKEN_DOUBLE_COUNT_GUARD (e.g., sector_token already one-hot)
      - 0.5 otherwise.
    """
    learned = PROFILE_LEARNED_WEIGHTS.get(profile, {})
    n_numeric = max(len(numeric_feature_keys), 1)
    uniform = 1.0 / n_numeric
    median_learned = (
        sorted(abs(v) for v in learned.values())[len(learned) // 2]
        if learned
        else uniform
    )

    weights: dict[str, float] = {}
    for k in numeric_feature_keys:
        if k in learned:
            weights[k] = abs(learned[k])
        elif learned:
            # Other features in a learned-profile bucket get the median of the
            # top-3 (a proxy for "average importance")
            weights[k] = median_learned
        else:
            weights[k] = uniform

    for k in token_feature_keys:
        if k in TOKEN_DOUBLE_COUNT_GUARD:
            weights[k] = 0.0
        else:
            weights[k] = 0.5

    return weights


def compute_distance(
    candidate: dict[str, Any],
    neighbor: dict[str, Any],
    means: dict[str, float],
    stds: dict[str, float],
    weights: dict[str, float],
) -> tuple[float, list[dict[str, Any]]]:
    """Weighted Euclidean over standardized numeric + 0/1 token mismatch.

    Returns (distance, top_3_delta_features).
    """
    sq_terms: list[tuple[str, float, float, float, float]] = []  # feature, cand, neigh, delta_z, contrib
    for f, w in weights.items():
        if w == 0:
            continue
        cv = candidate.get(f)
        nv = neighbor.get(f)
        if cv is None or nv is None:
            # Penalize missing alignment lightly
            sq_terms.append((f, float("nan"), float("nan"), 0.5, 0.5 * w))
            continue
        # Token feature: string — 0/1 mismatch
        if isinstance(cv, str) or isinstance(nv, str):
            mismatch = 1.0 if str(cv) != str(nv) else 0.0
            sq_terms.append((f, cv, nv, mismatch, mismatch * w))
            continue
        # Numeric feature: standardize
        m = means.get(f, 0.0)
        s = stds.get(f, 1.0)
        cz = (_to_float(cv) - m) / s
        nz = (_to_float(nv) - m) / s
        delta_z = cz - nz
        sq_terms.append((f, cv, nv, delta_z, w * (delta_z ** 2)))

    total = sum(t[4] for t in sq_terms)
    distance = math.sqrt(max(total, 0.0))

    # Top-3 deltas by absolute contribution
    sq_terms.sort(key=lambda t: abs(t[4]), reverse=True)
    top_3: list[dict[str, Any]] = []
    for f, cv, nv, dz, contrib in sq_terms[:3]:
        if contrib == 0:
            continue
        top_3.append(
            {
                "feature": f,
                "candidate": None if (isinstance(cv, float) and math.isnan(cv)) else cv,
                "neighbor": None if (isinstance(nv, float) and math.isnan(nv)) else nv,
                "delta_z": round(dz, 4) if isinstance(dz, (int, float)) else None,
                "contribution": round(contrib, 4),
            }
        )
    return distance, top_3


def similarity(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


if __name__ == "__main__":
    # Self-test
    universe = [
        {"a": 1.0, "b": 0, "tag": "x"},
        {"a": 2.0, "b": 1, "tag": "y"},
        {"a": 3.0, "b": 0, "tag": "x"},
    ]
    m, s = standardize_universe(universe)
    print("means:", m, "stds:", s)
    w = profile_weights("activist_governance", ["a", "b"], ["tag"])
    print("weights:", w)
    cand = {"a": 2.0, "b": 0, "tag": "x"}
    for n in universe:
        d, top = compute_distance(cand, n, m, s, w)
        print(f"d={d:.3f} sim={similarity(d):.3f} top={top}")
