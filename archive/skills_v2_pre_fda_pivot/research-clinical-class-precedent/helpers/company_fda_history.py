"""company_fda_history.py — EDGAR EFTS wrapper for sponsor FDA history (P2).

Pulls 8-K, 10-K, 10-Q filings by CIK and matches on FDA-disclosure keywords:
  - "Complete Response Letter"   → prior CRLs received
  - "Breakthrough Therapy"       → BTD events
  - "Priority Review"            → priority-review events
  - "Real-Time Oncology Review"  → RTOR (oncology)
  - "Form 483" / "warning letter" → ongoing inspection / CMC concerns

Returns a structured dict matching what probability_synthesizer expects.

Network and rate-limit handling: 3-attempt exponential backoff, 12s timeout.
On rate-limit (HTTP 429), returns {"ok": False, "_status": "edgar_rate_limited"}.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
USER_AGENT = "investment-tool-research-clinical-class-precedent/1.0 (skill-build) javiergorordo13@hotmail.com"
DEFAULT_TIMEOUT = 12.0
MAX_RETRIES = 3

KEYWORDS = {
    "prior_crls_received": ["Complete Response Letter"],
    "breakthrough_designation": ["Breakthrough Therapy designation", "Breakthrough Therapy Designation"],
    "priority_review": ["Priority Review designation", "Priority Review"],
    "rtor_participation": ["Real-Time Oncology Review", "RTOR"],
    "ongoing_inspection_concerns": ["Form 483", "warning letter from the FDA"],
}


def _http_get_json(url: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status == 429:
                    last_err = "HTTP 429 rate_limited"
                    time.sleep(min(2 ** (attempt + 2), 30))
                    continue
                if resp.status >= 400:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                return True, json.loads(resp.read().decode("utf-8")), ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTPError {e.code}"
            if e.code == 429:
                time.sleep(min(2 ** (attempt + 2), 30))
                continue
            time.sleep(min(2 ** attempt, 8))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8))
        except Exception as e:  # pragma: no cover
            last_err = f"unexpected: {type(e).__name__}: {e}"
            break
    return False, None, last_err


def _normalize_cik(cik: str) -> str:
    """EDGAR EFTS expects 10-digit zero-padded CIK; strip leading zeros for output."""
    return str(cik).strip().lstrip("0") or "0"


def _build_efts_url(query: str, ciks: str, forms: List[str], gte: str = "", lte: str = "") -> str:
    cik_padded = str(ciks).strip().zfill(10)
    params = [
        ("q", query),
        ("ciks", cik_padded),
        ("forms", ",".join(forms)),
    ]
    if gte and lte:
        params.append(("dateRange", "custom"))
        params.append(("startdt", gte))
        params.append(("enddt", lte))
    return f"{EFTS_BASE}?{urllib.parse.urlencode(params)}"


def search_keyword(cik: str, query: str, forms: List[str]) -> Dict[str, Any]:
    """One EDGAR EFTS keyword search, return hits + source URL."""
    url = _build_efts_url(query, cik, forms)
    ok, payload, err = _http_get_json(url)
    if not ok:
        return {"ok": False, "url": url, "hits": [], "reason": err}
    hits_obj = (payload or {}).get("hits", {})
    hits = hits_obj.get("hits", []) if isinstance(hits_obj, dict) else []
    parsed: List[Dict[str, Any]] = []
    for h in hits:
        src = (h.get("_source") or {})
        parsed.append({
            "accession_no": src.get("adsh"),
            "form": src.get("form"),
            "filed": src.get("file_date"),
            "company": (src.get("display_names") or [None])[0],
            "snippet": ((h.get("highlight") or {}).get("body", []) or [None])[0],
            "source_url": f"https://www.sec.gov/Archives/edgar/data/{_normalize_cik(cik)}/{(src.get('adsh') or '').replace('-', '')}/{src.get('adsh')}-index.htm",
        })
    return {"ok": True, "url": url, "hits": parsed, "reason": ""}


def get_sponsor_history(
    ticker: str,
    cik: str,
    company_name: str = "",
    drug_name: str = "",
    indication: str = "",
) -> Dict[str, Any]:
    """Aggregate sponsor's FDA fingerprint."""
    if not cik:
        return {
            "ticker": ticker,
            "cik": "",
            "company_name": company_name,
            "_status": "no_cik_provided",
            "prior_approvals": [],
            "prior_crls_received": [],
            "prior_crl_same_indication": False,
            "breakthrough_designation": False,
            "priority_review": False,
            "rtor_participation": False,
            "ongoing_inspection_concerns": False,
            "source": "(no CIK)",
            "confidence": 0.20,
        }

    cik = _normalize_cik(cik)
    findings: Dict[str, List[Dict[str, Any]]] = {k: [] for k in KEYWORDS}
    sources: List[str] = []
    rate_limited = False
    errors: List[str] = []

    for category, terms in KEYWORDS.items():
        for term in terms:
            forms = ["8-K"]
            if category == "ongoing_inspection_concerns":
                forms = ["8-K", "10-K", "10-Q"]
            r = search_keyword(cik, term, forms)
            if not r["ok"]:
                if "429" in (r.get("reason") or ""):
                    rate_limited = True
                errors.append(f"{category}/{term}: {r.get('reason')}")
                continue
            sources.append(r["url"])
            for hit in r["hits"]:
                hit["category"] = category
                hit["query_term"] = term
                findings[category].append(hit)
            time.sleep(0.2)  # polite throttle

    if rate_limited and not any(findings.values()):
        return {
            "ticker": ticker,
            "cik": cik,
            "company_name": company_name,
            "_status": "edgar_rate_limited",
            "prior_approvals": [],
            "prior_crls_received": [],
            "prior_crl_same_indication": False,
            "breakthrough_designation": False,
            "priority_review": False,
            "rtor_participation": False,
            "ongoing_inspection_concerns": False,
            "source": "EDGAR EFTS (rate-limited)",
            "confidence": 0.30,
            "errors": errors,
        }

    # Reduce: presence of any hit → True
    out_findings = {
        "prior_crls_received": findings["prior_crls_received"],
        "breakthrough_designation": bool(findings["breakthrough_designation"]),
        "priority_review": bool(findings["priority_review"]),
        "rtor_participation": bool(findings["rtor_participation"]),
        "ongoing_inspection_concerns": bool(findings["ongoing_inspection_concerns"]),
    }

    # Heuristic: prior_crl_same_indication = was a CRL filing snippet matched the indication string?
    prior_crl_same = False
    if indication and out_findings["prior_crls_received"]:
        ind_l = indication.lower()
        for hit in out_findings["prior_crls_received"]:
            snip = (hit.get("snippet") or "").lower()
            if any(k in snip for k in ind_l.split()):
                prior_crl_same = True
                break

    return {
        "ticker": ticker,
        "cik": cik,
        "company_name": company_name,
        "_status": "ok",
        "prior_approvals": [],  # filled by analyze.py via openFDA cross-reference
        "prior_crls_received": out_findings["prior_crls_received"],
        "prior_crl_same_indication": prior_crl_same,
        "breakthrough_designation": out_findings["breakthrough_designation"],
        "priority_review": out_findings["priority_review"],
        "rtor_participation": out_findings["rtor_participation"],
        "ongoing_inspection_concerns": out_findings["ongoing_inspection_concerns"],
        "source": "; ".join(sources[:3]) if sources else "EDGAR EFTS",
        "confidence": 0.75 if not errors else 0.60,
        "errors": errors if errors else None,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--cik", required=True)
    p.add_argument("--company-name", default="")
    p.add_argument("--drug", default="")
    p.add_argument("--indication", default="")
    args = p.parse_args()
    out = get_sponsor_history(args.ticker, args.cik, args.company_name, args.drug, args.indication)
    print(json.dumps(out, indent=2, default=str))
