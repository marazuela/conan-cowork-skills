"""adcom_history_lookup.py

Light-weight wrapper around the Federal Register API to detect FDA Advisory
Committee meetings matching a drug, indication, or division.

Inputs:
    --term: free-text term (drug, indication, division, MoA)

Outputs:
    JSON dict {
        "ok": bool,
        "results": [ {title, html_url, publication_date, agencies} ],
        "error_class": str,
        "source_url": str
    }

The Federal Register publishes AdCom notices typically 30+ days ahead of the
meeting. Absence of a hit in the last 90 days is a useful negative signal.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


_BASE = "https://www.federalregister.gov/api/v1"
_USER_AGENT = "InvestmentTool-FDA-Skill/1.0"
_TIMEOUT = 12.0


def _http_get(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return {"ok": True, "body": resp.read().decode("utf-8", errors="replace"), "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": "http_error"}
    except urllib.error.URLError as e:
        return {"ok": False, "status": 0, "error": f"url_error:{e.reason}"}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "status": 0, "error": f"exception:{type(e).__name__}"}


def search_adcom(term: str, per_page: int = 20) -> Dict[str, Any]:
    if not term:
        return {"ok": False, "results": [], "error_class": "missing_term", "source_url": None}
    q = urllib.parse.quote(term)
    url = (
        f"{_BASE}/documents.json?per_page={per_page}&order=newest"
        f"&conditions[term]={q}&conditions[type][]=NOTICE"
    )
    r = _http_get(url)
    if not r["ok"]:
        return {"ok": False, "results": [], "error_class": "unavailable", "source_url": url}
    try:
        data = json.loads(r["body"])
    except (TypeError, ValueError):
        return {"ok": False, "results": [], "error_class": "parse_error", "source_url": url}
    items = data.get("results", []) or []
    flat = [
        {
            "title": it.get("title"),
            "html_url": it.get("html_url"),
            "publication_date": it.get("publication_date"),
            "agencies": [a.get("name") for a in (it.get("agencies") or [])],
        }
        for it in items
        if "advisory committee" in (it.get("title") or "").lower()
    ]
    return {
        "ok": True,
        "results": flat,
        "error_class": "ok",
        "source_url": url,
        "raw_count": len(items),
        "filtered_count": len(flat),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Search Federal Register for AdCom notices")
    p.add_argument("--term", required=True, help="Drug, indication, or division name")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    result = search_adcom(args.term)
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
