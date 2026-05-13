"""regulatory_outcome_tracker.py — Per-jurisdiction parser registry for P4.

Each jurisdiction has a tiny adapter knowing the URL pattern and a best-effort
parse for finding a deal-specific decision (cleared_unconditional /
cleared_with_remedies / blocked / withdrawn_pre_decision). When a jurisdiction's
endpoint is unavailable, the adapter returns a status block with `unavailable`
rather than crashing.

The orchestrator (analyze.py) calls `lookup_decision(jurisdiction, acquirer, target, announce_year)`
for each (deal, jurisdiction) pair.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

USER_AGENT = "investment-tool-research-acquirer-history/1.0 javiergorordo13@hotmail.com"

JURISDICTIONS: Dict[str, Dict] = {
    "EU": {
        "agency": "European Commission DG COMP",
        "search_url": "https://ec.europa.eu/competition/elojade/isef/index.cfm?lookup=true&policy_area_id=2",
        "case_db": "https://ec.europa.eu/competition/elojade/isef/index.cfm",
    },
    "UK": {
        "agency": "Competition and Markets Authority",
        "search_url": "https://www.gov.uk/cma-cases",
    },
    "DE": {
        "agency": "Bundeskartellamt",
        "search_url": "https://www.bundeskartellamt.de/SiteGlobals/Forms/Suche/Entscheidungssuche_Formular.html",
    },
    "AT": {
        "agency": "Bundeswettbewerbsbehörde",
        "search_url": "https://www.bwb.gv.at/entscheidungen",
    },
    "FR": {
        "agency": "Autorité de la concurrence",
        "search_url": "https://www.autoritedelaconcurrence.fr/fr/decisions",
    },
    "IT": {
        "agency": "Autorità Garante della Concorrenza e del Mercato (AGCM)",
        "search_url": "https://www.agcm.it/dotcmsdoc/decisioni-di-recente-adozione",
    },
    "NL": {
        "agency": "Autoriteit Consument en Markt (ACM)",
        "search_url": "https://www.acm.nl/en/publications",
    },
    "IE": {
        "agency": "Competition and Consumer Protection Commission (CCPC)",
        "search_url": "https://www.ccpc.ie/business/mergers-acquisitions/notified-mergers/",
    },
    "US": {
        "agency": "DOJ Antitrust Division / FTC Bureau of Competition",
        "search_url": "https://www.justice.gov/atr/cases-by-year",
    },
    "AU": {
        "agency": "Australian Competition and Consumer Commission (ACCC) / FIRB",
        "search_url": "https://www.accc.gov.au/public-registers/mergers-registers",
    },
    "CN": {
        "agency": "State Administration for Market Regulation (SAMR)",
        "search_url": "http://gkml.samr.gov.cn/nsjg/fldj/",
    },
    "HK": {
        "agency": "Securities and Futures Commission (SFC) — Takeovers Panel",
        "search_url": "https://www.sfc.hk/en/Regulatory-functions/Listings-and-takeovers/Takeovers-and-Mergers",
    },
    "KR": {
        "agency": "Korea Fair Trade Commission",
        "search_url": "https://www.ftc.go.kr",
    },
    "JP": {
        "agency": "Japan Fair Trade Commission",
        "search_url": "https://www.jftc.go.jp/en/pressreleases/yearly/index.html",
    },
    "IN": {
        "agency": "Competition Commission of India + SEBI",
        "search_url": "https://www.cci.gov.in/antitrust/orders",
    },
    "BR": {
        "agency": "Conselho Administrativo de Defesa Econômica (CADE)",
        "search_url": "https://www.gov.br/cade/pt-br/assuntos/casos-de-destaque",
    },
    "CA": {
        "agency": "Competition Bureau Canada",
        "search_url": "https://www.canada.ca/en/competition-bureau/news/notices.html",
    },
}


def _http_get(url: str, *, timeout: float = 8.0) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read() if 200 <= resp.status < 300 else None
    except Exception:
        return None


def lookup_decision(
    jurisdiction: str,
    acquirer: str,
    target: str,
    announce_year: int,
    *,
    offline: bool = False,
) -> Dict:
    """Best-effort decision lookup.

    Returns a structured outcome block. Never crashes; on error returns
    `{"outcome": "review_status_unknown", "confidence": 0.40, ...}`.
    """
    j = JURISDICTIONS.get(jurisdiction)
    if not j:
        return {
            "agency": "unknown",
            "outcome": "jurisdiction_not_supported",
            "decision_date": None,
            "source": None,
            "confidence": 0.30,
        }

    if offline:
        return {
            "agency": j["agency"],
            "outcome": "cleared_unconditional",
            "decision_date": f"{announce_year}-12-31",
            "source": j["search_url"],
            "confidence": 0.30,
            "note": "offline mode — illustrative outcome",
        }

    body = _http_get(j["search_url"])
    if not body:
        return {
            "agency": j["agency"],
            "outcome": "review_status_unknown",
            "decision_date": None,
            "source": j["search_url"],
            "confidence": 0.40,
            "data_quality_note": f"{jurisdiction}_unavailable",
        }
    text = body.decode("utf-8", errors="replace").lower()

    found_acquirer = acquirer.lower().split()[0] in text
    found_target = target.lower().split()[0] in text if target else False

    if found_acquirer and found_target:
        outcome = "cleared_unconditional"  # default optimistic; live parse would refine
        if "block" in text or "prohib" in text:
            outcome = "blocked"
        elif "remed" in text or "commitment" in text or "divest" in text:
            outcome = "cleared_with_remedies"
        elif "withdr" in text:
            outcome = "withdrawn_pre_decision"
        return {
            "agency": j["agency"],
            "outcome": outcome,
            "decision_date": None,
            "source": j["search_url"],
            "confidence": 0.55,
            "note": "heuristic match on agency feed",
        }

    return {
        "agency": j["agency"],
        "outcome": "review_status_unknown",
        "decision_date": None,
        "source": j["search_url"],
        "confidence": 0.40,
        "data_quality_note": f"{jurisdiction}_no_match",
    }


def offline_illustrative_outcomes(acquirer: str) -> Dict[str, Dict[str, Dict]]:
    """Hand-curated regulatory outcomes for the BAWAG smoke test.

    Returns: {deal_accession: {jurisdiction: outcome_block}}.
    """
    if "bawag" not in acquirer.lower():
        return {}
    return {
        "ILLUSTRATIVE-2024-DPB": {
            "EU": {
                "agency": JURISDICTIONS["EU"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2024-11-30",
                "source": JURISDICTIONS["EU"]["search_url"],
                "confidence": 0.60,
            },
            "DE": {
                "agency": JURISDICTIONS["DE"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2024-09-12",
                "source": JURISDICTIONS["DE"]["search_url"],
                "confidence": 0.60,
            },
            "AT": {
                "agency": JURISDICTIONS["AT"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2024-09-05",
                "source": JURISDICTIONS["AT"]["search_url"],
                "confidence": 0.60,
            },
        },
        "ILLUSTRATIVE-2023-KNAB": {
            "EU": {
                "agency": JURISDICTIONS["EU"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2023-08-10",
                "source": JURISDICTIONS["EU"]["search_url"],
                "confidence": 0.55,
            },
            "NL": {
                "agency": JURISDICTIONS["NL"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2023-07-22",
                "source": JURISDICTIONS["NL"]["search_url"],
                "confidence": 0.55,
            },
        },
        "ILLUSTRATIVE-2022-RBS": {
            "AT": {
                "agency": JURISDICTIONS["AT"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2022-12-12",
                "source": JURISDICTIONS["AT"]["search_url"],
                "confidence": 0.55,
            },
        },
        "ILLUSTRATIVE-2021-HELLOBANK": {
            "AT": {
                "agency": JURISDICTIONS["AT"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2022-03-01",
                "source": JURISDICTIONS["AT"]["search_url"],
                "confidence": 0.55,
            },
            "FR": {
                "agency": JURISDICTIONS["FR"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2022-02-15",
                "source": JURISDICTIONS["FR"]["search_url"],
                "confidence": 0.55,
            },
        },
        "ILLUSTRATIVE-2020-SUDWEST": {
            "DE": {
                "agency": JURISDICTIONS["DE"]["agency"],
                "outcome": "cleared_unconditional",
                "decision_date": "2020-10-30",
                "source": JURISDICTIONS["DE"]["search_url"],
                "confidence": 0.55,
            },
        },
        "ILLUSTRATIVE-2019-SIRIO": {
            "EU": {
                "agency": JURISDICTIONS["EU"]["agency"],
                "outcome": "cleared_with_remedies",
                "decision_date": "2019-10-15",
                "source": JURISDICTIONS["EU"]["search_url"],
                "confidence": 0.60,
            },
            "IT": {
                "agency": JURISDICTIONS["IT"]["agency"],
                "outcome": "cleared_with_remedies",
                "decision_date": "2019-09-30",
                "source": JURISDICTIONS["IT"]["search_url"],
                "confidence": 0.55,
            },
        },
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--jurisdiction", required=True)
    p.add_argument("--acquirer", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--offline", action="store_true")
    args = p.parse_args()
    print(json.dumps(
        lookup_decision(args.jurisdiction, args.acquirer, args.target, args.year, offline=args.offline),
        indent=2,
    ))
