"""binary_catalyst kill-condition checker.

Looks for: AdCom announcements, FDA decisions (approval / CRL / extension),
new safety signals, price-break thresholds, sell-side downgrades, and any
8-K signaling FDA communication ahead of the catalyst date.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

try:
    from primary_source_clients import edgar_recent_filings, federal_register_search, yfinance_close
except ImportError:  # pragma: no cover
    from .primary_source_clients import edgar_recent_filings, federal_register_search, yfinance_close  # type: ignore


_PRICE_BREAK_RE = re.compile(r"\$\s*([0-9][0-9,]*\.?[0-9]*)")


def _parse_price_threshold(text: str):
    m = _PRICE_BREAK_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def check(
    state: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    as_of_iso: str,
) -> List[Dict[str, Any]]:
    fm = state.get("frontmatter", {})
    cik = str(fm.get("cik", "") or "")
    ticker = fm.get("ticker_local") or fm.get("ticker") or ""
    drug_or_indication = fm.get("drug") or fm.get("signal_type") or ""

    edgar = edgar_recent_filings(cik) if cik else {"ok": False}
    fed_reg = federal_register_search(str(drug_or_indication)) if drug_or_indication else {"ok": False}
    price = yfinance_close(str(ticker)) if ticker else {"ok": False}

    out: List[Dict[str, Any]] = []
    for cond in conditions:
        text = cond.get("raw_text", "")
        text_l = text.lower()
        status = "unverifiable"
        confidence = 0.30
        evidence = "no primary-source check executed"
        source_url = None

        if "adcom" in text_l or "advisory committee" in text_l:
            if fed_reg.get("ok"):
                hits = fed_reg.get("result", []) or []
                # Crude filter for AdCom-relevant titles
                ad_hits = [h for h in hits if "advisory committee" in (h.get("title", "") or "").lower()]
                if ad_hits:
                    status = "manual_review"
                    confidence = 0.65
                    evidence = f"federal register returned {len(ad_hits)} advisory-committee hits — verify drug match"
                    source_url = (ad_hits[0].get("html_url") or fed_reg.get("source_url"))
                else:
                    status = "clear"
                    confidence = 0.90
                    evidence = "no advisory-committee announcement in Federal Register matching drug/indication term"
                    source_url = fed_reg.get("source_url")
            else:
                status = "unverifiable"
                evidence = "Federal Register unavailable"
        elif "crl" in text_l or "complete response letter" in text_l or "delay" in text_l:
            # Approximated via 8-K presence — operator confirms in body.
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR unavailable"
            else:
                forms = [f.get("form") for f in (edgar.get("result") or [])]
                if "8-K" in forms:
                    status = "manual_review"
                    confidence = 0.55
                    evidence = "recent 8-K present — confirm whether body mentions FDA action"
                    source_url = edgar.get("source_url")
                else:
                    status = "clear"
                    confidence = 0.85
                    evidence = "no recent 8-K detected"
                    source_url = edgar.get("source_url")
        elif "price" in text_l and "<" in text_l:
            threshold = _parse_price_threshold(text)
            if threshold is None:
                status = "manual_review"
                confidence = 0.40
                evidence = "could not parse explicit price threshold"
            elif price.get("ok"):
                close = price["result"]["close"]
                if close < threshold:
                    status = "triggered"
                    confidence = 0.90
                    evidence = f"close ${close:.2f} below threshold ${threshold:.2f}"
                else:
                    status = "clear"
                    confidence = 0.92
                    evidence = f"close ${close:.2f} above threshold ${threshold:.2f}"
                source_url = price.get("source_url")
            else:
                status = "unverifiable"
                evidence = "yfinance unavailable for close price"
        elif "safety signal" in text_l or "openfda" in text_l or "adverse event" in text_l:
            status = "manual_review"
            confidence = 0.40
            evidence = "openFDA query is best-effort; this wrapper does not enumerate AE clusters in the fast sweep"
        elif "downgrade" in text_l or "price target" in text_l:
            status = "manual_review"
            confidence = 0.30
            evidence = "analyst-action checks require an analyst-news feed not wired into this sweep"
        elif "short interest" in text_l or "put buildup" in text_l:
            status = "manual_review"
            confidence = 0.30
            evidence = "FINRA / OCC checks not wired into this sweep"
        else:
            status = "manual_review"
            confidence = 0.30
            evidence = "unstructured binary-catalyst kill condition — operator review"

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
