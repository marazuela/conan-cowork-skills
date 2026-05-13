"""fda_class_lookup.py — openFDA wrapper for class-precedent skill (P2).

Queries https://api.fda.gov/drug/drugsfda.json for approval history of drugs
in a class. Supports query by generic name, brand name, MoA pharmacological
class, or active ingredient. Normalizes results to a small dict shape that
analyze.py consumes.

Returns a dict:
    {"ok": bool, "results": [...], "n": int, "source_url": str, "reason": "..."}

A single result entry looks like:
    {
      "appl_no": "212102",
      "appl_type": "NDA",
      "sponsor": "Janssen",
      "brand_name": "Spravato",
      "generic_name": "esketamine",
      "submission_type": "ORIG-1",
      "submission_status": "AP",
      "submission_status_date": "2019-03-05",
      "review_priority": "PRIORITY",
      "indication": "...",
      "boxed_warning": null,
      "rems": null,
      "source": "https://api.fda.gov/...",
      "confidence": 0.95
    }

Network and rate-limit handling: 3-attempt exponential backoff, 10s timeout.
Returns ok=False rather than crashing if openFDA is unreachable.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

OPENFDA_BASE = "https://api.fda.gov/drug/drugsfda.json"
USER_AGENT = "investment-tool-research-clinical-class-precedent/1.0 (skill-build)"
DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 3


def _http_get_json(url: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status >= 400:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                payload = json.loads(resp.read().decode("utf-8"))
                return True, payload, ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTPError {e.code}"
            if e.code == 404:
                # openFDA returns 404 on no-match — treat as ok with empty results
                return True, {"results": []}, ""
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


def _build_query(field: str, value: str) -> str:
    """openFDA uses Lucene-style queries. We URL-encode values defensively."""
    safe_value = value.replace('"', "")
    return f'{field}:"{safe_value}"'


def _normalize(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A single openFDA record can have multiple submissions; emit one row per ORIG-N."""
    rows: List[Dict[str, Any]] = []
    appl_no = record.get("application_number", "")
    sponsor = record.get("sponsor_name", "")
    openfda = record.get("openfda", {}) or {}
    brand = (openfda.get("brand_name") or [None])[0]
    generic = (openfda.get("generic_name") or [None])[0]
    submissions = record.get("submissions", []) or []
    for sub in submissions:
        st_date = sub.get("submission_status_date")
        rows.append({
            "appl_no": appl_no,
            "appl_type": record.get("application_number", "")[:3] if appl_no else None,
            "sponsor": sponsor,
            "brand_name": brand,
            "generic_name": generic,
            "submission_type": sub.get("submission_type"),
            "submission_class_code": sub.get("submission_class_code"),
            "submission_class_code_description": sub.get("submission_class_code_description"),
            "submission_status": sub.get("submission_status"),
            "submission_status_date": st_date,
            "review_priority": sub.get("review_priority"),
            "submission_number": sub.get("submission_number"),
            "indication": (openfda.get("pharm_class_epc") or [None])[0],
            "pharm_class_moa": openfda.get("pharm_class_moa") or [],
            "pharm_class_epc": openfda.get("pharm_class_epc") or [],
            "substance_name": openfda.get("substance_name") or [],
            "boxed_warning": None,  # not in drugsfda; would require label parsing
            "rems": None,
            "source": OPENFDA_BASE,
            "confidence": 0.90,
        })
    return rows


def search_by_generic_name(name: str, limit: int = 25) -> Dict[str, Any]:
    """Look up approval history for an active ingredient / generic name."""
    if not name:
        return {"ok": False, "results": [], "n": 0, "source_url": "", "reason": "empty_query"}
    q = _build_query("openfda.generic_name", name.lower())
    url = f"{OPENFDA_BASE}?search={urllib.parse.quote(q)}&limit={limit}"
    ok, payload, err = _http_get_json(url)
    if not ok:
        return {"ok": False, "results": [], "n": 0, "source_url": url, "reason": err}
    results: List[Dict[str, Any]] = []
    for rec in (payload or {}).get("results", []) or []:
        results.extend(_normalize(rec))
    return {"ok": True, "results": results, "n": len(results), "source_url": url, "reason": ""}


def search_by_brand_name(name: str, limit: int = 25) -> Dict[str, Any]:
    if not name:
        return {"ok": False, "results": [], "n": 0, "source_url": "", "reason": "empty_query"}
    q = _build_query("openfda.brand_name", name.lower())
    url = f"{OPENFDA_BASE}?search={urllib.parse.quote(q)}&limit={limit}"
    ok, payload, err = _http_get_json(url)
    if not ok:
        return {"ok": False, "results": [], "n": 0, "source_url": url, "reason": err}
    results: List[Dict[str, Any]] = []
    for rec in (payload or {}).get("results", []) or []:
        results.extend(_normalize(rec))
    return {"ok": True, "results": results, "n": len(results), "source_url": url, "reason": ""}


def search_by_moa(moa: str, limit: int = 100) -> Dict[str, Any]:
    if not moa:
        return {"ok": False, "results": [], "n": 0, "source_url": "", "reason": "empty_query"}
    q = _build_query("openfda.pharm_class_moa", moa.lower())
    url = f"{OPENFDA_BASE}?search={urllib.parse.quote(q)}&limit={limit}"
    ok, payload, err = _http_get_json(url)
    if not ok:
        return {"ok": False, "results": [], "n": 0, "source_url": url, "reason": err}
    results: List[Dict[str, Any]] = []
    for rec in (payload or {}).get("results", []) or []:
        results.extend(_normalize(rec))
    return {"ok": True, "results": results, "n": len(results), "source_url": url, "reason": ""}


def lookup_class(class_drugs: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    """Aggregate openFDA pulls for a list of (generic, brand, sponsor) tuples.

    Returns ALL submissions; caller filters by ORIG-1 / AP / date.
    """
    all_rows: List[Dict[str, Any]] = []
    sources: List[str] = []
    failed: List[str] = []
    for generic, brand, _sponsor in class_drugs or []:
        # Prefer generic name search; fall back to brand
        r = search_by_generic_name(generic) if generic else {"ok": False}
        if not r["ok"] or r["n"] == 0:
            if brand and brand != "investigational":
                r = search_by_brand_name(brand)
        if r["ok"]:
            all_rows.extend(r["results"])
            sources.append(r["source_url"])
        else:
            failed.append(f"{generic}/{brand}")
    return {
        "ok": True,
        "rows": all_rows,
        "sources": sources,
        "failed": failed,
        "n": len(all_rows),
    }


def filter_first_approvals(rows: List[Dict[str, Any]], lookback_years: int = 10) -> List[Dict[str, Any]]:
    """Keep only ORIG-1 AP submissions inside the lookback window."""
    import datetime as _dt
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=int(lookback_years) * 365)).date().isoformat()
    out = []
    seen = set()
    for r in rows:
        if r.get("submission_type") != "ORIG-1":
            continue
        if r.get("submission_status") != "AP":
            continue
        st_date = r.get("submission_status_date") or ""
        # openFDA returns dates as YYYYMMDD; normalize to YYYY-MM-DD
        if len(st_date) == 8 and st_date.isdigit():
            st_date = f"{st_date[:4]}-{st_date[4:6]}-{st_date[6:]}"
            r["submission_status_date"] = st_date
        if not st_date or st_date < cutoff:
            continue
        key = (r.get("appl_no"), r.get("submission_number"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


if __name__ == "__main__":
    import argparse
    import sys
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["generic", "brand", "moa", "class"], required=True)
    p.add_argument("--q", action="append", default=[], help="query value(s); for class mode pass generic;brand;sponsor triplets")
    p.add_argument("--limit", type=int, default=25)
    args = p.parse_args()
    if args.mode == "generic":
        out = search_by_generic_name(args.q[0] if args.q else "", limit=args.limit)
    elif args.mode == "brand":
        out = search_by_brand_name(args.q[0] if args.q else "", limit=args.limit)
    elif args.mode == "moa":
        out = search_by_moa(args.q[0] if args.q else "", limit=args.limit)
    else:
        triplets = []
        for s in args.q:
            parts = s.split(";")
            triplets.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
        out = lookup_class(triplets)
    print(json.dumps(out, indent=2, default=str))
    sys.exit(0)
