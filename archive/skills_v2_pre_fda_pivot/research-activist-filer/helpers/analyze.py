"""analyze.py — P3 (research-activist-filer) orchestrator.

Composes the JSON sidecar and markdown track-record report by:
  1. Resolving filer name -> CIK
  2. Listing the filer's SC 13D / SC 13D/A filings
  3. Parsing each primary document for issuer, position %, Item 4 text
  4. Grouping into campaigns by target CIK
  5. Resolving outcome per campaign
  6. Computing aggregate metrics + tier classification
  7. Atomic-writing outputs
  8. Emitting structured stdout JSON summary

Usage:
  python analyze.py --filer "Forager Fund, L.P." \
      --target-ticker RPAY --target-cik 1720592 \
      [--filer-cik 1539281] [--lookback-years 15] \
      [--offline] --output-dir <path>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

# Local imports (helpers in same dir)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from atomic_write import atomic_write_text  # noqa: E402
import edgar_filer_history as efh  # noqa: E402
import campaign_outcome_resolver as cor  # noqa: E402
import tier1_benchmark_data as t1b  # noqa: E402
from sic_sector_map import sic_to_sector  # noqa: E402

HALT_FLAG_PATH = "/sessions/great-sweet-darwin/mnt/Investment tool backup/02_System/engine/health/HALT_FLAG"


def now_iso() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def filer_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "unknown_filer"


def wilson_ci(successes: int, n: int, z: float = 1.96) -> List[float]:
    if n <= 0:
        return [0.0, 0.0]
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return [round(max(0.0, centre - spread), 3), round(min(1.0, centre + spread), 3)]


def detect_language_intensity(item_four_first: Optional[str], item_four_latest: Optional[str]) -> str:
    """Heuristic intensification score. Strong demand-words count delta."""
    if not item_four_first or not item_four_latest:
        return "unknown"
    demand_kws = re.compile(r"\b(require|must|demand|tender|remove|replace|insist|urge|reject|withdraw|terminate|nominate)\b", re.IGNORECASE)
    first_n = len(demand_kws.findall(item_four_first))
    latest_n = len(demand_kws.findall(item_four_latest))
    if latest_n > first_n + 1:
        return "increasing"
    if latest_n < first_n - 1:
        return "decreasing"
    return "flat"


def detect_board_nomination(item_four_text: str) -> bool:
    if not item_four_text:
        return False
    return bool(re.search(r"\b(nominate|nomination|director\s+slate|nominees|PRREN14A|DEFC14A|board\s+representation)\b", item_four_text, re.IGNORECASE))


def detect_strategic_review_demand(item_four_text: str) -> bool:
    if not item_four_text:
        return False
    return bool(re.search(r"\b(strategic\s+(?:alternatives|review|options)|explore\s+sale|sale\s+process|maximize\s+(?:shareholder\s+)?value)\b", item_four_text, re.IGNORECASE))


def offline_illustrative(filer_name: str, target_ticker: str, target_cik: str) -> Dict[str, Any]:
    """Worked-example illustrative defaults for offline smoke test.

    These match the SKILL.md worked example for Forager Fund / RPAY.
    """
    sidecar = {
        "filer_name": filer_name,
        "filer_cik": "1539281",
        "filer_aliases": [filer_name, "Forager Capital Management, LLC"],
        "as_of": now_iso(),
        "lookback_years": 15,
        "current_target": {
            "ticker": target_ticker,
            "cik": target_cik,
            "company_name": "Repay Holdings Corporation",
            "in_campaign_list": True,
        },
        "n_campaigns": 4,
        "n_distinct_targets": 4,
        "n_active": 1,
        "n_resolved": 3,
        "success_rate": 0.667,
        "success_rate_ci": [0.21, 0.94],
        "avg_position_pct": 9.1,
        "avg_holding_days": 370,
        "avg_amendments_per_campaign": 2.5,
        "sector_concentration": {"Technology": 2, "Consumer": 1, "Industrials": 1},
        "tier_classification": "emerging",
        "tier_benchmarks": {
            "tier_1_minimum_campaigns": t1b.TIER_1_MIN_CAMPAIGNS,
            "tier_1_minimum_success_rate": t1b.TIER_1_MIN_SUCCESS_RATE,
            "this_filer_n_campaigns": 4,
            "this_filer_success_rate": 0.667,
            "this_filer_vs_tier_1": "below_threshold",
        },
        "campaigns": [
            {
                "campaign_id": "forager_industrial_2018",
                "target_ticker": "_unknown",
                "target_cik": "0000000001",
                "target_company_name": "Industrial smallcap (illustrative)",
                "sector": "Industrials",
                "first_13d_date": "2018-06-12",
                "first_13d_accession": "0001539281-18-000003",
                "first_13d_position_pct": 8.2,
                "latest_13da_date": "2019-04-22",
                "latest_13da_accession": "0001539281-19-000007",
                "latest_position_pct": 11.5,
                "n_amendments": 3,
                "amendment_cadence_days": 90,
                "outcome_status": "sale_merger_closed",
                "outcome_event": "Definitive merger agreement executed; deal closed Q3 2019",
                "outcome_event_date": "2019-07-28",
                "holding_days_to_outcome": 411,
                "thesis_realized_return": None,
                "board_nomination_filing": False,
                "settlement_terms": None,
                "language_intensification": "flat",
                "source_first": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "source_latest": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "confidence": 0.55,
            },
            {
                "campaign_id": "forager_consumer_2021",
                "target_ticker": "_unknown",
                "target_cik": "0000000002",
                "target_company_name": "Consumer smallcap (illustrative)",
                "sector": "Consumer",
                "first_13d_date": "2021-02-04",
                "first_13d_accession": "0001539281-21-000002",
                "first_13d_position_pct": 7.0,
                "latest_13da_date": "2021-11-09",
                "latest_13da_accession": "0001539281-21-000018",
                "latest_position_pct": 4.6,
                "n_amendments": 2,
                "amendment_cadence_days": 140,
                "outcome_status": "withdrawal",
                "outcome_event": "Latest 13D/A position dropped to 4.6% (below 5% threshold)",
                "outcome_event_date": "2021-11-09",
                "holding_days_to_outcome": 278,
                "thesis_realized_return": None,
                "board_nomination_filing": False,
                "settlement_terms": None,
                "language_intensification": "decreasing",
                "source_first": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "source_latest": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "confidence": 0.50,
            },
            {
                "campaign_id": "forager_tech_2023",
                "target_ticker": "_unknown",
                "target_cik": "0000000003",
                "target_company_name": "Technology smallcap (illustrative)",
                "sector": "Technology",
                "first_13d_date": "2023-03-19",
                "first_13d_accession": "0001539281-23-000004",
                "first_13d_position_pct": 9.6,
                "latest_13da_date": "2024-05-12",
                "latest_13da_accession": "0001539281-24-000009",
                "latest_position_pct": 9.8,
                "n_amendments": 1,
                "amendment_cadence_days": 420,
                "outcome_status": "settlement_board_seats",
                "outcome_event": "Cooperation agreement; one Forager-aligned director appointed (illustrative)",
                "outcome_event_date": "2024-05-12",
                "holding_days_to_outcome": 420,
                "thesis_realized_return": None,
                "board_nomination_filing": True,
                "settlement_terms": "1 board seat + 12-month standstill",
                "language_intensification": "increasing",
                "source_first": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "source_latest": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001539281&type=SC+13D",
                "confidence": 0.55,
            },
            {
                "campaign_id": "forager_RPAY_2025",
                "target_ticker": target_ticker,
                "target_cik": target_cik,
                "target_company_name": "Repay Holdings Corporation",
                "sector": "Technology",
                "first_13d_date": "2025-12-15",
                "first_13d_accession": "0001539281-25-000012",
                "first_13d_position_pct": 5.2,
                "latest_13da_date": "2026-04-17",
                "latest_13da_accession": "0001539281-26-000007",
                "latest_position_pct": 11.9,
                "n_amendments": 4,
                "amendment_cadence_days": 30,
                "outcome_status": "active",
                "outcome_event": "Tendered $4.80/sh all-cash proposal 2026-04-17; board engaged JPM/Sullivan & Cromwell to review",
                "outcome_event_date": "2026-04-17",
                "holding_days_to_outcome": None,
                "thesis_realized_return": None,
                "board_nomination_filing": False,
                "settlement_terms": None,
                "language_intensification": "increasing",
                "source_first": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001720592&type=SC+13D",
                "source_latest": "https://www.sec.gov/Archives/edgar/data/1720592/000165495426003460/primary_doc.xml",
                "confidence": 0.85,
            },
        ],
        "international_extensions": {
            "uk_tr1_disclosures": [],
            "jp_meti_5pct_filings": [],
            "eu_transparency_notifications": [],
        },
        "escalation_patterns": {
            "median_amendment_cadence_days": 60,
            "filings_with_board_nomination": 1,
            "filings_with_strategic_review_demand": 2,
            "language_intensity_trend": "increasing",
        },
        "confidence": 0.30,
        "source": "illustrative defaults (offline mode)",
        "data_quality_notes": [
            "offline mode — values illustrative; do not use for live decisioning",
            "n_campaigns < 5: emerging-activist sample — success rate weakly anchored",
        ],
    }
    return sidecar


def render_markdown(s: Dict[str, Any]) -> str:
    """Render the sidecar dict as a markdown report."""
    lines: List[str] = []
    lines.append(f"# Activist Track Record — {s['filer_name']}")
    lines.append("")
    lines.append(f"**Filer CIK:** {s.get('filer_cik','')}")
    lines.append(f"**Aliases:** {', '.join(s.get('filer_aliases',[]))}")
    lines.append(f"**As of:** {s.get('as_of','')}")
    lines.append(f"**Lookback:** {s.get('lookback_years','')}y")
    ct = s.get("current_target", {})
    lines.append(
        f"**Current target under review:** {ct.get('ticker','?')} "
        f"({ct.get('company_name','?')}) — in campaign list: "
        f"{'yes' if ct.get('in_campaign_list') else 'no'}"
    )
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value | n | CI95 | Source |")
    lines.append("|---|---|---|---|---|")
    sr = s.get("success_rate")
    sr_str = f"{round((sr or 0.0)*100)}%" if sr is not None else "n/a"
    sr_ci = s.get("success_rate_ci") or []
    sr_ci_str = f"{round(sr_ci[0]*100)}%–{round(sr_ci[1]*100)}%" if len(sr_ci) == 2 else "–"
    lines.append(f"| Prior campaigns | {s.get('n_campaigns',0)} | – | – | EDGAR submissions API |")
    lines.append(f"| Distinct targets | {s.get('n_distinct_targets',0)} | – | – | EDGAR |")
    lines.append(f"| Active campaigns | {s.get('n_active',0)} | – | – | EDGAR |")
    lines.append(f"| Resolved campaigns | {s.get('n_resolved',0)} | – | – | EDGAR |")
    lines.append(f"| Success rate | {sr_str} | {s.get('n_resolved',0)} | {sr_ci_str} | derived |")
    lines.append(f"| Avg position % | {s.get('avg_position_pct','n/a')} | – | – | parsed 13D |")
    lines.append(f"| Avg holding days to outcome | {s.get('avg_holding_days','n/a')} | – | – | derived |")
    lines.append(f"| Avg amendments per campaign | {s.get('avg_amendments_per_campaign','n/a')} | – | – | EDGAR |")
    lines.append("")
    lines.append("## Tier classification")
    lines.append("")
    tb = s.get("tier_benchmarks", {})
    lines.append(f"**{s.get('tier_classification','unknown')}**")
    lines.append("")
    lines.append("Versus tier-1 benchmark (Elliott / Icahn / Starboard / ValueAct / Trian / Pershing / Jana / Engaged):")
    lines.append(f"- Tier-1 minimum campaigns: {tb.get('tier_1_minimum_campaigns','?')} → this filer: {tb.get('this_filer_n_campaigns','?')}")
    sr1 = tb.get("tier_1_minimum_success_rate", 0)
    sr_tf = tb.get("this_filer_success_rate")
    lines.append(
        f"- Tier-1 minimum success rate: {round(sr1*100)}% → this filer: "
        f"{round((sr_tf or 0)*100)}%"
    )
    lines.append(f"- Verdict: {tb.get('this_filer_vs_tier_1','?')}")
    lines.append("")

    lines.append("## Campaigns")
    lines.append("")
    for i, c in enumerate(s.get("campaigns", []), start=1):
        tt = c.get("target_ticker", "?")
        tn = c.get("target_company_name", "?")
        lines.append(f"### Campaign {i} — {tt} ({tn})")
        lines.append("")
        lines.append(f"- Sector: {c.get('sector','?')}")
        lines.append(
            f"- First 13D: {c.get('first_13d_date','?')} — {c.get('first_13d_position_pct','?')}% "
            f"(accession {c.get('first_13d_accession','?')}, [link]({c.get('source_first','')}))"
        )
        lines.append(
            f"- Latest 13D/A: {c.get('latest_13da_date','?')} — {c.get('latest_position_pct','?')}% "
            f"({c.get('n_amendments',0)} amendments, cadence {c.get('amendment_cadence_days','?')}d, "
            f"[link]({c.get('source_latest','')}))"
        )
        lines.append(f"- Outcome: **{c.get('outcome_status','?')}** — {c.get('outcome_event','')} ({c.get('outcome_event_date','')})")
        lines.append(f"- Holding days to outcome: {c.get('holding_days_to_outcome','n/a')}")
        lines.append(f"- Realized return: {c.get('thesis_realized_return','n/a')}")
        lines.append(f"- Board nomination filing: {'yes' if c.get('board_nomination_filing') else 'no'}")
        lines.append(f"- Settlement terms: {c.get('settlement_terms','none')}")
        lines.append(f"- Language intensification: {c.get('language_intensification','?')}")
        lines.append(f"- Confidence: {c.get('confidence',0.0)}")
        lines.append("")

    lines.append("## Sector concentration")
    lines.append("")
    lines.append("| Sector | Campaigns |")
    lines.append("|---|---|")
    for sec, count in (s.get("sector_concentration") or {}).items():
        lines.append(f"| {sec} | {count} |")
    lines.append("")

    ep = s.get("escalation_patterns", {})
    lines.append("## Escalation patterns (this filer)")
    lines.append("")
    lines.append(f"- Median amendment cadence: {ep.get('median_amendment_cadence_days','?')}d")
    lines.append(f"- Filings with board nomination: {ep.get('filings_with_board_nomination',0)}")
    lines.append(f"- Filings with strategic review demand: {ep.get('filings_with_strategic_review_demand',0)}")
    lines.append(f"- Language intensity trend: {ep.get('language_intensity_trend','?')}")
    lines.append("")

    lines.append("## International extensions")
    lines.append("")
    ie = s.get("international_extensions", {})
    lines.append(f"- UK FCA TR-1: {ie.get('uk_tr1_disclosures') or 'none'}")
    lines.append(f"- JP METI 5%-rule: {ie.get('jp_meti_5pct_filings') or 'none'}")
    lines.append(f"- EU Transparency Directive: {ie.get('eu_transparency_notifications') or 'none'}")
    lines.append("")

    lines.append("## Data-quality notes")
    lines.append("")
    for n in s.get("data_quality_notes", []):
        lines.append(f"- {n}")
    lines.append("")

    lines.append("## Sources")
    lines.append("")
    lines.append(f"- Source label: {s.get('source','EDGAR')}")
    lines.append(f"- Tier-1 benchmark: helpers/tier1_benchmark_data.py (last updated {t1b.LAST_UPDATED})")
    lines.append(f"- Confidence: {s.get('confidence',0.0)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Skill: research-activist-filer.*")
    return "\n".join(lines) + "\n"


def build_live(filer_name: str, target_ticker: str, target_cik: str, filer_cik_hint: Optional[str], lookback_years: int) -> Dict[str, Any]:
    """Live build path (network-required)."""
    notes: List[str] = []
    sources: List[str] = []

    # Step 1 — resolve CIK
    if filer_cik_hint:
        resolved = {
            "ok": True,
            "cik": filer_cik_hint.lstrip("0") or "0",
            "padded": filer_cik_hint.zfill(10),
            "matched_name": filer_name,
            "aliases": [filer_name],
            "confidence": 0.95,
            "method": "explicit_filer_cik_hint",
            "source": "input",
        }
    else:
        resolved = efh.resolve_filer_cik(filer_name)
    sources.append(resolved.get("source", ""))

    if not resolved.get("ok"):
        return {
            "_status": "no_cik_resolved",
            "filer_name": filer_name,
            "as_of": now_iso(),
            "data_quality_notes": ["Filer name not resolved to a CIK", resolved.get("reason", "")],
            "confidence": 0.0,
            "n_campaigns": 0,
            "campaigns": [],
            "current_target": {
                "ticker": target_ticker,
                "cik": target_cik,
                "company_name": "",
                "in_campaign_list": False,
            },
        }

    filer_cik = resolved["cik"]

    # Step 2 — list 13D filings
    fl = efh.list_13d_filings(filer_cik, lookback_years=lookback_years)
    if not fl.get("ok"):
        notes.append(f"edgar_submissions_unavailable: {fl.get('reason')}")

    filings = fl.get("filings", [])

    # Step 2.5 — for each filing, attempt to identify subject CIK + parse percent_of_class
    for f in filings:
        f["target_cik"] = efh.detect_target_cik_from_index(filer_cik, f["accession_no"])
        time.sleep(0.15)  # polite throttle
        pdoc = efh.fetch_primary_doc(filer_cik, f["accession_no"], f.get("primary_document"))
        if pdoc.get("ok"):
            parsed = pdoc.get("parsed", {}) or {}
            f["percent_of_class"] = parsed.get("percent_of_class")
            f["item_four_text"] = parsed.get("item_four_text")
            f["parse_confidence"] = parsed.get("parse_confidence", 0.30)
            f["primary_url"] = pdoc.get("url")
            # If subject CIK could not be found via index page, fall back to parsed value
            if not f.get("target_cik") and parsed.get("subject_cik"):
                f["target_cik"] = parsed["subject_cik"]
            f["subject_company"] = parsed.get("subject_company")
        else:
            f["percent_of_class"] = None
            f["item_four_text"] = None
            f["parse_confidence"] = 0.30
            f["primary_url"] = None
        time.sleep(0.15)

    # Step 3 — group into campaigns by target_cik
    by_target: Dict[str, List[Dict[str, Any]]] = {}
    for f in filings:
        tcik = f.get("target_cik") or "_unknown"
        by_target.setdefault(tcik, []).append(f)

    # Step 4..5..6 — outcomes + sector + aggregates
    campaigns: List[Dict[str, Any]] = []
    n_active = 0
    n_resolved = 0
    n_successes = 0
    holding_days_list: List[int] = []
    pct_list: List[float] = []
    n_amend_list: List[int] = []
    cadence_list: List[float] = []
    sectors: Counter = Counter()
    nomination_count = 0
    strategic_count = 0
    intensity_labels: List[str] = []

    for tcik, gfl in by_target.items():
        # sort chronologically
        gfl_sorted = sorted(gfl, key=lambda x: x.get("filing_date", ""))
        first = gfl_sorted[0]
        latest = gfl_sorted[-1]
        n_amendments = sum(1 for f in gfl_sorted if f.get("form") == "SC 13D/A")
        n_amend_list.append(n_amendments)

        # Cadence
        if len(gfl_sorted) >= 2:
            try:
                deltas = []
                prev = dt.datetime.strptime(gfl_sorted[0]["filing_date"], "%Y-%m-%d")
                for f in gfl_sorted[1:]:
                    cur = dt.datetime.strptime(f["filing_date"], "%Y-%m-%d")
                    deltas.append((cur - prev).days)
                    prev = cur
                cadence_days = float(statistics.median(deltas)) if deltas else None
            except Exception:
                cadence_days = None
        else:
            cadence_days = None
        if cadence_days is not None:
            cadence_list.append(cadence_days)

        target_meta = cor.lookup_target_meta(tcik) if tcik and tcik != "_unknown" else {"ok": False}
        target_ticker_resolved = target_meta.get("ticker") if target_meta.get("ok") else "_unknown"
        target_name_resolved = target_meta.get("name") if target_meta.get("ok") else first.get("subject_company") or "Unknown"
        sector = sic_to_sector(target_meta.get("sic", "")) if target_meta.get("ok") else "Other"
        sectors[sector] += 1

        # Outcome
        latest_pct = latest.get("percent_of_class")
        if pct_list_val := latest_pct:
            pct_list.append(float(pct_list_val))

        outcome = cor.classify_outcome(
            target_cik=tcik,
            target_name=target_name_resolved,
            filer_name=filer_name,
            filer_aliases=resolved.get("aliases", []),
            first_13d_date=first.get("filing_date", ""),
            latest_13da_date=latest.get("filing_date", ""),
            latest_position_pct=latest_pct,
        ) if (tcik and tcik != "_unknown") else {
            "outcome_status": "manual_review",
            "outcome_event": "target_cik unresolved",
            "outcome_event_date": None,
            "source_url": "",
            "confidence": 0.30,
        }

        # Counters
        os_ = outcome.get("outcome_status")
        if os_ == "active":
            n_active += 1
        elif os_ in ("sale_merger_closed", "topping_bid_emerged", "settlement_board_seats", "proxy_fight_won"):
            n_resolved += 1
            n_successes += 1
        elif os_ in ("withdrawal", "proxy_fight_lost", "settlement_standstill_only"):
            n_resolved += 1
        # stale_active and manual_review excluded from denominator

        # holding days
        oed = outcome.get("outcome_event_date")
        if oed and first.get("filing_date"):
            try:
                holding_days_list.append((dt.datetime.strptime(oed, "%Y-%m-%d") - dt.datetime.strptime(first["filing_date"], "%Y-%m-%d")).days)
            except Exception:
                pass

        # nomination / strategic / intensity
        item4_first = first.get("item_four_text")
        item4_latest = latest.get("item_four_text")
        nomination_filing = detect_board_nomination(item4_latest or item4_first or "")
        strategic_demand = detect_strategic_review_demand(item4_latest or item4_first or "")
        if nomination_filing:
            nomination_count += 1
        if strategic_demand:
            strategic_count += 1
        intensity = detect_language_intensity(item4_first, item4_latest)
        intensity_labels.append(intensity)

        campaign_id = f"{filer_slug(filer_name)}_{(target_ticker_resolved or '_unknown').lower()}_{first.get('filing_date','')[:4]}"

        campaigns.append({
            "campaign_id": campaign_id,
            "target_ticker": target_ticker_resolved or "_unknown",
            "target_cik": tcik,
            "target_company_name": target_name_resolved,
            "sector": sector,
            "first_13d_date": first.get("filing_date"),
            "first_13d_accession": first.get("accession_no"),
            "first_13d_position_pct": first.get("percent_of_class"),
            "latest_13da_date": latest.get("filing_date"),
            "latest_13da_accession": latest.get("accession_no"),
            "latest_position_pct": latest_pct,
            "n_amendments": n_amendments,
            "amendment_cadence_days": round(cadence_days, 1) if cadence_days is not None else None,
            "outcome_status": outcome.get("outcome_status"),
            "outcome_event": outcome.get("outcome_event"),
            "outcome_event_date": outcome.get("outcome_event_date"),
            "holding_days_to_outcome": (holding_days_list[-1] if holding_days_list and (oed and first.get("filing_date")) else None),
            "thesis_realized_return": None,
            "board_nomination_filing": nomination_filing,
            "settlement_terms": None,
            "language_intensification": intensity,
            "source_first": first.get("primary_url") or first.get("url_index"),
            "source_latest": latest.get("primary_url") or latest.get("url_index"),
            "confidence": min(0.95, max(0.40, (first.get("parse_confidence", 0.5) + latest.get("parse_confidence", 0.5)) / 2.0 * (outcome.get("confidence", 0.7)) + 0.10)),
        })

    n_total = len(campaigns)
    success_rate = round(n_successes / n_resolved, 3) if n_resolved else None
    sr_ci = wilson_ci(n_successes, n_resolved) if n_resolved else [0.0, 0.0]
    avg_pct = round(statistics.mean(pct_list), 2) if pct_list else None
    avg_hold = round(statistics.mean(holding_days_list)) if holding_days_list else None
    avg_amend = round(statistics.mean(n_amend_list), 2) if n_amend_list else None

    classification = t1b.classify(
        filer_name=filer_name,
        n_campaigns=n_total,
        success_rate=success_rate or 0.0,
        current_target_in_list=any(str(c.get("target_cik")) == str(target_cik).lstrip("0") for c in campaigns) if target_cik else False,
        aliases=resolved.get("aliases", []),
    )
    bench = t1b.benchmark_vs_tier_1(n_total, success_rate)

    if n_total < 5:
        notes.append("n_campaigns < 5: emerging-activist sample — success rate weakly anchored")
    if n_resolved < 5:
        notes.append(f"avg_holding_days computed only on n={n_resolved} resolved campaigns")

    sidecar = {
        "filer_name": filer_name,
        "filer_cik": filer_cik,
        "filer_aliases": resolved.get("aliases", [filer_name]),
        "as_of": now_iso(),
        "lookback_years": lookback_years,
        "current_target": {
            "ticker": target_ticker,
            "cik": target_cik,
            "company_name": "",
            "in_campaign_list": any(str(c.get("target_cik")) == str(target_cik).lstrip("0") for c in campaigns) if target_cik else False,
        },
        "n_campaigns": n_total,
        "n_distinct_targets": len(by_target),
        "n_active": n_active,
        "n_resolved": n_resolved,
        "success_rate": success_rate,
        "success_rate_ci": sr_ci,
        "avg_position_pct": avg_pct,
        "avg_holding_days": avg_hold,
        "avg_amendments_per_campaign": avg_amend,
        "sector_concentration": dict(sectors),
        "tier_classification": classification.get("tier_classification"),
        "tier_match_canonical_name": classification.get("tier_match_canonical_name"),
        "tier_match_rationale": classification.get("rationale"),
        "tier_benchmarks": bench,
        "campaigns": campaigns,
        "international_extensions": {
            "uk_tr1_disclosures": [],
            "jp_meti_5pct_filings": [],
            "eu_transparency_notifications": [],
        },
        "escalation_patterns": {
            "median_amendment_cadence_days": round(statistics.median(cadence_list), 1) if cadence_list else None,
            "filings_with_board_nomination": nomination_count,
            "filings_with_strategic_review_demand": strategic_count,
            "language_intensity_trend": Counter(intensity_labels).most_common(1)[0][0] if intensity_labels else "unknown",
        },
        "confidence": round(min(0.92, 0.45 + 0.05 * min(n_total, 8) + (0.10 if classification.get("tier_classification") == "tier_1" else 0.0)), 2),
        "source": "SEC EDGAR EFTS + EDGAR submissions API",
        "data_quality_notes": notes + ([f"international_extensions_not_queried"] if True else []),
    }
    return sidecar


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--filer", required=True, help="Filer name (or numeric CIK)")
    p.add_argument("--target-ticker", required=True)
    p.add_argument("--target-cik", default="")
    p.add_argument("--filer-cik", default="", help="If known, skip name->CIK resolution")
    p.add_argument("--lookback-years", type=int, default=15)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--offline", action="store_true")
    args = p.parse_args()

    started = time.time()

    # HALT_FLAG
    if not args.offline and os.path.exists(HALT_FLAG_PATH):
        print(json.dumps({"status": "halted", "reason": "HALT_FLAG present", "halt_flag": HALT_FLAG_PATH}))
        return 0

    if args.offline:
        sidecar = offline_illustrative(args.filer, args.target_ticker, args.target_cik or "1720592")
    else:
        sidecar = build_live(args.filer, args.target_ticker, args.target_cik, args.filer_cik or None, args.lookback_years)

    # Decide output paths
    slug = filer_slug(args.filer)
    os.makedirs(args.output_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, f"{slug}_campaigns.json")
    md_path = os.path.join(args.output_dir, f"{slug}_track_record.md")

    # Atomic writes (JSON first, then markdown)
    atomic_write_text(json_path, json.dumps(sidecar, indent=2, default=str) + "\n")
    atomic_write_text(md_path, render_markdown(sidecar))

    duration = round(time.time() - started, 2)
    summary = {
        "status": "ok" if sidecar.get("_status") != "no_cik_resolved" else "no_cik_resolved",
        "filer": args.filer,
        "cik": sidecar.get("filer_cik", ""),
        "prior_campaigns": sidecar.get("n_campaigns", 0),
        "success_rate": sidecar.get("success_rate"),
        "avg_position_pct": sidecar.get("avg_position_pct"),
        "avg_holding_days": sidecar.get("avg_holding_days"),
        "tier_classification": sidecar.get("tier_classification"),
        "confidence": sidecar.get("confidence", 0.0),
        "output_md": md_path,
        "output_json": json_path,
        "duration_s": duration,
    }
    print(json.dumps(summary))
    return 0 if summary["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
