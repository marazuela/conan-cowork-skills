"""adcom_class_history.py — Federal Register API wrapper for AdCom history (P2).

Queries https://www.federalregister.gov/api/v1/documents.json for FDA Advisory
Committee meeting notices. Used to compute the AdCom convene rate for a given
class / division across the lookback window.

The Federal Register API supports a faceted search:
  - conditions[term]: free-text query (e.g. "psychopharmacologic drugs advisory committee")
  - conditions[publication_date][gte] / [lte]: ISO dates
  - conditions[type][]: "Notice", "Rule", etc. — AdCom announcements are notices
  - per_page: up to 1000

Returns a dict:
    {"ok": bool, "results": [...], "n": int, "source_url": str, "reason": "..."}

Each result entry:
    {
      "publication_date": "2024-01-15",
      "title": "...",
      "html_url": "https://www.federalregister.gov/documents/...",
      "drug_or_topic": "...",  # parsed from title
      "division": "...",
      "source": "<api_url>",
      "confidence": 0.85
    }
"""

from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

FR_BASE = "https://www.federalregister.gov/api/v1/documents.json"
USER_AGENT = "investment-tool-research-clinical-class-precedent/1.0 (skill-build)"
DEFAULT_TIMEOUT = 12.0
MAX_RETRIES = 3

# Map division name → search term and year-base AdCom rate fallback (industry estimate)
DIVISION_TERMS: Dict[str, Tuple[str, float]] = {
    "psychiatry": ("psychopharmacologic drugs advisory committee", 0.18),
    "neurology": ("peripheral and central nervous system drugs advisory committee", 0.20),
    "oncology": ("oncologic drugs advisory committee", 0.30),
    "cardiology": ("cardiovascular and renal drugs advisory committee", 0.25),
    "endocrinology": ("endocrinologic and metabolic drugs advisory committee", 0.20),
    "pulmonary": ("pulmonary-allergy drugs advisory committee", 0.15),
    "rheumatology": ("arthritis advisory committee", 0.15),
    "anti-infective": ("antimicrobial drugs advisory committee", 0.20),
    "ophthalmology": ("dermatologic and ophthalmic drugs advisory committee", 0.10),
    "gastroenterology": ("gastrointestinal drugs advisory committee", 0.15),
    "general": ("advisory committee", 0.12),  # fallback
}


def _http_get_json(url: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status >= 400:
                    last_err = f"HTTP {resp.status}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                return True, json.loads(resp.read().decode("utf-8")), ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTPError {e.code}"
            if e.code == 429:
                time.sleep(min(2 ** (attempt + 2), 30))
                continue
            time.sleep(min(2 ** attempt, 8))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8))
        except Exception as e:  # pragma: no cover
            last_err = f"unexpected: {type(e).__name__}: {e}"
            break
    return False, None, last_err


def _build_url(term: str, gte: str, lte: str, per_page: int = 100) -> str:
    params = [
        ("conditions[term]", term),
        ("conditions[publication_date][gte]", gte),
        ("conditions[publication_date][lte]", lte),
        ("conditions[type][]", "Notice"),
        ("per_page", str(per_page)),
        ("order", "newest"),
    ]
    return f"{FR_BASE}?{urllib.parse.urlencode(params)}"


def division_for_indication(indication: str) -> str:
    """Best-effort map indication → FDA review division."""
    if not indication:
        return "general"
    n = indication.lower()
    if any(k in n for k in ["depress", "schizo", "bipolar", "anxiety", "psychiatr", "mood", "ptsd", "ocd", "adhd"]):
        return "psychiatry"
    if any(k in n for k in ["alzheim", "parkinson", "epilepsy", "migraine", "neurolog", "ms ", "multiple sclerosis", "stroke", "tbi"]):
        return "neurology"
    if any(k in n for k in ["cancer", "tumor", "leukem", "lymphom", "myelom", "sarcom", "carcinom", "oncolog"]):
        return "oncology"
    if any(k in n for k in ["heart", "cardio", "hypertens", "atrial", "thrombo", "anticoag", "bleed"]):
        return "cardiology"
    if any(k in n for k in ["diabet", "obesity", "thyroid", "endocrin", "glp", "insulin", "weight"]):
        return "endocrinology"
    if any(k in n for k in ["asthma", "copd", "pulmonary", "respirat"]):
        return "pulmonary"
    if any(k in n for k in ["rheumat", "arthrit", "lupus", "psoria"]):
        return "rheumatology"
    if any(k in n for k in ["infection", "antibiot", "antifung", "antiviral", "hiv ", "hepatit", "tuberc"]):
        return "anti-infective"
    if any(k in n for k in ["eye", "macular", "amd", "dme", "thyroid eye", "glaucom", "retinop"]):
        return "ophthalmology"
    if any(k in n for k in ["crohn", "colit", "iga nephropathy", "ibd", "gastro", "celiac", "fsgs", "renal", "kidney"]):
        return "gastroenterology"
    return "general"


def search_adcom_class_history(
    class_label: str,
    indication: str = "",
    lookback_years: int = 10,
) -> Dict[str, Any]:
    """Pull AdCom notices for the relevant division across the lookback window."""
    division = division_for_indication(indication)
    term, fallback_rate = DIVISION_TERMS.get(division, DIVISION_TERMS["general"])
    today = _dt.datetime.utcnow().date()
    gte = (today - _dt.timedelta(days=int(lookback_years) * 365)).isoformat()
    lte = today.isoformat()
    url = _build_url(term, gte, lte)
    ok, payload, err = _http_get_json(url)
    if not ok:
        return {
            "ok": False,
            "results": [],
            "n": 0,
            "source_url": url,
            "reason": err,
            "division": division,
            "fallback_rate": fallback_rate,
        }
    docs = (payload or {}).get("results", []) or []
    results: List[Dict[str, Any]] = []
    for d in docs:
        title = d.get("title") or ""
        results.append({
            "publication_date": d.get("publication_date"),
            "title": title,
            "html_url": d.get("html_url"),
            "abstract": (d.get("abstract") or "")[:300],
            "division": division,
            "source": url,
            "confidence": 0.85,
        })
    return {
        "ok": True,
        "results": results,
        "n": len(results),
        "source_url": url,
        "reason": "",
        "division": division,
        "fallback_rate": fallback_rate,
    }


def estimate_adcom_rate(
    adcom_results: List[Dict[str, Any]],
    n_class_approvals: int,
    division_fallback: float,
) -> Tuple[float, float, str]:
    """Heuristic AdCom rate.

    AdCom *convene* rate is hard to compute purely from Federal Register because
    not every AdCom notice corresponds 1:1 with a class drug review. We use:

      rate = min(1.0, n_class_relevant_adcoms / max(1, n_class_decisions))

    Where n_class_relevant_adcoms is the count of AdCom notices in the division
    over the lookback window. This conflates per-drug AdCom and per-topic AdCom,
    so confidence is moderate.
    """
    n_adcom = len(adcom_results or [])
    n_decisions = max(1, n_class_approvals)
    if n_adcom == 0 or n_class_approvals == 0:
        return division_fallback, 0.45, "fallback_industry_rate (n=0 in primary)"
    rate = min(1.0, n_adcom / n_decisions)
    # Sanity-clamp: AdCom rate above 0.5 for non-oncology is suspicious
    if rate > 0.5:
        rate = (rate + division_fallback) / 2
        return rate, 0.55, "clamped_to_division_fallback (high primary rate)"
    return rate, 0.70, "primary"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--class-label", required=True)
    p.add_argument("--indication", default="")
    p.add_argument("--lookback-years", type=int, default=10)
    args = p.parse_args()
    out = search_adcom_class_history(args.class_label, args.indication, args.lookback_years)
    print(json.dumps(out, indent=2, default=str))
