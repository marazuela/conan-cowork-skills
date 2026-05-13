"""Profile-specific HIT/MISS/PARTIAL classification rules.

Single entry point: classify(profile, event, forward_returns, opts=None)
returns (label, criterion, confidence_adjustment, extra_fields).

Per the SKILL.md §4 spec:
  - merger_arb: ret_60d signed; HIT if >= 0
  - activist_governance: ret_365d > 0 + governance follow-up filing
  - binary_catalyst: FDA action class (HIT=Approved, MISS=CRL/Withdrawn)
  - litigation: ret_365d signed + case resolution status, thesis-direction-aware
  - insider: ret_90d > 5% AND beats sector by 2pp (sector-relative)

The governance follow-up + FDA-action-class checks fall back to return-only
rules when their data sources are unreachable; the SKILL spec marks confidence
deltas in those paths.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# Canonical window per profile (per SKILL.md §3 table)
CANONICAL_WINDOW_DAYS = {
    "merger_arb": 60,
    "activist_governance": 365,
    "binary_catalyst": 1,
    "litigation": 365,
    "insider": 90,
}

DEFAULT_WINDOWS_DAYS = {
    "merger_arb": [30, 60, 90, 180, 365],
    "activist_governance": [30, 90, 180, 365, 730],
    "binary_catalyst": [1, 7, 30, 90],
    "litigation": [30, 180, 365, 730],
    "insider": [30, 60, 90, 180],
}

# SIC 2-digit prefix -> sector ETF (insider profile)
SECTOR_ETF_MAP = {
    "10": "XLE", "11": "XLE", "12": "XLE", "13": "XLE", "14": "XLE",
    "20": "XLI", "21": "XLI", "22": "XLI", "23": "XLI", "24": "XLI",
    "25": "XLI", "26": "XLI", "27": "XLI", "28": "XLB", "29": "XLE",
    "30": "XLI", "31": "XLI", "32": "XLI", "33": "XLI", "34": "XLI",
    "35": "XLI", "36": "XLK", "37": "XLI", "38": "XLI", "39": "XLI",
    "40": "XLI", "41": "XLI", "42": "XLI", "43": "XLI", "44": "XLI",
    "45": "XLI", "46": "XLI", "47": "XLI", "48": "XLC", "49": "XLU",
    "50": "XLY", "51": "XLY", "52": "XLY", "53": "XLY", "54": "XLP",
    "55": "XLY", "56": "XLY", "57": "XLY", "58": "XLY", "59": "XLY",
    "60": "XLF", "61": "XLF", "62": "XLF", "63": "XLF", "64": "XLF",
    "65": "XLF", "67": "XLF",
    "70": "XLY", "72": "XLY", "73": "XLK", "75": "XLI", "76": "XLI",
    "78": "XLC", "79": "XLC",
    "80": "XLV", "82": "XLC", "83": "XLP", "87": "XLK", "89": "XLK",
}


def get_sector_etf(sic_2dig: Optional[str]) -> str:
    if not sic_2dig:
        return "SPY"
    return SECTOR_ETF_MAP.get(str(sic_2dig).zfill(2), "SPY")


def _get(forward_returns: Dict[str, Any], window: int) -> Optional[float]:
    return forward_returns.get("ret_" + str(window) + "d")


def _classify_merger_arb(
    event: Dict[str, Any],
    fr: Dict[str, Any],
    opts: Dict[str, Any],
) -> Tuple[str, str, float, Dict[str, Any]]:
    ret60 = _get(fr, 60)
    extras: Dict[str, Any] = {}
    if ret60 is None:
        return ("PENDING_WINDOW", "merger_arb_60d_window_open", 0.0, extras)
    label = "HIT" if ret60 >= 0 else "MISS"
    criterion = "merger_arb_60d_forward_return_signed"
    conf_delta = 0.0
    # Optional spread booster
    deal_spread = (event.get("features") or {}).get("deal_spread_pct")
    ret60_ann = fr.get("ret_60d_annualized")
    if deal_spread is not None and ret60_ann is not None:
        spread_ann = float(deal_spread) * (365.0 / 60.0)
        if ret60_ann > spread_ann:
            extras["beats_implied_spread_annualized"] = True
            conf_delta += 0.05
        else:
            extras["beats_implied_spread_annualized"] = False
    # Optional PARTIAL band
    if opts.get("enable_partial") and -0.02 < ret60 < 0.02:
        # Indeterminate band — leave as PARTIAL pending deal-status follow-up
        return ("PARTIAL", "merger_arb_60d_indeterminate_band", conf_delta, extras)
    return (label, criterion, conf_delta, extras)


def _classify_activist(
    event: Dict[str, Any],
    fr: Dict[str, Any],
    opts: Dict[str, Any],
) -> Tuple[str, str, float, Dict[str, Any]]:
    ret365 = _get(fr, 365)
    extras: Dict[str, Any] = {}
    if ret365 is None:
        return ("PENDING_WINDOW", "activist_365d_window_open", 0.0, extras)
    governance_followup = opts.get("governance_followup_verified")  # True / False / None
    criterion_base = "activist_365d_return_AND_governance_followup"
    if governance_followup is None:
        # Data source unreachable -> return-only fallback
        criterion = "activist_365d_return_only_no_followup_verified"
        conf_delta = -0.10
        label = "HIT" if ret365 > 0 else "MISS"
        extras["governance_followup_status"] = "data_source_unreachable"
        return (label, criterion, conf_delta, extras)
    if ret365 > 0 and governance_followup:
        return ("HIT", criterion_base, 0.05, {"governance_followup_status": "verified_match"})
    if ret365 > 0 and not governance_followup:
        return ("PARTIAL", "activist_365d_return_positive_no_governance_change", 0.0, {"governance_followup_status": "verified_absent"})
    if ret365 <= 0 and governance_followup:
        return ("PARTIAL", "activist_365d_governance_change_no_return", 0.0, {"governance_followup_status": "verified_match"})
    return ("MISS", criterion_base, 0.0, {"governance_followup_status": "verified_absent"})


def _classify_binary_catalyst(
    event: Dict[str, Any],
    fr: Dict[str, Any],
    opts: Dict[str, Any],
) -> Tuple[str, str, float, Dict[str, Any]]:
    fda_action = opts.get("fda_action_class")  # "approved" | "crl" | "withdrawn" | "extended" | "rtf" | None
    ret1 = _get(fr, 1)
    extras: Dict[str, Any] = {"ret_1d": ret1}
    criterion = "binary_catalyst_FDA_action_class"
    if fda_action is None:
        # FDA data unreachable; if ret1 is large negative (>=-15%) likely CRL signal
        if ret1 is not None and ret1 <= -0.15:
            return ("MISS", "binary_catalyst_FDA_data_unavailable_inferred_from_price", -0.20, {"inferred": True, "ret_1d": ret1})
        if ret1 is not None and ret1 >= 0.10:
            return ("HIT", "binary_catalyst_FDA_data_unavailable_inferred_from_price", -0.20, {"inferred": True, "ret_1d": ret1})
        return ("UNRESOLVABLE", "binary_catalyst_FDA_data_unavailable", -0.20, extras)
    if fda_action in ("approved", "approved_with_rems"):
        return ("HIT", criterion, 0.0, extras)
    if fda_action in ("crl", "withdrawn", "rtf"):
        return ("MISS", criterion, 0.0, extras)
    if fda_action == "extended":
        return ("PARTIAL", criterion + "_pdufa_extended", 0.0, extras)
    if fda_action == "approved_narrow_label":
        return ("PARTIAL", criterion + "_narrow_label", 0.0, extras)
    return ("UNRESOLVABLE", criterion + "_unknown_class", -0.20, extras)


def _classify_litigation(
    event: Dict[str, Any],
    fr: Dict[str, Any],
    opts: Dict[str, Any],
) -> Tuple[str, str, float, Dict[str, Any]]:
    ret365 = _get(fr, 365)
    extras: Dict[str, Any] = {}
    if ret365 is None:
        return ("PENDING_WINDOW", "litigation_365d_window_open", 0.0, extras)
    thesis_dir = (event.get("features") or {}).get("thesis_direction") or opts.get("thesis_direction")
    case_resolved = opts.get("case_resolved")  # True / False / None
    # If thesis_direction missing, fall back to signed-return-only
    if not thesis_dir:
        label = "HIT" if ret365 > 0 else "MISS"
        return (label, "litigation_365d_signed_return_only", -0.15, {"thesis_direction": "missing"})
    # Direction-aware: align "favorable" with thesis_direction
    favorable_for_thesis = (
        (thesis_dir in ("long_defendant", "long_plaintiff") and ret365 > 0)
        or (thesis_dir == "short_defendant" and ret365 < 0)
    )
    if case_resolved is None:
        # Unresolved case but window passed — treat as pending-resolution, return-only fallback
        if favorable_for_thesis:
            return ("PARTIAL", "litigation_window_closed_case_pending_thesis_aligned", -0.10, {"thesis_direction": thesis_dir})
        return ("PARTIAL", "litigation_window_closed_case_pending_thesis_misaligned", -0.10, {"thesis_direction": thesis_dir})
    if case_resolved and favorable_for_thesis:
        if abs(ret365) < 0.05:
            return ("PARTIAL", "litigation_resolved_aligned_small_magnitude", 0.0, {"thesis_direction": thesis_dir})
        return ("HIT", "litigation_365d_return_AND_resolution_status", 0.0, {"thesis_direction": thesis_dir})
    if case_resolved and not favorable_for_thesis:
        if abs(ret365) < 0.05:
            return ("PARTIAL", "litigation_resolved_misaligned_small_magnitude", 0.0, {"thesis_direction": thesis_dir})
        return ("MISS", "litigation_365d_return_AND_resolution_status", 0.0, {"thesis_direction": thesis_dir})
    return ("UNRESOLVABLE", "litigation_unknown_state", -0.10, {"thesis_direction": thesis_dir})


def _classify_insider(
    event: Dict[str, Any],
    fr: Dict[str, Any],
    opts: Dict[str, Any],
) -> Tuple[str, str, float, Dict[str, Any]]:
    ret90 = _get(fr, 90)
    extras: Dict[str, Any] = {}
    if ret90 is None:
        return ("PENDING_WINDOW", "insider_90d_window_open", 0.0, extras)
    sector_ret90 = opts.get("sector_etf_ret_90d")  # float or None
    if sector_ret90 is None:
        # Sector ETF unreachable — fallback to absolute-return-only
        if ret90 > 0.05:
            return ("HIT", "insider_90d_abs_only_5pct", -0.15, {"sector_etf": "unreachable"})
        if 0 < ret90 <= 0.05:
            return ("PARTIAL", "insider_90d_abs_only_small_positive", -0.15, {"sector_etf": "unreachable"})
        return ("MISS", "insider_90d_abs_only_nonpositive", -0.15, {"sector_etf": "unreachable"})
    extras["sector_etf_ret_90d"] = sector_ret90
    extras["abs_ret_90d"] = ret90
    extras["sector_relative_pp"] = round((ret90 - sector_ret90) * 100, 2)
    criterion = "insider_90d_abs_5pct_AND_sector_relative_2pp"
    if ret90 > 0.05 and (ret90 - sector_ret90) >= 0.02:
        return ("HIT", criterion, 0.0, extras)
    if ret90 > 0.05 and 0 <= (ret90 - sector_ret90) < 0.02:
        return ("PARTIAL", criterion + "_abs_win_relative_tie", 0.0, extras)
    if 0 < ret90 <= 0.05 and (ret90 - sector_ret90) >= 0.02:
        return ("PARTIAL", criterion + "_abs_small_relative_beat", 0.0, extras)
    return ("MISS", criterion, 0.0, extras)


_PROFILE_DISPATCH = {
    "merger_arb": _classify_merger_arb,
    "activist_governance": _classify_activist,
    "binary_catalyst": _classify_binary_catalyst,
    "litigation": _classify_litigation,
    "insider": _classify_insider,
}


def classify(
    profile: str,
    event: Dict[str, Any],
    forward_returns: Dict[str, Any],
    opts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single entry point. Returns a dict:
    {
      "label": "HIT" | "MISS" | "PARTIAL" | "PENDING_WINDOW" | "UNRESOLVABLE",
      "criterion": str,
      "confidence_delta": float,
      "extras": {...}
    }
    """
    opts = opts or {}
    fn = _PROFILE_DISPATCH.get(profile)
    if fn is None:
        return {
            "label": "UNRESOLVABLE",
            "criterion": "unknown_profile",
            "confidence_delta": -1.0,
            "extras": {},
        }
    label, criterion, conf_delta, extras = fn(event, forward_returns, opts)
    return {
        "label": label,
        "criterion": criterion,
        "confidence_delta": conf_delta,
        "extras": extras,
    }


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--event-json", required=True, help="JSON of event dict")
    ap.add_argument("--forward-returns-json", required=True, help="JSON of forward_returns dict")
    ap.add_argument("--opts-json", default="{}", help="Optional opts dict as JSON")
    args = ap.parse_args()
    ev = json.loads(args.event_json)
    fr = json.loads(args.forward_returns_json)
    opts = json.loads(args.opts_json)
    res = classify(args.profile, ev, fr, opts)
    print(json.dumps(res, indent=2))
