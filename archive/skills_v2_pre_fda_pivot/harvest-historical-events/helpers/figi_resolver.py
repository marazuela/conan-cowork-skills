"""OpenFIGI ticker resolution with JP 5-character handling (Q-003).

Two paths:
  1. EDGAR submissions API (preferred, no auth, no rate-limit issue)
     Endpoint: https://data.sec.gov/submissions/CIK<10-digit>.json
     Returns issuer's primary ticker(s) directly.

  2. OpenFIGI mapping endpoint (fallback)
     Endpoint: https://api.openfigi.com/v3/mapping
     Public limit: 25 jobs/min, 100 mappings/min without API key.
     Returns multiple FIGI matches per CIK (one per share class / exchange).

JP 5-character ticker fix (Q-003): Tokyo Stock Exchange uses 4-digit numeric
codes that frequently collide with US 4-letter tickers when truncated. We keep
the full 5-char code (4 digits + check digit if returned) and the company name
for downstream rendering as `<ticker> (<company>)` per CLAUDE.md §1.7.

CLI usage:
    python figi_resolver.py --cik 0001870404 --user-agent "..."
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_USER_AGENT = "Investment-Tool-Skill harvest-historical-events research@local"
SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
PER_REQUEST_TIMEOUT_S = 8.0
OPENFIGI_THROTTLE_S = 0.6  # < 100 req/min


def resolve_via_edgar_submissions(cik: str, user_agent: str = DEFAULT_USER_AGENT) -> dict:
    """Look up issuer tickers via EDGAR submissions API.

    Returns {"tickers": [...], "exchanges": [...], "name": str, "source": "edgar_submissions"}
    or {"tickers": [], "errors": [...]} on failure.
    """
    cik_padded = str(cik).zfill(10)
    url = f"{SEC_SUBMISSIONS_BASE}/CIK{cik_padded}.json"
    out = {"tickers": [], "exchanges": [], "name": "", "source": "edgar_submissions", "errors": []}

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        out["errors"].append({"http": e.code, "msg": str(e)})
        return out
    except urllib.error.URLError as e:
        out["errors"].append({"url_error": str(e)})
        return out
    except Exception as e:
        out["errors"].append({"exception": str(e)})
        return out

    out["tickers"] = list(payload.get("tickers") or [])
    out["exchanges"] = list(payload.get("exchanges") or [])
    out["name"] = payload.get("name") or ""
    return out


def resolve_via_openfigi(cik: str, user_agent: str = DEFAULT_USER_AGENT) -> dict:
    """OpenFIGI fallback. Returns first equity match.

    Returns {"ticker": str|None, "figi": str|None, "exch_code": str|None,
             "name": str|None, "source": "openfigi", "errors": [...], "is_jp_5char": bool}.
    """
    cik_padded = str(cik).zfill(10)
    body = json.dumps([{"idType": "ID_CIK", "idValue": cik_padded}]).encode("utf-8")
    out = {
        "ticker": None,
        "figi": None,
        "exch_code": None,
        "name": None,
        "source": "openfigi",
        "errors": [],
        "is_jp_5char": False,
    }

    try:
        req = urllib.request.Request(
            OPENFIGI_MAPPING_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        out["errors"].append({"http": e.code, "msg": str(e)})
        return out
    except urllib.error.URLError as e:
        out["errors"].append({"url_error": str(e)})
        return out
    except Exception as e:
        out["errors"].append({"exception": str(e)})
        return out

    if not isinstance(payload, list) or not payload:
        out["errors"].append({"empty_response": True})
        return out

    first = payload[0]
    if "data" not in first or not first["data"]:
        out["errors"].append({"warning": first.get("warning", "no data")})
        return out

    # Pick first equity-like match
    equity = None
    for d in first["data"]:
        if (d.get("securityType") or "").lower().startswith("common stock") or \
           (d.get("securityType2") or "").lower() == "common stock":
            equity = d
            break
    equity = equity or first["data"][0]

    out["figi"] = equity.get("figi")
    out["exch_code"] = equity.get("exchCode")
    out["name"] = equity.get("name")

    raw_ticker = equity.get("ticker") or ""
    # JP 5-char fix: Tokyo Stock Exchange tickers are 4 numeric digits
    # (sometimes 5 with a check char). We keep the full numeric form and
    # flag for downstream rendering.
    if raw_ticker.isdigit() and 4 <= len(raw_ticker) <= 5:
        out["ticker"] = raw_ticker
        out["is_jp_5char"] = True
    else:
        out["ticker"] = raw_ticker.split(":")[0] if raw_ticker else None

    return out


def resolve_ticker(cik: str, user_agent: str = DEFAULT_USER_AGENT, use_openfigi_fallback: bool = True) -> dict:
    """Two-step resolution: EDGAR submissions first, OpenFIGI fallback.

    Returns {"ticker": str|None, "figi": str|None, "name": str|None,
             "source_chain": [...], "is_jp_5char": bool, "errors": [...]}.
    """
    out = {
        "ticker": None,
        "figi": None,
        "name": None,
        "source_chain": [],
        "is_jp_5char": False,
        "errors": [],
    }

    sec = resolve_via_edgar_submissions(cik, user_agent=user_agent)
    out["source_chain"].append("edgar_submissions")
    if sec.get("tickers"):
        out["ticker"] = sec["tickers"][0]
        out["name"] = sec.get("name") or ""
        if sec.get("errors"):
            out["errors"].extend(sec["errors"])
        return out
    if sec.get("errors"):
        out["errors"].extend(sec["errors"])

    if not use_openfigi_fallback:
        return out

    time.sleep(OPENFIGI_THROTTLE_S)
    figi = resolve_via_openfigi(cik, user_agent=user_agent)
    out["source_chain"].append("openfigi")
    out["ticker"] = figi.get("ticker")
    out["figi"] = figi.get("figi")
    out["name"] = figi.get("name") or out["name"]
    out["is_jp_5char"] = bool(figi.get("is_jp_5char"))
    if figi.get("errors"):
        out["errors"].extend(figi["errors"])

    return out


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Resolve ticker for a CIK")
    p.add_argument("--cik", required=True)
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--no-openfigi", action="store_true")
    args = p.parse_args()

    result = resolve_ticker(
        args.cik, user_agent=args.user_agent, use_openfigi_fallback=not args.no_openfigi
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ticker") else 2)
