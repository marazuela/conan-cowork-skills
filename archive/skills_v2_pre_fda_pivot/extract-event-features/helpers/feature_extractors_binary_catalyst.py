"""binary_catalyst feature extractor. Mirrors iter-4 Option-B schema (8 prospective biotech features)."""
from __future__ import annotations
from typing import Optional

NUMERIC_KEYS = [
    "sponsor_p3_track_record",
    "sponsor_p3_prior_count_log",
    "indication_p3_success_rate",
    "indication_p3_pool_size_log",
    "enrollment_zscore_vs_indication",
    "phase2_readout_strength",
    "phase2_prior_count",
    "sponsor_biorxiv_volume_log",
]
TOKEN_KEYS = []  # binary_catalyst sidecar has private "_indication_group" / "_sponsor_canon" but those start with _ and are not features
ALL_KEYS = NUMERIC_KEYS


def extract(event: dict, sidecar_row: Optional[dict]) -> tuple[dict, list[str], str]:
    """For binary_catalyst, the iter-4 sidecar uses {by_event_id: {eid: {feats}}} schema (Option-B).
    sidecar_row here is the per-event-id flat dict, not a {rich_features: ...} wrapper.
    """
    feats: dict = {}
    imputed: list[str] = []
    if sidecar_row and isinstance(sidecar_row, dict):
        for k in NUMERIC_KEYS:
            if k in sidecar_row:
                feats[k] = sidecar_row[k]
            else:
                feats[k] = 0
                imputed.append(k)
        source = "iter4_sidecar_optionB"
    else:
        for k in NUMERIC_KEYS:
            feats[k] = 0
            imputed.append(k)
        source = "m2_inline"
    return feats, imputed, source
