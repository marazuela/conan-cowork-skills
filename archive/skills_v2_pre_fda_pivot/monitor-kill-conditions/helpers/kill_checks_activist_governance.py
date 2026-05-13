"""activist_governance kill-condition checker.

Looks for material EDGAR events that resolve an activist campaign:
13D withdrawal / conversion to 13G, settlement 8-K, DEF 14A nomination
window expiry, dilutive issuance.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from primary_source_clients import edgar_recent_filings, yfinance_close
except ImportError:  # pragma: no cover
    from .primary_source_clients import edgar_recent_filings, yfinance_close  # type: ignore


def check(
    state: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    as_of_iso: str,
) -> List[Dict[str, Any]]:
    fm = state.get("frontmatter", {})
    cik = str(fm.get("cik", "") or "")
    ticker = fm.get("ticker_local") or fm.get("ticker") or ""

    edgar = edgar_recent_filings(cik) if cik else {"ok": False}
    recent_forms = [f.get("form", "") for f in (edgar.get("result") or [])]
    recent_set = set(recent_forms)

    out: List[Dict[str, Any]] = []
    for cond in conditions:
        text = cond.get("raw_text", "")
        text_l = text.lower()
        status = "unverifiable"
        confidence = 0.30
        evidence = "no primary-source check executed"
        source_url = edgar.get("source_url") if edgar else None

        if "13d/a" in text_l or "13d filing" in text_l or "schedule 13d" in text_l or "13g" in text_l:
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR submissions API unavailable"
            else:
                # Note: ownership filings live under the filer's CIK, not the
                # issuer's — submissions feed for the issuer captures these
                # via the issuer-as-subject path on EDGAR.
                if "SC 13G" in recent_set or "SC 13G/A" in recent_set:
                    status = "manual_review"
                    confidence = 0.65
                    evidence = "13G filing present on issuer feed — verify whether activist filer flipped to passive"
                else:
                    status = "clear"
                    confidence = 0.85
                    evidence = "no recent 13G conversion detected on issuer EDGAR feed"
        elif "settlement" in text_l or "board agreement" in text_l or "5.02" in text_l:
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR submissions API unavailable"
            elif "8-K" in recent_set:
                status = "manual_review"
                confidence = 0.55
                evidence = "recent 8-K present — confirm Item 5.02 board-change in body"
            else:
                status = "clear"
                confidence = 0.90
                evidence = "no recent 8-K detected; no settlement filing observed"
        elif "annual meeting" in text_l and ("nominat" in text_l or "passes" in text_l or "passed" in text_l):
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR unavailable; cannot verify proxy-window status"
            elif "DEF 14A" in recent_set or "PRE 14A" in recent_set:
                status = "manual_review"
                confidence = 0.60
                evidence = "DEF/PRE 14A present — verify whether activist nominees were filed"
            else:
                status = "clear"
                confidence = 0.80
                evidence = "no proxy filing detected; nomination window status uncertain"
        elif "issuance" in text_l or "dilut" in text_l or "equity raise" in text_l:
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR unavailable"
            elif "S-3" in recent_set or "S-1" in recent_set or "424B" in str(recent_set):
                status = "manual_review"
                confidence = 0.65
                evidence = "registration / prospectus filing present on issuer feed — verify size and direction"
            else:
                status = "clear"
                confidence = 0.85
                evidence = "no recent registration filing"
        elif "sector" in text_l and ("decline" in text_l or "selloff" in text_l or "drawdown" in text_l):
            if ticker:
                yp = yfinance_close(str(ticker))
                if yp.get("ok"):
                    status = "manual_review"
                    confidence = 0.55
                    evidence = f"close={yp['result']['close']:.2f} — operator must compare to sector benchmark"
                    source_url = yp.get("source_url")
                else:
                    status = "unverifiable"
                    evidence = "yfinance unavailable"
            else:
                status = "unverifiable"
                evidence = "ticker missing in frontmatter"
        else:
            status = "manual_review"
            confidence = 0.30
            evidence = "unstructured activist kill condition — operator review"

        out.append(
            {
                "index": cond.get("index"),
                "raw_text": text,
                "kind": cond.get("kind"),
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "source_url": source_url,
            }
        )
    return out
