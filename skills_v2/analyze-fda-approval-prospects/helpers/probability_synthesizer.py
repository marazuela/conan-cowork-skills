"""probability_synthesizer.py

Combines:
  - class base rate (from P2 if available; else conservative default)
  - trial forensic findings (from endpoint_integrity_check)
  - AdCom risk
  - CMC risk
  - sponsor-specific FDA history (from P2 if available)

Into a (p_low, p_mid, p_high) range with an explicit assumption ledger.

Conservative defaults:
  - small-molecule NDA full-approval base rate ≈ 0.60–0.65 historical aggregate
  - AdCom convening base rate ≈ 0.10–0.15 across FDA divisions
  - boxed-warning base rate ≈ 0.20 for new chemical entities

The midpoint is the natural decision-relevant number; the spread is
information about evidence quality. The synthesizer never returns a single
point estimate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_DEFAULT_ANCHOR = 0.62
_BASE_SPREAD = 0.10  # ±5pp before evidence-quality adjustments


def synthesize(
    findings: List[Dict[str, Any]],
    adcom: Optional[Dict[str, Any]] = None,
    cmc: Optional[Dict[str, Any]] = None,
    class_precedent: Optional[Dict[str, Any]] = None,
    sponsor_history: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ledger: List[Dict[str, Any]] = []

    # Anchor
    anchor = _DEFAULT_ANCHOR
    anchor_source = "BIO/Informa 2011-2020 small-molecule NDA full-approval aggregate (~62%)"
    anchor_conf = 0.65
    if class_precedent and class_precedent.get("approval_rate_class") is not None:
        anchor = float(class_precedent["approval_rate_class"])
        anchor_source = class_precedent.get("source") or "P2 class precedent"
        anchor_conf = class_precedent.get("confidence", 0.80)
    ledger.append(
        {
            "adjustment": "class base rate",
            "sign": "anchor",
            "magnitude_pp": round(anchor * 100),
            "rationale": "starting probability",
            "source": anchor_source,
            "confidence": anchor_conf,
        }
    )

    # Trial forensic adjustments
    pp_adj = 0
    overall_conf = anchor_conf
    for f in findings or []:
        m = f.get("magnitude_pp", 0) or 0
        pp_adj += m
        overall_conf = max(overall_conf, f.get("confidence", 0.5)) * 0.5 + overall_conf * 0.5
        ledger.append(
            {
                "adjustment": f"trial: {f.get('dimension')}",
                "sign": f.get("signal"),
                "magnitude_pp": m,
                "rationale": f.get("finding"),
                "source": f.get("source"),
                "confidence": f.get("confidence", 0.5),
            }
        )
    # Cap aggregate trial adjustment
    if pp_adj > 25:
        pp_adj = 25
    elif pp_adj < -25:
        pp_adj = -25

    # AdCom modifier
    adcom_pp = 0
    if adcom:
        st = (adcom.get("status") or "").lower()
        if st == "confirmed_scheduled":
            adcom_pp = -15
        elif st == "elevated":
            adcom_pp = -7
        elif st == "moderate":
            adcom_pp = -3
        ledger.append(
            {
                "adjustment": "AdCom risk",
                "sign": "negative" if adcom_pp < 0 else "neutral",
                "magnitude_pp": adcom_pp,
                "rationale": adcom.get("rationale", ""),
                "source": adcom.get("source"),
                "confidence": adcom.get("confidence", 0.6),
            }
        )

    # CMC modifier
    cmc_pp = 0
    if cmc:
        st = (cmc.get("status") or "").lower()
        if st == "elevated":
            cmc_pp = -10
        elif st == "moderate":
            cmc_pp = -4
        ledger.append(
            {
                "adjustment": "CMC / manufacturing risk",
                "sign": "negative" if cmc_pp < 0 else "neutral",
                "magnitude_pp": cmc_pp,
                "rationale": cmc.get("rationale", ""),
                "source": cmc.get("source"),
                "confidence": cmc.get("confidence", 0.6),
            }
        )

    # Sponsor-specific FDA history
    sponsor_pp = 0
    if sponsor_history:
        if sponsor_history.get("prior_crl_same_indication"):
            sponsor_pp -= 10
            ledger.append({"adjustment": "sponsor: prior CRL same indication", "sign": "negative", "magnitude_pp": -10, "rationale": "CRL on same indication", "source": sponsor_history.get("source"), "confidence": 0.85})
        if sponsor_history.get("breakthrough_designation"):
            sponsor_pp += 3
            ledger.append({"adjustment": "sponsor: breakthrough designation", "sign": "positive", "magnitude_pp": 3, "rationale": "BTD signals FDA support", "source": sponsor_history.get("source"), "confidence": 0.80})
        if sponsor_history.get("priority_review"):
            sponsor_pp += 2
            ledger.append({"adjustment": "sponsor: priority review", "sign": "positive", "magnitude_pp": 2, "rationale": "PR signals FDA willingness", "source": sponsor_history.get("source"), "confidence": 0.80})

    # Compose
    p_mid = anchor + (pp_adj + adcom_pp + cmc_pp + sponsor_pp) / 100.0
    p_mid = max(0.05, min(0.95, p_mid))

    # Spread reflects evidence quality
    n_findings = len([f for f in findings or [] if f.get("confidence", 0) >= 0.6])
    spread = _BASE_SPREAD + (0.03 * max(0, 4 - n_findings))
    if not class_precedent:
        spread += 0.05
    if adcom and (adcom.get("status") or "").lower() == "unverifiable":
        spread += 0.05
    if cmc and (cmc.get("status") or "").lower() == "unverifiable":
        spread += 0.03
    spread = min(0.30, max(0.05, spread))

    p_low = max(0.02, p_mid - spread / 2)
    p_high = min(0.98, p_mid + spread / 2)

    return {
        "p_low": round(p_low, 3),
        "p_mid": round(p_mid, 3),
        "p_high": round(p_high, 3),
        "spread": round(spread, 3),
        "ledger": ledger,
        "overall_confidence": round(min(0.95, overall_conf), 2),
    }
