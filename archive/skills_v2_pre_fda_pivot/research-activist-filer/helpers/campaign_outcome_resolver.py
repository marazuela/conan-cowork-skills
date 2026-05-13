"""campaign_outcome_resolver.py — Per-campaign outcome classifier for P3.

For each (filer, target) campaign, query the target issuer's EDGAR
submissions API for follow-up filings (DEFM14A, 8-K, DEFC14A, PRREN14A,
SC TO-T) after the filer's first 13D. Apply the decision tree from
SKILL.md Step 4 to classify outcome.

Returns: {"outcome_status", "outcome_event", "outcome_event_date",
          "source_url", "confidence", "raw_signals"}
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
USER_AGENT = "investment-tool-research-activist-filer/1.0 javiergorordo13@hotmail.com"
DEFAULT_TIMEOUT = 12.0
MAX_RETRIES = 3


def _http_get(url: str, accept: str = "application/json") -> Tuple[bool, Optional[bytes], str]:
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
            )
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status == 429:
                    last_err = "HTTP 429 rate_limited"
                    time.sleep(min(2 ** (attempt + 2), 30))
                    continue
                if resp.status >= 400:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                return True, resp.read(), ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTPError {e.code}"
            if e.code == 429:
                time.sleep(min(2 ** (attempt + 2), 30))
                continue
            time.sleep(min(2 ** attempt, 8))
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8))
        except Exception as e:  # pragma: no cover
            last_err = f"unexpected: {type(e).__name__}: {e}"
            break
    return False, None, last_err


def _http_get_json(url: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    ok, body, err = _http_get(url, accept="application/json")
    if not ok or body is None:
        return False, None, err
    try:
        return True, json.loads(body.decode("utf-8")), ""
    except json.JSONDecodeError as e:
        return False, None, f"JSONDecodeError: {e}"


def _pad_cik(cik: str) -> str:
    return str(cik).strip().lstrip("0").zfill(10) if str(cik).strip() else "0000000000"


def _normalize_cik(cik: str) -> str:
    return str(cik).strip().lstrip("0") or "0"


def fetch_target_filings_post(target_cik: str, since_date: str) -> Dict[str, Any]:
    """Fetch target's filings filed on or after `since_date` (YYYY-MM-DD)."""
    padded = _pad_cik(target_cik)
    url = f"{SUBMISSIONS_BASE}/CIK{padded}.json"
    ok, payload, err = _http_get_json(url)
    if not ok or payload is None:
        return {"ok": False, "filings": [], "url": url, "reason": err}

    out: List[Dict[str, Any]] = []
    recent = ((payload or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    primary_descs = recent.get("primaryDocDescription", [])
    items = recent.get("items", [])

    n = min(len(forms), len(accs), len(dates))
    for i in range(n):
        if dates[i] < since_date:
            continue
        out.append({
            "form": forms[i],
            "accession_no": accs[i],
            "filing_date": dates[i],
            "primary_document": primary_docs[i] if i < len(primary_docs) else None,
            "primary_description": primary_descs[i] if i < len(primary_descs) else None,
            "items": items[i] if i < len(items) else None,
            "url_index": f"{ARCHIVES_BASE}/{_normalize_cik(target_cik)}/{(accs[i] or '').replace('-', '')}/{accs[i]}-index.htm",
        })

    target_name = (payload or {}).get("name", "")
    target_ticker = ((payload or {}).get("tickers") or [None])[0] if (payload or {}).get("tickers") else None
    target_sic = (payload or {}).get("sicDescription", "")
    target_sic_code = (payload or {}).get("sic", "")
    return {
        "ok": True,
        "filings": out,
        "url": url,
        "name": target_name,
        "ticker": target_ticker,
        "sic_description": target_sic,
        "sic": target_sic_code,
    }


def fetch_8k_text(target_cik: str, accession_no: str, primary_document: Optional[str]) -> Optional[str]:
    """Best-effort fetch of the 8-K body text for keyword classification."""
    acc_clean = (accession_no or "").replace("-", "")
    if not acc_clean:
        return None
    candidates = []
    if primary_document:
        candidates.append(f"{ARCHIVES_BASE}/{_normalize_cik(target_cik)}/{acc_clean}/{primary_document}")
    candidates.append(f"{ARCHIVES_BASE}/{_normalize_cik(target_cik)}/{acc_clean}/{accession_no}-index.htm")
    for url in candidates:
        ok, body, err = _http_get(url, accept="text/html,application/xml")
        if ok and body:
            txt = re.sub(r"<[^>]+>", " ", body.decode("utf-8", errors="ignore"))
            return re.sub(r"\s+", " ", txt)[:60000]
    return None


# Keyword tables for 8-K / target-filing classification
KW_SETTLEMENT_BOARD_SEATS = re.compile(
    r"\b(cooperation\s+agreement|settlement\s+agreement|board\s+representation|director\s+nomination\s+agreement|appointment.{0,40}director|appoint.{0,30}to.{0,30}board)\b",
    re.IGNORECASE,
)
KW_SETTLEMENT_STANDSTILL = re.compile(r"\b(standstill\s+agreement|standstill\s+provisions?)\b", re.IGNORECASE)
KW_PROXY_FIGHT_RESULT = re.compile(r"\b(election\s+of\s+directors|director\s+election\s+results|item\s*5\.07)\b", re.IGNORECASE)
KW_TENDER_OFFER = re.compile(r"\b(tender\s+offer|SC\s*TO\-T|SC\s*14D9)\b", re.IGNORECASE)


def classify_outcome(
    target_cik: str,
    target_name: str,
    filer_name: str,
    filer_aliases: List[str],
    first_13d_date: str,
    latest_13da_date: str,
    latest_position_pct: Optional[float],
) -> Dict[str, Any]:
    """Apply outcome-decision-tree to a single campaign.

    Returns the outcome record per SKILL.md Step 4 schema.
    """
    res = fetch_target_filings_post(target_cik, first_13d_date)
    raw_signals: List[Dict[str, Any]] = []
    sources: List[str] = []
    if not res["ok"]:
        return {
            "outcome_status": "manual_review",
            "outcome_event": "target submissions unavailable",
            "outcome_event_date": None,
            "source_url": res.get("url", ""),
            "confidence": 0.30,
            "raw_signals": [],
            "_status": "edgar_submissions_unavailable",
            "reason": res.get("reason", ""),
        }
    sources.append(res["url"])

    forms_present = {f["form"]: f for f in res["filings"]}
    by_form_list: Dict[str, List[Dict[str, Any]]] = {}
    for f in res["filings"]:
        by_form_list.setdefault(f["form"], []).append(f)

    # --- Sale / merger closed ---
    for form_name in ("DEFM14A", "8-K"):
        for f in by_form_list.get(form_name, []):
            items = (f.get("items") or "").upper()
            desc = (f.get("primary_description") or "").upper()
            if "2.01" in items or "COMPLETION OF ACQUISITION" in desc:
                return {
                    "outcome_status": "sale_merger_closed",
                    "outcome_event": f"{form_name} item 2.01 / completion of acquisition",
                    "outcome_event_date": f["filing_date"],
                    "source_url": f["url_index"],
                    "confidence": 0.88,
                    "raw_signals": [{"form": form_name, "filing_date": f["filing_date"], "url": f["url_index"]}],
                }

    # --- Topping bid / tender offer ---
    if "SC TO-T" in by_form_list or "SC 14D9" in by_form_list:
        first_to = (by_form_list.get("SC TO-T", []) + by_form_list.get("SC 14D9", []))[0]
        return {
            "outcome_status": "topping_bid_emerged",
            "outcome_event": "SC TO-T / SC 14D9 tender offer filed",
            "outcome_event_date": first_to["filing_date"],
            "source_url": first_to["url_index"],
            "confidence": 0.78,
            "raw_signals": [{"form": first_to["form"], "filing_date": first_to["filing_date"]}],
        }

    # --- Settlement (board seats) — scan 8-Ks for keyword match ---
    for f in by_form_list.get("8-K", []):
        # Quick check: items containing 5.02 (Departure/Election of Directors) is a strong signal
        items = (f.get("items") or "").upper()
        if "5.02" in items:
            body = fetch_8k_text(target_cik, f["accession_no"], f.get("primary_document"))
            if body and (KW_SETTLEMENT_BOARD_SEATS.search(body) or any(a.lower() in body.lower() for a in [filer_name] + filer_aliases if a)):
                return {
                    "outcome_status": "settlement_board_seats",
                    "outcome_event": "8-K Item 5.02 with cooperation/settlement language and filer name reference",
                    "outcome_event_date": f["filing_date"],
                    "source_url": f["url_index"],
                    "confidence": 0.82,
                    "raw_signals": [{"form": "8-K", "items": items, "filing_date": f["filing_date"]}],
                }

    # --- Settlement (standstill only) ---
    for f in by_form_list.get("8-K", []):
        body = fetch_8k_text(target_cik, f["accession_no"], f.get("primary_document"))
        if body and KW_SETTLEMENT_STANDSTILL.search(body) and not KW_SETTLEMENT_BOARD_SEATS.search(body):
            return {
                "outcome_status": "settlement_standstill_only",
                "outcome_event": "8-K mentioning standstill agreement, no board seat appointment",
                "outcome_event_date": f["filing_date"],
                "source_url": f["url_index"],
                "confidence": 0.68,
                "raw_signals": [{"form": "8-K", "filing_date": f["filing_date"]}],
            }

    # --- Proxy fight result ---
    if "DEFC14A" in by_form_list or "PREN14A" in by_form_list or "DFAN14A" in by_form_list:
        # Look for an Item 5.07 8-K reporting the vote
        for f in by_form_list.get("8-K", []):
            items = (f.get("items") or "").upper()
            if "5.07" in items:
                body = fetch_8k_text(target_cik, f["accession_no"], f.get("primary_document"))
                if body:
                    # If filer name appears alongside elected/won language, classify as won
                    if any(a.lower() in body.lower() for a in [filer_name] + filer_aliases if a):
                        return {
                            "outcome_status": "proxy_fight_won",
                            "outcome_event": "8-K Item 5.07 with filer name in vote results",
                            "outcome_event_date": f["filing_date"],
                            "source_url": f["url_index"],
                            "confidence": 0.80,
                            "raw_signals": [{"form": "8-K", "items": items, "filing_date": f["filing_date"]}],
                        }
                    return {
                        "outcome_status": "proxy_fight_lost",
                        "outcome_event": "8-K Item 5.07 vote results without filer slate election",
                        "outcome_event_date": f["filing_date"],
                        "source_url": f["url_index"],
                        "confidence": 0.78,
                        "raw_signals": [{"form": "8-K", "items": items, "filing_date": f["filing_date"]}],
                    }

    # --- Withdrawal (position dropped <5%) ---
    if latest_position_pct is not None and latest_position_pct < 5.0:
        return {
            "outcome_status": "withdrawal",
            "outcome_event": f"Latest 13D/A reports {latest_position_pct:.2f}% (<5% threshold)",
            "outcome_event_date": latest_13da_date,
            "source_url": "",
            "confidence": 0.72,
            "raw_signals": [{"signal": "position_drop", "latest_position_pct": latest_position_pct}],
        }

    # --- Active vs stale-active ---
    import datetime as dt
    try:
        latest_dt = dt.datetime.strptime(latest_13da_date, "%Y-%m-%d")
        age_days = (dt.datetime.utcnow() - latest_dt).days
    except Exception:
        age_days = 9999

    if age_days <= 365:
        return {
            "outcome_status": "active",
            "outcome_event": f"Latest 13D/A within last 365d ({age_days}d ago); no terminal event detected in target filings",
            "outcome_event_date": None,
            "source_url": res["url"],
            "confidence": 0.70,
            "raw_signals": [{"target_filings_scanned": len(res["filings"]), "age_days": age_days}],
        }

    return {
        "outcome_status": "stale_active",
        "outcome_event": f"Latest 13D/A {age_days}d ago; no terminal event found — manual review recommended",
        "outcome_event_date": None,
        "source_url": res["url"],
        "confidence": 0.50,
        "raw_signals": [{"target_filings_scanned": len(res["filings"]), "age_days": age_days}],
    }


def lookup_target_meta(target_cik: str) -> Dict[str, Any]:
    """Resolve target ticker, name, sector via SIC."""
    padded = _pad_cik(target_cik)
    url = f"{SUBMISSIONS_BASE}/CIK{padded}.json"
    ok, payload, err = _http_get_json(url)
    if not ok or payload is None:
        return {"ok": False, "ticker": None, "name": "", "sic": "", "sic_description": "", "reason": err}
    return {
        "ok": True,
        "ticker": ((payload or {}).get("tickers") or [None])[0] if (payload or {}).get("tickers") else None,
        "name": (payload or {}).get("name", ""),
        "sic": (payload or {}).get("sic", ""),
        "sic_description": (payload or {}).get("sicDescription", ""),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target-cik", required=True)
    p.add_argument("--filer-name", required=True)
    p.add_argument("--first-13d-date", required=True)
    p.add_argument("--latest-13da-date", required=True)
    p.add_argument("--latest-position-pct", type=float, default=None)
    args = p.parse_args()

    out = classify_outcome(
        target_cik=args.target_cik,
        target_name="",
        filer_name=args.filer_name,
        filer_aliases=[],
        first_13d_date=args.first_13d_date,
        latest_13da_date=args.latest_13da_date,
        latest_position_pct=args.latest_position_pct,
    )
    print(json.dumps(out, indent=2, default=str))
