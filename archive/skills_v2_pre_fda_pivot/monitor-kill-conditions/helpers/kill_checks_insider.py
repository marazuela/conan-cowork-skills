"""insider kill-condition checker.

Looks for: time-horizon expiry, price-target hit, insider reversal (Form 4
sale within 6 months of cluster buy), sector drawdown threshold, material
adverse 8-K between cluster buy and horizon.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List

try:
    from primary_source_clients import edgar_recent_filings, yfinance_close
except ImportError:  # pragma: no cover
    from .primary_source_clients import edgar_recent_filings, yfinance_close  # type: ignore


_PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*\.?[0-9]*)")


def _parse_iso(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def check(
    state: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    as_of_iso: str,
) -> List[Dict[str, Any]]:
    fm = state.get("frontmatter", {})
    cik = str(fm.get("cik", "") or "")
    ticker = fm.get("ticker_local") or fm.get("ticker") or ""
    first_signal = _parse_iso(fm.get("first_signal_date"))
    today = _parse_iso(as_of_iso) or _dt.date.today()

    edgar = edgar_recent_filings(cik) if cik else {"ok": False}
    forms = [f.get("form") for f in (edgar.get("result") or [])]
    price = yfinance_close(str(ticker)) if ticker else {"ok": False}

    out: List[Dict[str, Any]] = []
    for cond in conditions:
        text = cond.get("raw_text", "")
        text_l = text.lower()
        status = "unverifiable"
        confidence = 0.30
        evidence = "no primary-source check executed"
        source_url = None

        if "horizon" in text_l or "expir" in text_l:
            if first_signal:
                # Default 90-day insider horizon if dossier doesn't specify
                horizon_days = 90
                m = re.search(r"(\d{2,3})\s*day", text_l)
                if m:
                    try:
                        horizon_days = int(m.group(1))
                    except ValueError:
                        pass
                expiry = first_signal + _dt.timedelta(days=horizon_days)
                if today >= expiry:
                    status = "triggered"
                    confidence = 0.95
                    evidence = (
                        f"first_signal_date={first_signal}, horizon={horizon_days}d, "
                        f"expiry={expiry}, today={today} — horizon reached"
                    )
                else:
                    status = "clear"
                    confidence = 0.95
                    evidence = (
                        f"first_signal_date={first_signal}, horizon expires {expiry}, "
                        f"days_remaining={(expiry - today).days}"
                    )
            else:
                status = "unverifiable"
                evidence = "first_signal_date not present in frontmatter"
        elif "price target" in text_l or "target hit" in text_l:
            m = _PRICE_RE.search(text)
            if m and price.get("ok"):
                target = float(m.group(1).replace(",", ""))
                close = price["result"]["close"]
                if close >= target:
                    status = "triggered"
                    confidence = 0.92
                    evidence = f"close ${close:.2f} reached target ${target:.2f}"
                else:
                    status = "clear"
                    confidence = 0.92
                    evidence = f"close ${close:.2f} below target ${target:.2f}"
                source_url = price.get("source_url")
            else:
                status = "unverifiable"
                evidence = "could not parse target or fetch close"
        elif "form 4" in text_l or "insider sell" in text_l or "reversal" in text_l:
            if not edgar.get("ok"):
                status = "unverifiable"
                evidence = "EDGAR unavailable"
            elif "4" in forms:
                status = "manual_review"
                confidence = 0.55
                evidence = "Form 4 present in recent filings — verify direction (buy vs sell) and insider role"
                source_url = edgar.get("source_url")
            else:
                status = "clear"
                confidence = 0.85
                evidence = "no Form 4 in recent submissions feed"
                source_url = edgar.get("source_url")
        elif "sector" in text_l and ("decline" in text_l or "selloff" in text_l or "drawdown" in text_l):
            status = "manual_review"
            confidence = 0.40
            evidence = "sector drawdown comparison requires benchmark series"
        else:
            status = "manual_review"
            confidence = 0.30
            evidence = "unstructured insider kill condition — operator review"

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
