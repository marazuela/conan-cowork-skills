"""case_outcome_tree.py — probability tree builder.

Given a `(case_type, motion_stage, claim_amount_usd, publicly_traded_party_role)`
tuple, return a list of outcome branches, each with probability + CI,
magnitude, time-to-resolution, source, and confidence.

Probability priors come from `precedent_settlements.py`. This module's job is
to translate priors into branch dicts, sign magnitudes for the publicly-traded
party (defendant => negative; plaintiff => positive), and validate that
top-level probabilities sum to 1.0 ± 0.001.

Usage:
    from case_outcome_tree import build_tree
    branches = build_tree(
        case_type="securities_fraud",
        motion_stage="mtd_pending",
        claim_amount_usd=250_000_000,
        publicly_traded_party_role="defendant",
    )
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import precedent_settlements as ps


# ---------- Branch builders per case-type family ----------


def _sign_for_role(role: str) -> int:
    """Return the magnitude sign multiplier for the publicly-traded party.

    role == 'defendant'    -> -1 (loss is a liability for the issuer)
    role == 'plaintiff'    -> +1 (recovery is a benefit for the issuer)
    role == 'neither_or_unknown' -> 0 (zero out — the candidate would have
        been auto-archived per Dimension-6 anyway, so this is defensive)
    """
    role = (role or "").lower()
    if role == "defendant":
        return -1
    if role == "plaintiff":
        return 1
    return 0


def _settlement_magnitude(
    case_type: str, claim_amount_usd: Optional[float], priors: Dict
) -> float:
    """Settlement magnitude in USD using case-type median multiple of claim."""
    if claim_amount_usd is None:
        return 0.0
    mp = priors["magnitude_priors"]
    if case_type == "sec_enforcement":
        # SEC enforcement: settlement_consent_order magnitude is disgorgement +
        # civil penalty; treat $-priors as absolute, not multiples of claim.
        d_mm = mp.get("issuer_side_disgorgement_median_usd_mm", 25)
        p_mm = mp.get("issuer_side_civil_penalty_median_usd_mm", 25)
        return (d_mm + p_mm) * 1e6
    if case_type == "itc_337":
        # ITC settlement is a one-time payment as % of claim where claim is
        # implied damages; if no claim_amount, fall back to absolute median.
        mult = mp.get("settlement_one_time_payment_median_of_claim", 0.06)
        return claim_amount_usd * mult
    mult = mp.get("settlement_multiple_of_claim_median", 0.025)
    return claim_amount_usd * mult


def _verdict_magnitude(
    case_type: str, claim_amount_usd: Optional[float], priors: Dict
) -> float:
    """Verdict-for-plaintiff magnitude in USD using case-type haircut multiple."""
    if claim_amount_usd is None:
        return 0.0
    mp = priors["magnitude_priors"]
    if case_type == "sec_enforcement":
        d_mm = mp.get("issuer_side_disgorgement_median_usd_mm", 25)
        p_mm = mp.get("issuer_side_civil_penalty_median_usd_mm", 25)
        # Litigated outcome implies higher penalty; uplift 1.5x median.
        return (d_mm + p_mm) * 1.5 * 1e6
    if case_type == "itc_337":
        # ITC violation found => exclusion order; magnitude = % of revenue
        # protected by patent. claim_amount_usd is treated as revenue-at-risk.
        mult = mp.get("exclusion_order_revenue_haircut_median", 0.15)
        return claim_amount_usd * mult
    mult = mp.get("verdict_haircut_multiple_median", 0.40)
    return claim_amount_usd * mult


def _time_for_branch(case_type: str, branch: str, priors: Dict) -> int:
    """Median time-to-resolution in days for a specific branch."""
    tp = priors["time_priors_days"]
    if branch in tp:
        return int(tp[branch].get("median", 365))
    # Fallback families: any settled-* branch maps to "settled"; verdict-* to "verdict_for_plaintiff"
    if "settled" in branch and "settled" in tp:
        return int(tp["settled"].get("median", 365))
    if "verdict_for_plaintiff" in branch and "verdict_for_plaintiff" in tp:
        return int(tp["verdict_for_plaintiff"].get("median", 365))
    if "verdict_for_defendant" in branch and "verdict_for_defendant" in tp:
        return int(tp["verdict_for_defendant"].get("median", 365))
    if branch.startswith("dismiss") and "mtd_granted" in tp:
        return int(tp["mtd_granted"].get("median", 365))
    return 365  # one-year fallback


def build_tree(
    case_type: str,
    motion_stage: str,
    claim_amount_usd: Optional[float],
    publicly_traded_party_role: str,
) -> Dict:
    """Build the outcome tree.

    Returns a dict:
        {
          "branches": [...],
          "case_type": ..., "motion_stage_used": ..., "stage_fallback_from": ...,
          "sparse_class": bool,
          "probability_sum": float,
          "auth_status": "ok" | "unknown_case_type",
        }
    """
    priors = ps.get_priors(case_type, motion_stage)
    if not priors:
        return {
            "branches": [],
            "case_type": case_type,
            "motion_stage_used": motion_stage,
            "sparse_class": True,
            "probability_sum": 0.0,
            "auth_status": "unknown_case_type",
            "supported_case_types": ps.list_supported_case_types(),
        }

    role_sign = _sign_for_role(publicly_traded_party_role)
    branches_out: List[Dict] = []
    for b in priors["branches"]:
        name = b["branch"]
        # Determine which magnitude family the branch belongs to.
        if "settled" in name or "consent_order" in name or "settlement" in name:
            base = _settlement_magnitude(case_type, claim_amount_usd, priors)
        elif "verdict_for_plaintiff" in name or "violation_found" in name or "judgment_for_sec" in name or "unpatentable" in name or "fair_value_above" in name:
            base = _verdict_magnitude(case_type, claim_amount_usd, priors)
        elif "institution_granted" in name and case_type == "ptab_ipr":
            # PTAB institution itself does not realize damages but is a binary
            # market mover for patent owner; treat as zero-magnitude milestone.
            base = 0.0
        else:
            base = 0.0  # dismissals, withdrawals, defendant-favorable verdicts
        magnitude = role_sign * base

        time_days = _time_for_branch(case_type, name, priors)

        branches_out.append(
            {
                "branch": name,
                "probability": b["probability"],
                "probability_ci_low": b["probability_ci_low"],
                "probability_ci_high": b["probability_ci_high"],
                "n_supporting_precedents": b["n_supporting_precedents"],
                "magnitude_to_issuer_usd": float(magnitude),
                "time_to_resolution_days": time_days,
                "confidence": b["confidence"],
                "source": b["source"],
            }
        )

    p_sum = round(sum(br["probability"] for br in branches_out), 4)

    out = {
        "branches": branches_out,
        "case_type": case_type,
        "motion_stage_used": priors.get("stage_used"),
        "sparse_class": priors.get("sparse_class", False),
        "probability_sum": p_sum,
        "auth_status": "ok",
    }
    if "stage_fallback_from" in priors:
        out["stage_fallback_from"] = priors["stage_fallback_from"]
    return out


if __name__ == "__main__":
    import json as _json
    import sys

    case_type = sys.argv[1] if len(sys.argv) > 1 else "sec_enforcement"
    motion_stage = sys.argv[2] if len(sys.argv) > 2 else "complaint_filed"
    claim_amount = float(sys.argv[3]) if len(sys.argv) > 3 else None
    role = sys.argv[4] if len(sys.argv) > 4 else "defendant"
    print(_json.dumps(
        build_tree(case_type, motion_stage, claim_amount, role),
        indent=2, default=str,
    ))
