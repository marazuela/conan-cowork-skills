"""EDGAR full-text search adapter for harvest-historical-events.

Endpoint: https://efts.sec.gov/LATEST/search-index
Rate limit: throttled to 2 req/s (SEC publishes 10 req/s ceiling).
User-Agent: required by SEC fair-access policy.

Pagination: `from=0,10,20,...` until hits.total.value exhausted.

CLI usage:
    python edgar_fulltext_search.py --form S-4 --year 2024 --month 1 \\
        --user-agent "Investment-Tool-Skill harvest research@local"

Library usage:
    from edgar_fulltext_search import search_form_month, parse_hit_to_event
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import datetime, timezone

EDGAR_SEARCH_BASE = "https://efts.sec.gov/LATEST/search-index"
DEFAULT_USER_AGENT = "Investment-Tool-Skill harvest-historical-events research@local"
PER_REQUEST_TIMEOUT_S = 8.0
THROTTLE_SLEEP_S = 0.5  # 2 req/s


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_get_json(url: str, user_agent: str, timeout: float = PER_REQUEST_TIMEOUT_S) -> dict:
    """Fetch URL, parse JSON. Raises urllib.error.URLError / HTTPError on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def search_form_month(
    form_type: str,
    year: int,
    month: int,
    user_agent: str = DEFAULT_USER_AGENT,
    max_pages: int = 20,
    page_size: int = 10,
) -> dict:
    """Query EDGAR full-text search for one (form_type, year, month).

    Returns {"hits": [...], "total": int, "pages_fetched": N, "errors": []}.
    Each hit is the raw EDGAR record (not yet normalized).

    Throttles between paginated requests at THROTTLE_SLEEP_S.
    Implements exponential backoff on 429/503 (1s/2s/4s, then surface).
    """
    last_day = monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"

    out = {"hits": [], "total": 0, "pages_fetched": 0, "errors": []}

    for page in range(max_pages):
        offset = page * page_size
        params = {
            "q": "",
            "dateRange": "custom",
            "startdt": start,
            "enddt": end,
            "forms": form_type,
            "from": offset,
        }
        url = f"{EDGAR_SEARCH_BASE}?{urllib.parse.urlencode(params)}"

        backoff = 1.0
        attempts = 0
        while attempts < 4:
            try:
                payload = _http_get_json(url, user_agent)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempts < 3:
                    time.sleep(backoff)
                    backoff *= 2
                    attempts += 1
                    continue
                out["errors"].append({"url": url, "http": e.code, "msg": str(e)})
                return out
            except urllib.error.URLError as e:
                if attempts < 3:
                    time.sleep(backoff)
                    backoff *= 2
                    attempts += 1
                    continue
                out["errors"].append({"url": url, "url_error": str(e)})
                return out
        else:
            out["errors"].append({"url": url, "exhausted_retries": True})
            return out

        hits = payload.get("hits", {}).get("hits", []) or []
        total = payload.get("hits", {}).get("total", {}).get("value", 0)
        out["total"] = total
        out["pages_fetched"] = page + 1
        out["hits"].extend(hits)

        if offset + len(hits) >= total or not hits:
            break
        time.sleep(THROTTLE_SLEEP_S)

    return out


def parse_hit_to_event(hit: dict, profile: str, bucket: str, harvester_version: str = "v1") -> dict | None:
    """Normalize one EDGAR full-text hit into an event dict.

    Returns None if required fields are missing (caller decides what to do).
    """
    src = hit.get("_source") or hit
    if not src:
        return None

    accession = src.get("adsh") or hit.get("_id") or ""
    form_type = src.get("form") or src.get("form_type") or ""
    filed_at = src.get("file_date") or src.get("filed_at") or ""
    ciks = src.get("ciks") or []
    cik = ciks[0] if ciks else ""
    display_names = src.get("display_names") or []
    company_name = display_names[0] if display_names else ""

    # Best-effort ticker parse from "COMPANY NAME (TICKER) (CIK ...)"
    ticker = None
    if company_name and "(" in company_name:
        try:
            paren_chunks = [p.split(")")[0] for p in company_name.split("(")[1:]]
            for chunk in paren_chunks:
                stripped = chunk.strip()
                if stripped.startswith("CIK"):
                    continue
                if 1 <= len(stripped) <= 6 and stripped.replace(",", "").replace(" ", "").isalnum():
                    ticker = stripped.split(",")[0].strip()
                    break
        except Exception:
            ticker = None

    if not (accession and form_type and filed_at and cik):
        return None

    cik_padded = str(cik).zfill(10)
    primary_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}-index.htm"
    )

    event_id = hashlib.sha1(
        f"{accession}|{cik_padded}|{form_type}".encode()
    ).hexdigest()[:24]

    confidence = 0.95 if ticker else 0.85

    return {
        "event_id": event_id,
        "bucket": bucket,
        "form_type": form_type,
        "filed_at": filed_at,
        "cik": cik_padded,
        "ticker": ticker,
        "figi": None,
        "company_name": company_name,
        "accession_number_or_id": accession,
        "primary_source_url": primary_url,
        "features": {
            "form": form_type,
            "is_amendment_form": int("/A" in form_type or form_type.endswith("A")),
            "is_definitive_form": int(form_type.startswith("DEFM") or form_type.startswith("DEF ")),
        },
        "confidence": confidence,
        "source": primary_url,
        "harvested_at": _utc_now_iso(),
        "harvester": f"harvest-historical-events.{harvester_version}",
        "_profile": profile,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="EDGAR full-text search one month")
    p.add_argument("--form", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--max-pages", type=int, default=20)
    args = p.parse_args()

    result = search_form_month(
        args.form, args.year, args.month, user_agent=args.user_agent, max_pages=args.max_pages
    )
    print(json.dumps({
        "form": args.form,
        "year": args.year,
        "month": args.month,
        "total": result["total"],
        "pages_fetched": result["pages_fetched"],
        "hits_returned": len(result["hits"]),
        "errors": result["errors"],
    }, indent=2))
    sys.exit(0 if not result["errors"] else 1)
