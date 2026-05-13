"""D-097 RESOLVER_LEAKAGE_FEATURES detector.

Sourced from 02_System/engine/tools/learning_loop.py:819 (RESOLVER_LEAKAGE_FEATURES).
This skill detects leakage at extract time and surfaces a verdict; it does NOT auto-strip features
(that is learning_loop.augmented_features's responsibility).
"""
from __future__ import annotations

# Mirror of learning_loop.RESOLVER_LEAKAGE_FEATURES (D-097, D-098). Update when source changes.
RESOLVER_LEAKAGE_FEATURES: dict[str, set[str]] = {
    "binary_catalyst": {"is_completed", "is_terminated", "has_results", "why_stopped_present"},
    "merger_arb": set(),
    "activist_governance": set(),
    "litigation": set(),
    "insider": set(),
    "short_positioning": set(),
}


def check(profile: str, feature_keys: list[str], events: list[dict]) -> dict:
    """Return a verdict dict.

    verdict: 'no_overlap' | 'leakage_features_present'
    """
    suspect_set = RESOLVER_LEAKAGE_FEATURES.get(profile, set())
    if not suspect_set:
        return {
            "verdict": "no_overlap",
            "checked_features": list(feature_keys),
            "resolver_leakage_set": [],
            "confidence_floor": 0.85,
        }
    overlap = [k for k in feature_keys if k in suspect_set]
    if not overlap:
        return {
            "verdict": "no_overlap",
            "checked_features": list(feature_keys),
            "resolver_leakage_set": sorted(suspect_set),
            "confidence_floor": 0.85,
        }
    # Leakage features present in schema. Check whether any event has nonzero values.
    nonzero_count = 0
    for ev in events:
        rf = ev.get("rich_features") or {}
        for k in overlap:
            v = rf.get(k)
            if isinstance(v, (int, float)) and v != 0:
                nonzero_count += 1
                break
    return {
        "verdict": "leakage_features_present",
        "checked_features": list(feature_keys),
        "resolver_leakage_set": sorted(suspect_set),
        "leakage_columns_in_schema": overlap,
        "events_with_nonzero_leakage": nonzero_count,
        "confidence_floor": 0.10,
        "shadow_eval_action": "downgrade_confidence_until_features_decoupled",
    }
