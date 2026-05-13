"""edgar_filer_history.py — EDGAR EFTS + submissions-API wrapper for P3.

Resolves filer name → CIK and pulls all SC 13D / SC 13D/A filings by that
CIK within the lookback window. Best-effort parses each primary document
for percent-of-class, reporting persons, and Item 4 purpose text.

Network and rate-limit handling: 3-attempt exponential backoff, 12s
timeout. On HTTP 429, returns {"ok": False, "_status": "edgar_rate_limited"}.

The skill obeys SEC EDGAR fair-access policy via a descriptive User-Agent.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
BROWSE_EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar"
USER_AGENT = "investment-tool-research-activist-filer/1.0 javiergorordo13@hotmail.com"
DEFAULT_TIMEOUT = 12.0
MAX_RETRIES = 3


def _http_get(url: str, accept: str = "application/json") -> Tuple[bool, Optional[bytes], str, int]:
    last_err = ""
    last_status = 0
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": accept, "Host": urllib.parse.urlparse(url).netloc},
            )
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                last_status = resp.status
                if resp.status == 429:
                    last_err = "HTTP 429 rate_limited"
                    time.sleep(min(2 ** (attempt + 2), 30))
                    continue
                if resp.status >= 400:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                return True, resp.read(), "", resp.status
        except urllib.error.HTTPError as e:
            last_status = e.code
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
    return False, None, last_err, last_status


def _http_get_json(url: str) -> Tuple[bool, Optional[Dict[str, Any]], str, int]:
    ok, body, err, status = _http_get(url, accept="application/json")
    if not ok or body is None:
        return False, None, err, status
    try:
        return True, json.loads(body.decode("utf-8")), "", status
    except json.JSONDecodeError as e:
        return False, None, f"JSONDecodeError: {e}", status


def _normalize_cik(cik: str) -> str:
    return str(cik).strip().lstrip("0") or "0"


def _pad_cik(cik: str) -> str:
    return str(cik).strip().lstrip("0").zfill(10) if str(cik).strip() else "0000000000"


def _is_cik_input(s: str) -> bool:
    """True if input looks like a CIK (digits, optionally zero-padded)."""
    s = s.strip()
    if not s:
        return False
    return bool(re.fullmatch(r"\d{1,10}", s.lstrip("0") or "0"))


def resolve_filer_cik(filer_name_or_cik: str) -> Dict[str, Any]:
    """Resolve filer input to a CIK with confidence. See SKILL.md Step 1."""
    s = filer_name_or_cik.strip()
    if _is_cik_input(s):
        return {
            "ok": True,
            "cik": _normalize_cik(s),
            "padded": _pad_cik(s),
            "matched_name": None,
            "aliases": [],
            "confidence": 0.95,
            "method": "direct_cik_input",
            "source": "input",
        }

    # EFTS lookup: search for SC 13D filings whose display_names contains the filer
    # NOTE: EFTS search puts the *issuer* CIK in `ciks` for 13D filings. The
    # *filer* CIK is in entity-related fields. To pull the filer's CIK robustly,
    # we use the dedicated company-search endpoint.
    enc_name = urllib.parse.quote(s)
    browse_url = f"{BROWSE_EDGAR}?action=getcompany&company={enc_name}&type=SC+13D&dateb=&owner=include&count=40"
    ok, body, err, status = _http_get(browse_url, accept="text/html")
    aliases = [s]
    candidates: List[Dict[str, Any]] = []
    if ok and body is not None:
        html = body.decode("utf-8", errors="ignore")
        # Parse rows of the format:
        # <a href="/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281...">0001539281</a>
        # <td ...>FORAGER FUND, L.P.</td>
        for m in re.finditer(
            r"CIK=(\d{10}).*?>\s*\d{10}\s*</a>.*?<td[^>]*>([^<]+)</td>",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            cik_p = m.group(1)
            name_p = re.sub(r"\s+", " ", m.group(2)).strip()
            candidates.append({"cik": _normalize_cik(cik_p), "padded": cik_p, "name": name_p})

    # Filter candidates by name similarity (case-insensitive substring)
    s_norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip().lower()
    matched: List[Dict[str, Any]] = []
    for c in candidates:
        c_norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", c["name"])).strip().lower()
        if s_norm and (s_norm in c_norm or c_norm in s_norm):
            matched.append(c)
    if not matched and candidates:
        # No name overlap — return top candidate at low confidence
        matched = candidates[:1]

    if not matched:
        return {
            "ok": False,
            "cik": "",
            "padded": "",
            "matched_name": None,
            "aliases": aliases,
            "confidence": 0.0,
            "method": "edgar_browse_fallback",
            "source": browse_url,
            "reason": err or "no matching CIK found",
            "_status": "no_cik_resolved",
        }

    chosen = matched[0]
    confidence = 0.85 if len(matched) == 1 else 0.70
    if not ok:
        confidence = min(confidence, 0.55)
    return {
        "ok": True,
        "cik": chosen["cik"],
        "padded": chosen["padded"],
        "matched_name": chosen["name"],
        "aliases": list({s, chosen["name"]}),
        "confidence": confidence,
        "method": "edgar_browse_match",
        "source": browse_url,
        "all_candidates": matched,
    }


def list_13d_filings(cik: str, lookback_years: int = 15) -> Dict[str, Any]:
    """Pull all SC 13D / SC 13D/A filings for the filer CIK from the EDGAR
    submissions API. Returns a structured list with filing metadata.
    """
    padded = _pad_cik(cik)
    url = f"{SUBMISSIONS_BASE}/CIK{padded}.json"
    ok, payload, err, status = _http_get_json(url)
    if not ok:
        return {"ok": False, "filings": [], "url": url, "reason": err, "_status": "edgar_submissions_unavailable"}

    out: List[Dict[str, Any]] = []
    recent = ((payload or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    primary_descs = recent.get("primaryDocDescription", [])

    n = min(len(forms), len(accs), len(dates))
    for i in range(n):
        form = forms[i]
        if form not in ("SC 13D", "SC 13D/A"):
            continue
        out.append({
            "form": form,
            "accession_no": accs[i],
            "filing_date": dates[i],
            "primary_document": primary_docs[i] if i < len(primary_docs) else None,
            "primary_description": primary_descs[i] if i < len(primary_descs) else None,
            "filer_cik": _normalize_cik(cik),
            "url_index": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded}&type=SC+13D&dateb=&owner=include&count=40",
        })

    # Look at continuation files for older history (>~15 years)
    files_meta = ((payload or {}).get("filings") or {}).get("files") or []
    for fm in files_meta:
        cont_url = f"{SUBMISSIONS_BASE}/{fm.get('name')}"
        ok2, payload2, err2, _ = _http_get_json(cont_url)
        if not ok2 or payload2 is None:
            continue
        forms2 = payload2.get("form", [])
        accs2 = payload2.get("accessionNumber", [])
        dates2 = payload2.get("filingDate", [])
        prim2 = payload2.get("primaryDocument", [])
        n2 = min(len(forms2), len(accs2), len(dates2))
        for i in range(n2):
            if forms2[i] in ("SC 13D", "SC 13D/A"):
                out.append({
                    "form": forms2[i],
                    "accession_no": accs2[i],
                    "filing_date": dates2[i],
                    "primary_document": prim2[i] if i < len(prim2) else None,
                    "filer_cik": _normalize_cik(cik),
                    "url_index": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded}&type=SC+13D&dateb=&owner=include&count=40",
                })

    # Filter by lookback window
    if lookback_years and out:
        cutoff = int(_year_floor(lookback_years))
        out = [r for r in out if _filing_year(r["filing_date"]) >= cutoff]

    return {"ok": True, "filings": out, "url": url, "n": len(out)}


def _filing_year(date_str: str) -> int:
    try:
        return int(str(date_str).split("-")[0])
    except Exception:
        return 0


def _year_floor(lookback_years: int) -> int:
    import datetime as dt
    return dt.datetime.utcnow().year - int(lookback_years)


def fetch_primary_doc(filer_cik: str, accession_no: str, primary_document: Optional[str]) -> Dict[str, Any]:
    """Fetch the 13D primary document and parse for issuer + position percent."""
    padded = _pad_cik(filer_cik)
    acc_clean = (accession_no or "").replace("-", "")
    if not acc_clean:
        return {"ok": False, "reason": "no_accession"}
    base = f"https://www.sec.gov/Archives/edgar/data/{_normalize_cik(filer_cik)}/{acc_clean}"
    candidates: List[str] = []
    if primary_document:
        candidates.append(f"{base}/{primary_document}")
    candidates.append(f"{base}/primary_doc.xml")
    candidates.append(f"{base}/{accession_no}-index.htm")

    for url in candidates:
        ok, body, err, status = _http_get(url, accept="application/xml,text/html,text/plain;q=0.5")
        if ok and body:
            text = body.decode("utf-8", errors="ignore")
            return {
                "ok": True,
                "url": url,
                "raw": text[:80000],  # cap to ~80KB to keep memory bounded
                "parsed": _parse_13d_doc(text),
            }
    return {"ok": False, "reason": "no_doc_found", "tried": candidates}


def _parse_13d_doc(text: str) -> Dict[str, Any]:
    """Best-effort parse of a 13D primary doc.

    Tier-1: XML schedule (`<edgarSubmission>`)
    Tier-2: HTML tables / regex
    Tier-3: plain-text regex
    """
    out: Dict[str, Any] = {
        "subject_company": None,
        "subject_cik": None,
        "reporting_persons": [],
        "percent_of_class": None,
        "aggregate_amount": None,
        "item_four_text": None,
        "parse_confidence": 0.30,
        "parse_method": "unparseable",
    }

    # XML schedule
    if "<edgarSubmission" in text or "<schedule13D" in text or "<schedule13DA" in text:
        m_sub = re.search(r"<subjectCompany[^>]*>([\s\S]*?)</subjectCompany>", text)
        if m_sub:
            block = m_sub.group(1)
            mn = re.search(r"<companyConformedName>([^<]+)</companyConformedName>", block)
            mc = re.search(r"<cikNumber>(\d+)</cikNumber>", block)
            if mn:
                out["subject_company"] = mn.group(1).strip()
            if mc:
                out["subject_cik"] = mc.group(1).strip().lstrip("0") or "0"
        # Reporting owners
        for m_rep in re.finditer(r"<reportingPerson[^>]*>([\s\S]*?)</reportingPerson>", text):
            block = m_rep.group(1)
            mn = re.search(r"<rptOwnerName>([^<]+)</rptOwnerName>", block) or \
                 re.search(r"<reportingPersonName>([^<]+)</reportingPersonName>", block)
            if mn:
                out["reporting_persons"].append(mn.group(1).strip())
        m_pct = re.search(r"<percentOfClass>([\d\.]+)</percentOfClass>", text)
        if m_pct:
            try:
                out["percent_of_class"] = float(m_pct.group(1))
            except ValueError:
                pass
        m_agg = re.search(r"<aggregateAmount>([\d,]+)</aggregateAmount>", text)
        if m_agg:
            try:
                out["aggregate_amount"] = int(m_agg.group(1).replace(",", ""))
            except ValueError:
                pass
        m_item4 = re.search(r"<itemFourPurpose[^>]*>([\s\S]*?)</itemFourPurpose>", text)
        if m_item4:
            out["item_four_text"] = re.sub(r"<[^>]+>", " ", m_item4.group(1))[:4000].strip()
        out["parse_method"] = "xml_schedule"
        out["parse_confidence"] = 0.92
        return out

    # HTML / plain text fallback
    txt = re.sub(r"<[^>]+>", " ", text)
    txt = re.sub(r"\s+", " ", txt)

    # Subject company: look for "RE: <NAME>" or first capitalized issuer reference
    m_sub = re.search(r"(?:Subject Company|SUBJECT COMPANY|Issuer)\s*[:\-]?\s*([A-Z][A-Za-z0-9 ,.&\-]{4,80})", txt)
    if m_sub:
        out["subject_company"] = m_sub.group(1).strip(" ,.-")

    # Percent-of-class
    m_pct = re.search(r"(?:Percent of Class[^:]*:?|Percent of Class Represented[^:]*:?)\s*([\d]+(?:\.\d+)?)\s*%", txt)
    if not m_pct:
        m_pct = re.search(r"\b([\d]+(?:\.\d+)?)\s*%\s*of\s+(?:the\s+)?(?:class|outstanding|common\s+shares)", txt, flags=re.IGNORECASE)
    if m_pct:
        try:
            out["percent_of_class"] = float(m_pct.group(1))
        except ValueError:
            pass

    # Aggregate amount
    m_agg = re.search(r"(?:Aggregate Amount Beneficially Owned[^:]*:?|Aggregate amount of)\s*([\d,]+)", txt)
    if m_agg:
        try:
            out["aggregate_amount"] = int(m_agg.group(1).replace(",", ""))
        except ValueError:
            pass

    # Item 4 text
    m_item4 = re.search(r"Item\s*4[\.\s\-:]*Purpose of Transaction([\s\S]{50,4000}?)Item\s*5", txt, flags=re.IGNORECASE)
    if m_item4:
        out["item_four_text"] = m_item4.group(1).strip()[:4000]

    # Reporting persons (heuristic)
    for m_rp in re.finditer(r"(?:NAME OF REPORTING PERSON|Name of Reporting Person)[:\s]+([A-Z][A-Za-z0-9 ,.&\-/]{4,80})", txt):
        out["reporting_persons"].append(m_rp.group(1).strip(" ,.-"))

    if any([out["percent_of_class"], out["item_four_text"], out["subject_company"]]):
        out["parse_method"] = "html_text_fallback"
        out["parse_confidence"] = 0.65
    else:
        out["parse_method"] = "unparseable"
        out["parse_confidence"] = 0.30

    return out


def detect_target_cik_from_index(filer_cik: str, accession_no: str) -> Optional[str]:
    """Fetch the filing index page and extract the *subject company* (issuer) CIK.

    For 13D filings the EDGAR index page lists both the filer and the subject
    company; the subject company CIK is what we need to group by target.
    """
    padded = _pad_cik(filer_cik)
    acc_clean = (accession_no or "").replace("-", "")
    if not acc_clean:
        return None
    url = f"https://www.sec.gov/Archives/edgar/data/{_normalize_cik(filer_cik)}/{acc_clean}/{accession_no}-index.htm"
    ok, body, err, status = _http_get(url, accept="text/html")
    if not ok or not body:
        return None
    html = body.decode("utf-8", errors="ignore")
    # Look for "Subject Company" block, then a CIK link
    m_subj = re.search(
        r"Subject\s*Company[\s\S]{0,400}?CIK=(\d{10})",
        html,
        flags=re.IGNORECASE,
    )
    if m_subj:
        return _normalize_cik(m_subj.group(1))
    return None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--filer", required=True)
    p.add_argument("--lookback-years", type=int, default=15)
    args = p.parse_args()

    res = resolve_filer_cik(args.filer)
    print("=== resolve_filer_cik ===")
    print(json.dumps(res, indent=2, default=str))
    if not res.get("ok"):
        raise SystemExit(2)
    fl = list_13d_filings(res["cik"], args.lookback_years)
    print("=== list_13d_filings ===")
    print(json.dumps({"ok": fl["ok"], "n": fl.get("n", 0), "head": fl["filings"][:3]}, indent=2, default=str))
