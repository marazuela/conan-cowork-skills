"""yfinance + EDGAR helpers for label-outcomes-from-prices.

Provides:
  - lookup_ticker_from_cik(cik): EDGAR submissions JSON -> first ticker
  - fetch_yahoo_history(ticker, start_dt, end_dt): list of (epoch_ts, adj_close)
  - compute_forward_returns(history, anchor_dt, windows_days): dict of returns

Mirrors the patterns in 02_System/engine/tools/plan/step_p1_04_forward_returns.py
but parameterized for any return-window list and with a `prefer_quote_close`
fallback flag.

No external dependencies — uses urllib only. Handles network errors gracefully
by returning empty / None.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


YAHOO_USER_AGENT = "Mozilla/5.0 (Investment-Tool-Label-Outcomes) Python-urllib/3"
EDGAR_USER_AGENT = "Investment-Tool-Label-Outcomes research@local"
PER_REQUEST_TIMEOUT_S = 6.0
THROTTLE_SLEEP_S = 0.6


def parse_iso_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        parts = [int(x) for x in s.split("-")[:3]]
        return datetime(*parts, tzinfo=timezone.utc)
    except Exception:
        return None


def lookup_ticker_from_cik(cik: str) -> Optional[str]:
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
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        tickers = data.get("tickers") or []
        if tickers:
            return str(tickers[0]).upper()
    except Exception:
        pass
    return None


def fetch_yahoo_history(
    ticker: str, start_dt: datetime, end_dt: datetime
) -> Tuple[List[Tuple[int, float]], str]:
    """Returns (history, source_field) tuple.

    source_field is "adjclose" if the adjusted close was available, "quote_close"
    on the fallback path, or "" on failure.
    """
    if not ticker:
        return [], ""
    p1 = int(start_dt.timestamp())
    p2 = int(end_dt.timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(ticker)
        + "?period1="
        + str(p1)
        + "&period2="
        + str(p2)
        + "&interval=1d&events=div%7Csplit"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": YAHOO_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return [], ""
    chart = (data.get("chart") or {}).get("result") or []
    if not chart:
        return [], ""
    result = chart[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adjclose_list = indicators.get("adjclose") or []
    closes: List[Optional[float]] = []
    source = ""
    if adjclose_list:
        closes = adjclose_list[0].get("adjclose") or []
        source = "adjclose"
    if not closes:
        quote_list = indicators.get("quote") or []
        if quote_list:
            closes = quote_list[0].get("close") or []
            source = "quote_close"
    if not timestamps or not closes:
        return [], ""
    out: List[Tuple[int, float]] = []
    for ts, c in zip(timestamps, closes):
        if c is not None:
            out.append((int(ts), float(c)))
    return out, source


def find_close_at_or_after(
    history: List[Tuple[int, float]], target_ts: int
) -> Optional[Tuple[int, float]]:
    for ts, c in history:
        if ts >= target_ts:
            return (ts, c)
    return None


def compute_forward_returns(
    history: List[Tuple[int, float]],
    anchor_dt: datetime,
    windows_days: List[int],
) -> Dict[str, Any]:
    """Compute forward returns at each window. Returns dict with keys
    ret_<w>d (None if window unresolvable), ret_<w>d_annualized, anchor_close,
    and anchor_ts.
    """
    if not history:
        return {}
    anchor_ts = int(anchor_dt.timestamp())
    anchor_pair = find_close_at_or_after(history, anchor_ts)
    if anchor_pair is None:
        return {}
    anchor_ts, anchor_close = anchor_pair
    if anchor_close is None or anchor_close <= 0:
        return {}
    out: Dict[str, Any] = {"anchor_close": round(anchor_close, 4), "anchor_ts": anchor_ts}
    for w in windows_days:
        target_ts = anchor_ts + int(w) * 86400
        forward_pair = find_close_at_or_after(history, target_ts)
        key = "ret_" + str(int(w)) + "d"
        ann_key = key + "_annualized"
        if forward_pair is None:
            out[key] = None
            out[ann_key] = None
            continue
        _, forward_close = forward_pair
        if forward_close is None or forward_close <= 0:
            out[key] = None
            out[ann_key] = None
            continue
        ret = (forward_close / anchor_close) - 1.0
        out[key] = round(ret, 6)
        try:
            ann = (1.0 + ret) ** (365.0 / float(w)) - 1.0
            # Cap absurdly large annualized returns from short windows
            if ann > 1e6 or ann != ann:  # NaN check
                ann = None
            else:
                ann = round(ann, 6)
        except Exception:
            ann = None
        out[ann_key] = ann
    return out


def yahoo_chart_source_url(ticker: str) -> str:
    return "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(ticker)


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--filed-at", required=True, help="YYYY-MM-DD")
    ap.add_argument("--windows", default="30,60,90,180,365")
    args = ap.parse_args()
    anchor = parse_iso_date(args.filed_at)
    if anchor is None:
        print(json.dumps({"status": "error", "msg": "invalid filed-at"}))
        sys.exit(2)
    start = anchor - timedelta(days=5)
    windows = [int(w) for w in args.windows.split(",")]
    end = anchor + timedelta(days=max(windows) + 14)
    history, src = fetch_yahoo_history(args.ticker, start, end)
    rets = compute_forward_returns(history, anchor, windows)
    print(
        json.dumps(
            {
                "ticker": args.ticker,
                "anchor": args.filed_at,
                "history_len": len(history),
                "history_source": src,
                "forward_returns": rets,
            },
            indent=2,
        )
    )
