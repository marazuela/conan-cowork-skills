"""litigation kill-condition checker.

Looks for: judgments, settlements, dismissals, stays, transfers. Where the
dossier references a specific docket and CourtListener auth is configured,
the checker queries that docket; otherwise it returns `unverifiable` with an
`auth_required` note (per Q-017 in OPEN_QUESTIONS).
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from primary_source_clients import courtlistener_docket, edgar_recent_filings
except ImportError:  # pragma: no cover
    from .primary_source_clients import courtlistener_docket, edgar_recent_filings  # type: ignore


def check(
    state: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    as_of_iso: str,
) -> List[Dict[str, Any]]:
    fm = state.get("frontmatter", {})
    cik = str(fm.get("cik", "") or "")
    docket_id = fm.get("docket_id") or fm.get("court_docket")

    cl = courtlistener_docket(str(docket_id)) if docket_id else {"ok": False, "error_class": "auth_required"}
    edgar = edgar_recent_filings(cik) if cik else {"ok": False}

    out: List[Dict[str, Any]] = []
    for cond in conditions:
        text = cond.get("raw_text", "")
        text_l = text.lower()
        status = "unverifiable"
        confidence = 0.30
        evidence = "no primary-source check executed"
        source_url = cl.get("source_url") if cl else None

        if any(k in text_l for k in ("judgment", "final order", "dismissal", "settle")):
            if cl.get("ok"):
                # We do not parse the docket entries here — the wrapper just
                # confirms the docket can be reached. Operator inspects.
                status = "manual_review"
                confidence = 0.55
                evidence = "docket reachable; inspect entries for judgment/dismissal/settlement"
            elif (cl or {}).get("error_class") == "auth_required":
                status = "unverifiable"
                evidence = "CourtListener API token missing (Q-017 OPEN_QUESTIONS)"
            else:
                status = "unverifiable"
                evidence = "court source unreachable"
            # Cross-check via EDGAR 8-K Item 8.01 disclosing the resolution
            if edgar.get("ok"):
                forms = [f.get("form") for f in (edgar.get("result") or [])]
                if "8-K" in forms:
                    confidence = max(confidence, 0.50)
                    evidence += "; recent 8-K present — possible Item 8.01 disclosure of resolution"
                    source_url = edgar.get("source_url")
        elif "stay" in text_l or "transfer" in text_l:
            status = "manual_review"
            confidence = 0.40
            evidence = "stay/transfer events require docket inspection"
        else:
            status = "manual_review"
            confidence = 0.30
            evidence = "unstructured litigation kill condition — operator review"

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
