"""Primary-source client wrappers used by the kill-condition checkers.

These wrappers prefer authoritative sources (SEC EDGAR submissions API, FDA
press release feed, Federal Register, yfinance Yahoo prices) but degrade
gracefully when a source is unreachable.

Each function returns a dict of shape:
    {
        "ok": bool,
        "endpoint": "edgar|fda|federal_register|yfinance|...",
        "result": <payload> | None,
        "error_class": "ok|http_error|auth_required|timeout|parse_error|unavailable",
        "confidence": float in [0, 1],
        "source_url": "https://...",
    }

The wrappers never raise to the caller — they return a structured failure.
This is what lets the orchestrator flag a kill condition as "unverifiable"
rather than crashing the sweep when the network is flaky.

Note: This skill is invoked inside the Cowork sandbox where network reach to
EDGAR / FDA is allowed but not guaranteed. The wrappers respect a 10-second
per-call timeout. Bulk callers should also enforce a global deadline.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


_USER_AGENT = (
    "InvestmentTool-KillSweep/1.0 (contact: javiergorordo13@hotmail.com)"
)
_TIMEOUT_S = 10.0


def _http_get(url: str, accept: str = "application/json") -> Dict[str, Any]:
    """Plain GET with a UA header. Returns structured ok/error dict."""
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": accept}
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
            return {
                "ok": True,
                "status": resp.status,
                "body": body,
                "content_type": ctype,
                "url": url,
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": "", "url": url, "error": "http_error"}
    except urllib.error.URLError as e:
        return {"ok": False, "status": 0, "body": "", "url": url, "error": f"url_error:{e.reason}"}
    except Exception as e:  # pragma: no cover — defensive
        return {"ok": False, "status": 0, "body": "", "url": url, "error": f"exception:{type(e).__name__}"}


def edgar_recent_filings(cik: str, since_iso: Optional[str] = None) -> Dict[str, Any]:
    """Fetch recent EDGAR filings via the submissions JSON for a CIK.

    Returns the most recent ~40 filings (whatever EDGAR returns) as a list of
    dicts {form, filed, accession, primary_doc_url}. Filters out filings older
    than `since_iso` if provided.
    """
    if not cik:
        return {"ok": False, "endpoint": "edgar", "error_class": "missing_cik", "confidence": 0.0}
    cik_padded = str(cik).strip().lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    r = _http_get(url, accept="application/json")
    if not r["ok"]:
        return {
            "ok": False,
            "endpoint": "edgar",
            "error_class": "unavailable",
            "source_url": url,
            "confidence": 0.0,
            "detail": r.get("error", "unknown"),
        }
    try:
        data = json.loads(r["body"])
    except (TypeError, ValueError):
        return {
            "ok": False,
            "endpoint": "edgar",
            "error_class": "parse_error",
            "source_url": url,
            "confidence": 0.0,
        }
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    out = []
    for i, form in enumerate(forms):
        filed = dates[i] if i < len(dates) else None
        if since_iso and filed and filed < since_iso:
            continue
        acc = accessions[i] if i < len(accessions) else None
        pdoc = primary_docs[i] if i < len(primary_docs) else None
        primary_url = None
        if acc and pdoc:
            acc_clean = acc.replace("-", "")
            primary_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik_padded)}/{acc_clean}/{pdoc}"
            )
        out.append(
            {
                "form": form,
                "filed": filed,
                "accession": acc,
                "primary_doc_url": primary_url,
            }
        )
    return {
        "ok": True,
        "endpoint": "edgar",
        "result": out,
        "source_url": url,
        "confidence": 0.95,
        "error_class": "ok",
    }


def federal_register_search(term: str) -> Dict[str, Any]:
    """Search Federal Register for advisory-committee announcements matching `term`."""
    if not term:
        return {"ok": False, "endpoint": "federal_register", "error_class": "missing_term", "confidence": 0.0}
    q = urllib.parse.quote(term) if hasattr(urllib, "parse") else term
    url = (
        "https://www.federalregister.gov/api/v1/documents.json?"
        f"per_page=20&order=newest&conditions[term]={q}"
    )
    r = _http_get(url)
    if not r["ok"]:
        return {
            "ok": False,
            "endpoint": "federal_register",
            "error_class": "unavailable",
            "source_url": url,
            "confidence": 0.0,
        }
    try:
        data = json.loads(r["body"])
    except (TypeError, ValueError):
        return {"ok": False, "endpoint": "federal_register", "error_class": "parse_error", "source_url": url, "confidence": 0.0}
    return {
        "ok": True,
        "endpoint": "federal_register",
        "result": data.get("results", []),
        "source_url": url,
        "confidence": 0.85,
        "error_class": "ok",
    }


def fda_recent_press(query: str) -> Dict[str, Any]:
    """Best-effort FDA press-release / approvals lookup.

    Returns a structured 'unavailable' result by default; callers should treat
    `regulatory_decision_issued` as `unverifiable` when this returns `ok=False`,
    not as `clear`. The skill prefers EDGAR 8-K plus issuer press release as
    the corroborating signal anyway.
    """
    return {
        "ok": False,
        "endpoint": "fda",
        "error_class": "unavailable",
        "confidence": 0.0,
        "result": None,
        "source_url": "https://www.fda.gov/news-events/press-announcements",
        "note": "FDA press-release feed not directly queried in this build; rely on EDGAR + issuer 8-K for binary_catalyst confirmation.",
    }


def yfinance_close(ticker: str) -> Dict[str, Any]:
    """Best-effort latest-close fetch for a ticker.

    The Cowork sandbox may or may not have yfinance installed; this wrapper
    attempts the import lazily and returns `ok=False, error_class="unavailable"`
    if the dependency is missing, rather than crashing the sweep.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return {
            "ok": False,
            "endpoint": "yfinance",
            "error_class": "unavailable",
            "confidence": 0.0,
            "result": None,
            "source_url": f"https://finance.yahoo.com/quote/{ticker}",
            "note": "yfinance not installed in this environment",
        }
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d")
        if h is None or len(h) == 0:
            return {
                "ok": False,
                "endpoint": "yfinance",
                "error_class": "no_data",
                "confidence": 0.0,
                "source_url": f"https://finance.yahoo.com/quote/{ticker}",
            }
        last = h.iloc[-1]
        return {
            "ok": True,
            "endpoint": "yfinance",
            "result": {
                "ticker": ticker,
                "close": float(last["Close"]),
                "date": str(h.index[-1].date()),
            },
            "confidence": 0.85,
            "source_url": f"https://finance.yahoo.com/quote/{ticker}",
            "error_class": "ok",
        }
    except Exception as e:  # pragma: no cover — defensive
        return {
            "ok": False,
            "endpoint": "yfinance",
            "error_class": f"exception:{type(e).__name__}",
            "confidence": 0.0,
            "source_url": f"https://finance.yahoo.com/quote/{ticker}",
        }


def courtlistener_docket(docket_id: str) -> Dict[str, Any]:
    """CourtListener requires an auth token (API key) for full access.

    Returns `auth_required` when the env var `COURTLISTENER_API_TOKEN` is
    missing. Per Q-017 in OPEN_QUESTIONS, the token isn't yet provisioned in
    this build; callers should treat litigation court-docket conditions as
    `unverifiable` until that lands.
    """
    token = os.environ.get("COURTLISTENER_API_TOKEN")
    if not token:
        return {
            "ok": False,
            "endpoint": "courtlistener",
            "error_class": "auth_required",
            "confidence": 0.0,
            "result": None,
            "source_url": f"https://www.courtlistener.com/docket/{docket_id}/",
        }
    url = f"https://www.courtlistener.com/api/rest/v3/dockets/{docket_id}/"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Token {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return {
                "ok": True,
                "endpoint": "courtlistener",
                "result": data,
                "confidence": 0.9,
                "source_url": url,
                "error_class": "ok",
            }
    except Exception as e:  # pragma: no cover
        return {
            "ok": False,
            "endpoint": "courtlistener",
            "error_class": f"exception:{type(e).__name__}",
            "confidence": 0.0,
            "source_url": url,
        }
