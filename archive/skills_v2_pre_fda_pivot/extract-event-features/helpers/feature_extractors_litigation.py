"""litigation feature extractor.

No iter-4 sidecar exists for litigation yet — features computed from M2 inline + dossier metadata.
Schema derived from profile_litigation.md scoring dimensions.
"""
from __future__ import annotations
from typing import Optional
import math

NUMERIC_KEYS = [
    "claim_amount_usd",
    "claim_amount_log10",
    "has_claim",
    "ev_pct_claim",
    "motion_stage_ord",   # 0=complaint, 1=mtd_pending, 2=mtd_denied, 3=discovery, 4=summary_judgment, 5=trial, 6=settled, 7=appeal
    "is_party_publicly_traded",
    "party_resolution_confidence",
    "days_since_complaint",
    "is_class_action",
    "is_sec_enforcement",
]
TOKEN_KEYS = ["case_type_token", "jurisdiction_token", "ticker"]
ALL_KEYS = NUMERIC_KEYS + TOKEN_KEYS

MOTION_STAGE_MAP = {
    "complaint": 0, "complaint_filed": 0,
    "mtd_pending": 1, "motion_to_dismiss_pending": 1,
    "mtd_denied": 2, "mtd_granted": 2,
    "discovery": 3,
    "summary_judgment": 4, "summary_judgment_pending": 4,
    "trial": 5, "trial_scheduled": 5,
    "settled": 6, "settlement": 6,
    "appeal": 7, "appeal_pending": 7,
}


def extract(event: dict, sidecar_row: Optional[dict]) -> tuple[dict, list[str], str]:
    feats: dict = {}
    imputed: list[str] = []
    m2_feats = (event.get("features") or {})

    feats["ticker"] = event.get("ticker") or ""
    feats["case_type_token"] = m2_feats.get("case_type") or ""
    feats["jurisdiction_token"] = m2_feats.get("jurisdiction") or ""

    claim = m2_feats.get("claim_amount_usd")
    if isinstance(claim, (int, float)) and claim and claim > 0:
        feats["claim_amount_usd"] = float(claim)
        feats["claim_amount_log10"] = round(math.log10(claim), 3)
        feats["has_claim"] = 1
    else:
        feats["claim_amount_usd"] = 0.0
        feats["claim_amount_log10"] = 0.0
        feats["has_claim"] = 0
        imputed.append("claim_amount_usd")

    ev = m2_feats.get("enterprise_value_usd")
    if isinstance(claim, (int, float)) and isinstance(ev, (int, float)) and ev and ev > 0 and claim > 0:
        feats["ev_pct_claim"] = round(claim / ev, 4)
    else:
        feats["ev_pct_claim"] = 0.0
        imputed.append("ev_pct_claim")

    stage = (m2_feats.get("motion_stage") or "").lower()
    feats["motion_stage_ord"] = MOTION_STAGE_MAP.get(stage, 0)
    if stage not in MOTION_STAGE_MAP:
        imputed.append("motion_stage_ord")

    feats["is_party_publicly_traded"] = int(m2_feats.get("is_party_publicly_traded") or 0)
    feats["party_resolution_confidence"] = float(m2_feats.get("party_resolution_confidence") or 0.0)
    if "party_resolution_confidence" not in m2_feats:
        imputed.append("party_resolution_confidence")
    feats["days_since_complaint"] = int(m2_feats.get("days_since_complaint") or 0)
    if "days_since_complaint" not in m2_feats:
        imputed.append("days_since_complaint")
    feats["is_class_action"] = int(m2_feats.get("is_class_action") or 0)
    feats["is_sec_enforcement"] = int(m2_feats.get("is_sec_enforcement") or 0)

    source = "m2_inline_only" if not sidecar_row else "iter4_sidecar"
    return feats, imputed, source
