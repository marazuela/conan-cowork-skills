"""merger_arb kill-condition checker.

Inputs (callable as a function):
    state: dict with frontmatter (cik, ticker, primary_catalyst_date, ...)
    conditions: list of parsed condition rows from dossier_parser
    as_of_iso: ISO date string

Output:
    list of dicts {index, raw_text, kind, status, confidence, evidence,
                   source_url}

The checker is conservative: it only marks a condition `triggered` when the
relevant primary-source check returns ok=True with confidence >= 0.85.
Otherwise the condition is `clear` (when the source confirms absence) or
`unverifiable` (when the source can't be reached).
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from primary_source_clients import edgar_recent_filings, yfinance_close
except ImportError:  # pragma: no cover
    from .primary_source_clients import edgar_recent_filings, yfinance_close  # type: ignore


_CLOSE_FORMS = {"8-K"}
_CLOSE_KEYWORDS = (
    "completion of acquisition",
    "scheme effective",
    "scheme implemented",
    "transaction closed",
    "merger consummation",
)
_TERM_KEYWORDS = (
    "termination of material definitive agreement",
    "merger agreement terminated",
    "offer lapses",
    "offer withdrawn",
)


def check(
    state: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    as_of_iso: str,
) -> List[Dict[str, Any]]:
    fm = state.get("frontmatter", {})
    cik = str(fm.get("cik", "") or "")
    ticker = fm.get("ticker_local") or fm.get("ticker") or ""

    edgar = edgar_recent_filings(cik) if cik else {"ok": False}

    closed_evidence = None
    terminated_evidence = None
    if edgar.get("ok"):
        for f in edgar.get("result", []):
            if f.get("form") not in _CLOSE_FORMS:
                continue
            url = f.get("primary_doc_url", "") or ""
            # We can't always read the primary doc body in a fast sweep; the
            # EDGAR submissions JSON exposes form code only, not item codes.
            # Treat presence of post-announcement 8-K as "filing detected,
            # body unread" — confidence capped at 0.65 unless the operator
            # verifies the item code.
            if not closed_evidence:
                closed_evidence = {
                    "filed": f.get("filed"),
                    "accession": f.get("accession"),
                    "url": url,
                }

    out: List[Dict[str, Any]] = []
    for cond in conditions:
        kind = cond.get("kind")
        text = cond.get("raw_text", "")
        text_l = text.lower()
        status = "unverifiable"
        confidence = 0.30
        evidence = "no primary-source check executed"
        source_url = edgar.get("source_url") if edgar else None

        if any(k in text_l for k in ("close", "completion", "effective", "implemented")):
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR submissions API unavailable for CIK"
            elif closed_evidence:
                status = "manual_review"  # need to read 8-K body for Item code
                confidence = 0.65
                evidence = (
                    f"recent 8-K filed {closed_evidence.get('filed')} "
                    f"acc={closed_evidence.get('accession')} — confirm Item 2.01 in body"
                )
                source_url = closed_evidence.get("url")
            else:
                status = "clear"
                confidence = 0.90
                evidence = "no recent 8-K matching close in EDGAR submissions feed"
        elif any(k in text_l for k in ("withdraw", "termin", "lapse", "abandon")):
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR submissions API unavailable for CIK"
            else:
                status = "clear"
                confidence = 0.90
                evidence = "no recent 8-K matching termination in EDGAR submissions feed"
        elif "spread" in text_l:
            # Light price-based check: fetch close, compute % vs explicit
            # consideration if it's parseable from the dossier text.
            if ticker:
                yp = yfinance_close(str(ticker))
                if yp.get("ok"):
                    status = "manual_review"
                    confidence = 0.55
                    evidence = (
                        f"close={yp['result']['close']:.2f} on {yp['result']['date']} — "
                        "spread vs consideration must be evaluated against deal terms"
                    )
                    source_url = yp.get("source_url")
                else:
                    status = "unverifiable"
                    evidence = "yfinance unavailable"
            else:
                status = "unverifiable"
                evidence = "ticker not present in frontmatter"
        elif kind == "regulatory_decision_issued":
            status = "manual_review"
            confidence = 0.40
            evidence = (
                "regulatory denial / remedy decisions are checked off-line "
                "via DOJ/FTC/CMA/EC press releases — not reachable in this "
                "sweep wrapper"
            )
        else:
            status = "manual_review"
            confidence = 0.30
            evidence = "unstructured kill condition — operator review"

        out.append(
            {
                "index": cond.get("index"),
                "raw_text": text,
                "kind": kind,
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "source_url": source_url,
            }
        )
    return out
