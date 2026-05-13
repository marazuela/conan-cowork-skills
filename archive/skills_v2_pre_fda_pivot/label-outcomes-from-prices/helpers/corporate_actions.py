"""Corporate actions resolver for label-outcomes-from-prices.

Given an event whose ticker returns no yfinance history, attempts to detect:
  - delisting (NT 10-K, 8-K Item 3.01)
  - name change (8-K Item 5.03)
  - spin-off completion (8-K Item 1.02 / 2.01)
  - OTC pink-sheet ticker reference

via EDGAR submissions API. Returns a structured action descriptor with the
recommended fallback ticker (if any).

This is a best-effort scanner. Any failure short-circuits to
{"action": "unresolvable", "fallback_ticker": null}. Confidence-marked
downstream by the orchestrator.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional


EDGAR_USER_AGENT = "Investment-Tool-Label-Outcomes research@local"
PER_REQUEST_TIMEOUT_S = 6.0

# 8-K item code -> action class
ITEM_TO_ACTION = {
    "1.02": "termination",
    "2.01": "completion_disposition_or_acquisition",
    "3.01": "delisting_or_failure_to_satisfy_listing",
    "5.03": "amendment_articles_or_change_fiscal_year",
    "5.07": "shareholder_vote_outcome",
}


def fetch_submissions(cik: str) -> Optional[Dict[str, Any]]:
    if not cik:
        return None
    try:
        cik_padded = str(cik).strip().lstrip("0").zfill(10)
        url = "https://data.sec.gov/submissions/CIK" + cik_padded + ".json"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": EDGAR_USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        parts = [int(x) for x in s.split("-")[:3]]
        return datetime(*parts, tzinfo=timezone.utc)
    except Exception:
        return None


def resolve_corporate_action(
    cik: str,
    filed_at_iso: str,
    original_ticker: Optional[str] = None,
    lookahead_days: int = 365,
) -> Dict[str, Any]:
    """Return a corporate-action descriptor.

    Output:
      {
        "action": "spinoff" | "name_change" | "delisting_to_otc" | "delisted" | "unresolvable",
        "fallback_ticker": <str or null>,
        "evidence": [{"form": ..., "filed": ..., "primary_doc": ..., "items": [...]}],
        "confidence": 0.0..1.0,
      }
    """
    out: Dict[str, Any] = {
        "action": "unresolvable",
        "fallback_ticker": None,
        "evidence": [],
        "confidence": 0.0,
    }
    filed_dt = parse_iso(filed_at_iso)
    if filed_dt is None or not cik:
        return out

    sub = fetch_submissions(cik)
    if not sub:
        return out

    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items_list = recent.get("items") or [""] * len(forms)
    primary_docs = recent.get("primaryDocument") or [""] * len(forms)

    window_end = filed_dt + timedelta(days=lookahead_days)
    relevant: list = []
    for i, fm in enumerate(forms):
        d = parse_iso(dates[i]) if i < len(dates) else None
        if d is None:
            continue
        if d < filed_dt or d > window_end:
            continue
        relevant.append(
            {
                "form": fm,
                "filed": dates[i],
                "items": items_list[i] if i < len(items_list) else "",
                "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
            }
        )

    # Detection rules (best-effort, ordered by specificity)
    for ev in relevant:
        items = (ev.get("items") or "").split(",")
        items = [it.strip() for it in items if it.strip()]
        form = ev["form"].upper()
        # Spin-off: 8-K with item 1.02 (termination of agreement) AND item 2.01 (acquisition/disposition)
        if form == "8-K" and ("1.02" in items and "2.01" in items):
            out["action"] = "spinoff"
            out["evidence"].append(ev)
            out["confidence"] = 0.65
            # We can't infer child ticker from submissions JSON alone; leave fallback null
            return out
        # Delisting via 3.01
        if form == "8-K" and "3.01" in items:
            out["action"] = "delisted"
            out["evidence"].append(ev)
            out["confidence"] = 0.65
            return out
        # Name change via 5.03 (amendment to articles)
        if form == "8-K" and "5.03" in items:
            out["action"] = "name_change"
            out["evidence"].append(ev)
            out["confidence"] = 0.55
            # New ticker may show up in subsequent submissions JSON .tickers field
            tickers_after = sub.get("tickers") or []
            if tickers_after and original_ticker and tickers_after[0].upper() != original_ticker.upper():
                out["fallback_ticker"] = tickers_after[0].upper()
                out["confidence"] = 0.70
            return out
        # NT 10-K → indicates filing is late, often pre-delisting
        if form == "NT 10-K":
            out["action"] = "delisted"
            out["evidence"].append(ev)
            out["confidence"] = 0.50
            return out

    # No corporate-action evidence found in window
    return out


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--cik", required=True)
    ap.add_argument("--filed-at", required=True)
    ap.add_argument("--ticker", default=None)
    args = ap.parse_args()
    res = resolve_corporate_action(args.cik, args.filed_at, args.ticker)
    print(json.dumps(res, indent=2))
