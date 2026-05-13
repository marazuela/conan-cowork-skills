"""acquirer_ma_history.py — EDGAR EFTS + submissions-API wrapper for P4.

Resolves an acquirer's CIK (when it has a US filer presence) and pulls every
acquirer-side M&A filing within the lookback window. Falls back to a
name-only path when EDGAR has no record (typical for non-US strategics
like BAWAG AG or Mitsubishi UFJ for inbound deals).

Key endpoints:
  - EFTS:        https://efts.sec.gov/LATEST/search-index
  - Submissions: https://data.sec.gov/submissions/CIK<padded>.json
  - Browse:      https://www.sec.gov/cgi-bin/browse-edgar

All requests carry a User-Agent identifying the project per SEC fair-access
policy. Network errors degrade gracefully — we never crash the orchestrator.

Usage:
    from acquirer_ma_history import resolve_acquirer, pull_acquirer_filings
    info = resolve_acquirer("BAWAG Group AG")
    filings = pull_acquirer_filings(info["cik"], lookback_years=10) if info["cik"] else []
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

USER_AGENT = "investment-tool-research-acquirer-history/1.0 javiergorordo13@hotmail.com"

ACQUIRER_FORM_TYPES = [
    "DEFM14A",
    "PREM14A",
    "S-4",
    "S-4/A",
    "SC TO-T",
    "SC TO-T/A",
    "SC TO-I",
    "SC TO-I/A",
    "SC 13E3",
    "SC 13E3/A",
    "8-K",
]


def _http_get(url: str, *, timeout: float = 12.0, max_retries: int = 3) -> Tuple[Optional[bytes], Optional[int], Optional[str]]:
    """GET with backoff + UA. Returns (body_bytes, status, error_text)."""
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return body, resp.status, None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate limited — exponential backoff
                time.sleep(min(2 ** attempt, 60))
                last_err = f"HTTP 429 (rate-limited) attempt {attempt + 1}"
                continue
            if 500 <= e.code < 600:
                time.sleep(2 ** attempt)
                last_err = f"HTTP {e.code} attempt {attempt + 1}"
                continue
            return None, e.code, f"HTTP {e.code}: {e.reason}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            time.sleep(2 ** attempt)
            last_err = f"network error attempt {attempt + 1}: {e}"
            continue
    return None, None, last_err or "exhausted retries"


def _pad_cik(cik) -> str:
    s = str(cik).strip().lstrip("0") or "0"
    return s.zfill(10)


def resolve_acquirer(acquirer_name: str, *, offline: bool = False) -> Dict:
    """Resolve an acquirer name to a CIK or name_only path.

    Returns a dict with at least: name, cik (or null), id_type, confidence, aliases.
    """
    out = {
        "name": acquirer_name,
        "cik": None,
        "id_type": "unknown",
        "confidence": 0.0,
        "aliases": [],
        "source": "EDGAR",
        "data_quality_notes": [],
    }
    if offline:
        out["id_type"] = "name_only"
        out["confidence"] = 0.30
        out["data_quality_notes"].append("offline mode — values illustrative")
        return out

    # accept a CIK pattern directly
    raw = acquirer_name.strip()
    if re.fullmatch(r"\d{1,10}", raw):
        out["cik"] = _pad_cik(raw)
        out["id_type"] = "cik"
        out["confidence"] = 0.95
        return out

    # EFTS lookup filtered by acquirer-side forms
    forms = ",".join(ACQUIRER_FORM_TYPES[:5])  # most useful prefix
    url = (
        "https://efts.sec.gov/LATEST/search-index?"
        + urllib.parse.urlencode({"q": '"' + raw + '"', "forms": forms})
    )
    body, status, err = _http_get(url)
    if body and status == 200:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            hits = payload.get("hits", {}).get("hits", [])
            cik_counts = {}
            cik_names = {}
            for h in hits:
                source = h.get("_source", {})
                ciks = source.get("ciks", []) or []
                names = source.get("display_names", []) or []
                for cik in ciks:
                    cik_counts[cik] = cik_counts.get(cik, 0) + 1
                    cik_names.setdefault(cik, set()).update(names)
            if cik_counts:
                # pick CIK with most filings AND name match
                ranked = sorted(cik_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                norm_target = re.sub(r"[^a-z0-9]+", "", raw.lower())
                best_cik = None
                for cik, _ in ranked:
                    names = cik_names.get(cik, set())
                    name_hits = [n for n in names if norm_target in re.sub(r"[^a-z0-9]+", "", n.lower())]
                    if name_hits:
                        best_cik = cik
                        out["aliases"] = sorted(set(names))[:8]
                        break
                if not best_cik:
                    best_cik = ranked[0][0]
                    out["aliases"] = sorted(cik_names.get(best_cik, set()))[:8]
                    out["confidence"] = 0.70
                else:
                    out["confidence"] = 0.90
                out["cik"] = _pad_cik(best_cik)
                out["id_type"] = "cik"
                return out
        except Exception as e:
            out["data_quality_notes"].append(f"efts_parse_failed: {e}")

    # browse-edgar HTML fallback
    url2 = (
        "https://www.sec.gov/cgi-bin/browse-edgar?"
        + urllib.parse.urlencode({
            "action": "getcompany",
            "company": raw,
            "type": "",
            "dateb": "",
            "owner": "include",
            "count": 40,
        })
    )
    body2, status2, err2 = _http_get(url2)
    if body2 and status2 == 200:
        text = body2.decode("utf-8", errors="replace")
        # crude parse of CIK column
        m = re.findall(r"CIK=(\d{6,10})", text)
        if m:
            out["cik"] = _pad_cik(m[0])
            out["id_type"] = "cik"
            out["confidence"] = 0.55
            return out

    # name-only foreign-filer fallback
    out["id_type"] = "name_only"
    out["confidence"] = 0.50
    out["data_quality_notes"].append(
        "no EDGAR CIK resolved — proceeding via international regulator name-match path"
    )
    return out


def pull_acquirer_filings(cik: str, *, lookback_years: int = 10) -> List[Dict]:
    """Pull all M&A-relevant filings by CIK from the EDGAR submissions API.

    Returns a list of filing records. Each record has:
        accession, form, filing_date, primary_doc_url, primary_doc_description.
    Does NOT classify acquirer-vs-target — that is the orchestrator's job.
    """
    cik_padded = _pad_cik(cik)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    body, status, err = _http_get(url)
    if not body or status != 200:
        return []
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return []

    cutoff_year_prefix = None
    try:
        from datetime import datetime
        cutoff = datetime.utcnow().replace(year=datetime.utcnow().year - lookback_years)
        cutoff_iso = cutoff.strftime("%Y-%m-%d")
    except Exception:
        cutoff_iso = "1900-01-01"

    out: List[Dict] = []

    def _consume(filings_block):
        forms = filings_block.get("form", [])
        accessions = filings_block.get("accessionNumber", [])
        dates = filings_block.get("filingDate", [])
        primary_docs = filings_block.get("primaryDocument", [])
        primary_descs = filings_block.get("primaryDocDescription", [])
        for i in range(min(len(forms), len(accessions), len(dates))):
            form = forms[i]
            if form not in ACQUIRER_FORM_TYPES:
                continue
            d = dates[i]
            if d < cutoff_iso:
                continue
            accession = accessions[i]
            accession_nodash = accession.replace("-", "")
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            primary_desc = primary_descs[i] if i < len(primary_descs) else ""
            url_pdoc = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik_padded)}/"
                f"{accession_nodash}/{primary_doc}"
                if primary_doc
                else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_padded}&type=&dateb=&owner=include&count=40"
            )
            out.append({
                "form": form,
                "accession": accession,
                "filing_date": d,
                "primary_doc_url": url_pdoc,
                "primary_doc_description": primary_desc,
            })

    _consume(payload.get("filings", {}).get("recent", {}))

    # continuation files
    for ent in payload.get("filings", {}).get("files", []) or []:
        ext_url = f"https://data.sec.gov/submissions/{ent.get('name')}"
        body2, status2, _ = _http_get(ext_url)
        if body2 and status2 == 200:
            try:
                _consume(json.loads(body2.decode("utf-8", errors="replace")))
            except Exception:
                continue

    return out


def offline_illustrative_filings(acquirer_name: str) -> List[Dict]:
    """Illustrative filings used by smoke tests when network is unavailable.

    Hand-curated to match the worked example in P4 SKILL.md so the smoke
    test produces deterministic, plausible numbers.
    """
    if "bawag" in acquirer_name.lower():
        return [
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2024-DPB",
                "filing_date": "2024-06-15",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2024-06-15-Offer.html",
                "primary_doc_description": "BAWAG offer for Deutsche Pfandbriefbank AG (illustrative)",
            },
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2023-KNAB",
                "filing_date": "2023-03-08",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2023-03-08-Knab.html",
                "primary_doc_description": "BAWAG offer for Knab (Dutch online bank, illustrative)",
            },
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2022-RBS",
                "filing_date": "2022-08-22",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2022-08-22-Raiffeisen.html",
                "primary_doc_description": "BAWAG offer for Raiffeisen Bausparkasse (illustrative)",
            },
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2021-HELLOBANK",
                "filing_date": "2021-11-10",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2021-11-10-Hellobank.html",
                "primary_doc_description": "BAWAG offer for Hello bank! Austria (illustrative)",
            },
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2020-SUDWEST",
                "filing_date": "2020-06-01",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2020-06-01-Sudwest.html",
                "primary_doc_description": "BAWAG offer for Südwestbank (illustrative DE add-on)",
            },
            {
                "form": "OFFER_DOCUMENT",
                "accession": "ILLUSTRATIVE-2019-SIRIO",
                "filing_date": "2019-04-04",
                "primary_doc_url": "https://www.bawaggroup.com/EN/IR/Press-releases/2019-04-04-Sirio.html",
                "primary_doc_description": "BAWAG offer for Sirio (illustrative IT consumer-finance carve-out)",
            },
        ]
    return []


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--acquirer", required=True)
    p.add_argument("--lookback-years", type=int, default=10)
    p.add_argument("--offline", action="store_true")
    args = p.parse_args()

    info = resolve_acquirer(args.acquirer, offline=args.offline)
    filings = []
    if args.offline:
        filings = offline_illustrative_filings(args.acquirer)
    elif info.get("cik"):
        filings = pull_acquirer_filings(info["cik"], lookback_years=args.lookback_years)
    print(json.dumps({"resolution": info, "filings_count": len(filings), "filings": filings[:5]}, indent=2))
