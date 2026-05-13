"""analyze.py — P4 orchestrator.

Resolves an acquirer, pulls prior acquirer-side M&A filings, classifies each
deal's outcome and per-jurisdiction regulatory result, parses MAC clauses
from definitive agreements, computes aggregate metrics + tier classification,
atomically writes the JSON sidecar and markdown report, and prints a
structured stdout summary.

Online mode performs network calls; offline mode (`--offline`) uses
hand-curated illustrative defaults from the BAWAG worked example.

Usage:
    python analyze.py \
      --acquirer "BAWAG Group AG" \
      --target-ticker PTSB \
      --target-cik 1738758 \
      --jurisdiction IE \
      --offline \
      --output-dir .
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Local imports — same dir
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from atomic_write import atomic_write_text  # noqa: E402
from acquirer_ma_history import (  # noqa: E402
    resolve_acquirer,
    pull_acquirer_filings,
    offline_illustrative_filings,
)
from regulatory_outcome_tracker import (  # noqa: E402
    JURISDICTIONS,
    lookup_decision,
    offline_illustrative_outcomes,
)
from mac_clause_extractor import (  # noqa: E402
    parse_definitive_agreement,
    offline_illustrative_mac,
)
from tier1_acquirer_benchmark import (  # noqa: E402
    classify,
    benchmark_vs_tier_1,
    is_pe_acquirer,
)


# ---------- ILLUSTRATIVE PROFILE FOR OFFLINE BAWAG SMOKE TEST ----------

BAWAG_OFFLINE_DEAL_PROFILE: List[Dict] = [
    {
        "deal_accession_key": "ILLUSTRATIVE-2024-DPB",
        "target_ticker": "DPB",
        "target_company_name": "Deutsche Pfandbriefbank AG (illustrative)",
        "target_country": "DE",
        "sector": "Financials",
        "announced_date": "2024-06-15",
        "deal_value_usd_mm": 1450,
        "consideration_type": "all_cash",
        "premium_to_30d_vwap_pct": 22.0,
        "structure": "tender_offer",
        "regulatory_jurisdictions": ["EU", "DE", "AT"],
        "outcome_status": "closed",
        "outcome_event_date": "2025-01-20",
        "financing_source": "balance_sheet_cash + EUR 500M revolver",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2024-06-15-Offer.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2025-01-20-Closing.html",
    },
    {
        "deal_accession_key": "ILLUSTRATIVE-2023-KNAB",
        "target_ticker": "_unlisted",
        "target_company_name": "Knab (Dutch online bank, illustrative)",
        "target_country": "NL",
        "sector": "Financials",
        "announced_date": "2023-03-08",
        "deal_value_usd_mm": 720,
        "consideration_type": "all_cash",
        "premium_to_30d_vwap_pct": 18.0,
        "structure": "merger",
        "regulatory_jurisdictions": ["EU", "NL"],
        "outcome_status": "closed",
        "outcome_event_date": "2023-09-28",
        "financing_source": "balance_sheet_cash",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2023-03-08-Knab.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2023-09-28-Knab-Closing.html",
    },
    {
        "deal_accession_key": "ILLUSTRATIVE-2022-RBS",
        "target_ticker": "_unlisted",
        "target_company_name": "Raiffeisen Bausparkasse (illustrative AT consolidation)",
        "target_country": "AT",
        "sector": "Financials",
        "announced_date": "2022-08-22",
        "deal_value_usd_mm": 410,
        "consideration_type": "all_cash",
        "premium_to_30d_vwap_pct": 20.0,
        "structure": "merger",
        "regulatory_jurisdictions": ["AT"],
        "outcome_status": "closed",
        "outcome_event_date": "2023-02-15",
        "financing_source": "balance_sheet_cash",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2022-08-22-Raiffeisen.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2023-02-15-RBS-Closing.html",
    },
    {
        "deal_accession_key": "ILLUSTRATIVE-2021-HELLOBANK",
        "target_ticker": "_unlisted",
        "target_company_name": "Hello bank! Austria (BNP Paribas Austria, illustrative)",
        "target_country": "AT",
        "sector": "Financials",
        "announced_date": "2021-11-10",
        "deal_value_usd_mm": 380,
        "consideration_type": "all_cash",
        "premium_to_30d_vwap_pct": 15.0,
        "structure": "carve_out_purchase",
        "regulatory_jurisdictions": ["AT", "FR"],
        "outcome_status": "closed",
        "outcome_event_date": "2022-04-30",
        "financing_source": "balance_sheet_cash",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2021-11-10-Hellobank.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2022-04-30-Hellobank-Closing.html",
    },
    {
        "deal_accession_key": "ILLUSTRATIVE-2020-SUDWEST",
        "target_ticker": "_unlisted",
        "target_company_name": "Südwestbank (illustrative DE add-on)",
        "target_country": "DE",
        "sector": "Financials",
        "announced_date": "2020-06-01",
        "deal_value_usd_mm": 510,
        "consideration_type": "cash_and_stock",
        "premium_to_30d_vwap_pct": 25.0,
        "structure": "merger",
        "regulatory_jurisdictions": ["DE"],
        "outcome_status": "closed",
        "outcome_event_date": "2020-12-15",
        "financing_source": "balance_sheet_cash + 8% stock issuance",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2020-06-01-Sudwest.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2020-12-15-Sudwest-Closing.html",
    },
    {
        "deal_accession_key": "ILLUSTRATIVE-2019-SIRIO",
        "target_ticker": "_unlisted",
        "target_company_name": "Sirio (illustrative IT consumer-finance carve-out)",
        "target_country": "IT",
        "sector": "Financials",
        "announced_date": "2019-04-04",
        "deal_value_usd_mm": 290,
        "consideration_type": "all_cash",
        "premium_to_30d_vwap_pct": 30.0,
        "structure": "carve_out_purchase",
        "regulatory_jurisdictions": ["EU", "IT"],
        "outcome_status": "repriced_then_closed",
        "outcome_event_date": "2019-12-20",
        "financing_source": "balance_sheet_cash",
        "source_announce": "https://www.bawaggroup.com/EN/IR/Press-releases/2019-04-04-Sirio.html",
        "source_close": "https://www.bawaggroup.com/EN/IR/Press-releases/2019-12-20-Sirio-Closing.html",
    },
]


# ---------- HELPERS ----------


def _slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "unknown_acquirer"


def wilson_ci(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p_hat = successes / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def days_between(a: str, b: str) -> Optional[int]:
    try:
        da = datetime.fromisoformat(a)
        db = datetime.fromisoformat(b)
        return (db - da).days
    except Exception:
        return None


def median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---------- DEAL ASSEMBLY ----------


def build_deals_offline(acquirer: str) -> List[Dict]:
    """For offline smoke test: hand-curated deals stitched with regulator + MAC outputs."""
    if "bawag" not in acquirer.lower():
        return []
    outcomes = offline_illustrative_outcomes(acquirer)
    deals: List[Dict] = []
    for profile in BAWAG_OFFLINE_DEAL_PROFILE:
        accession_key = profile["deal_accession_key"]
        per_jurisdiction = outcomes.get(accession_key, {})
        # MAC parse from offline illustrative
        mac = offline_illustrative_mac(acquirer, accession_key)
        announced = profile["announced_date"]
        outcome_date = profile["outcome_event_date"]
        ttc = days_between(announced, outcome_date) if outcome_date else None
        deal = {
            "deal_id": f"bawag_{_slugify(profile['target_company_name'])}_{announced[:4]}",
            "target_ticker": profile["target_ticker"],
            "target_cik": None,
            "target_company_name": profile["target_company_name"],
            "target_country": profile["target_country"],
            "sector": profile["sector"],
            "announced_date": announced,
            "definitive_agreement_url": profile["source_announce"],
            "deal_value_usd_mm": profile["deal_value_usd_mm"],
            "consideration_type": profile["consideration_type"],
            "premium_to_30d_vwap_pct": profile["premium_to_30d_vwap_pct"],
            "structure": profile["structure"],
            "regulatory_jurisdictions": profile["regulatory_jurisdictions"],
            "regulatory_outcomes": per_jurisdiction,
            "outcome_status": profile["outcome_status"],
            "outcome_event_date": outcome_date,
            "time_to_close_days": ttc,
            "mac_invoked": False,
            "mac_clause_quoted": mac.get("mac_clause_quoted"),
            "mac_carve_outs": mac.get("mac_carve_outs", []),
            "mac_tightness_score": mac.get("mac_tightness_score"),
            "break_fee_terms": mac.get("break_fee_terms"),
            "financing_condition_present": mac.get("financing_condition_present"),
            "financing_source": profile["financing_source"],
            "post_close_realized_premium_to_announce_pct": 0.0,
            "source_announce": profile["source_announce"],
            "source_close": profile["source_close"],
            "confidence": 0.78,
        }
        deals.append(deal)
    return deals


def build_deals_online(acquirer_resolution: Dict, acquirer: str, lookback_years: int) -> List[Dict]:
    """Online path — pull acquirer filings, classify acquirer-side, then enrich.

    Best-effort; degrades gracefully when network calls fail.
    """
    deals: List[Dict] = []
    cik = acquirer_resolution.get("cik")
    filings: List[Dict] = []
    if cik:
        try:
            filings = pull_acquirer_filings(cik, lookback_years=lookback_years)
        except Exception:
            filings = []
    # Group filings by year + form prefix as a coarse "deal" key. Real implementation
    # would link DEFM14A → S-4 → 8-K Item 2.01 by target name; here we keep a
    # conservative one-deal-per-DEFM14A skeleton when filings exist.
    for f in filings:
        if f["form"] not in ("DEFM14A", "S-4", "SC TO-T", "SC TO-I", "SC 13E3"):
            continue
        announced = f["filing_date"]
        deal = {
            "deal_id": f"{_slugify(acquirer)}_{f['accession']}",
            "target_ticker": "_unknown_target",
            "target_cik": None,
            "target_company_name": f.get("primary_doc_description") or "unresolved target",
            "target_country": None,
            "sector": None,
            "announced_date": announced,
            "definitive_agreement_url": f["primary_doc_url"],
            "deal_value_usd_mm": None,
            "consideration_type": None,
            "premium_to_30d_vwap_pct": None,
            "structure": None,
            "regulatory_jurisdictions": [],
            "regulatory_outcomes": {},
            "outcome_status": "manual_review",
            "outcome_event_date": None,
            "time_to_close_days": None,
            "mac_invoked": None,
            "mac_clause_quoted": None,
            "mac_carve_outs": [],
            "mac_tightness_score": None,
            "break_fee_terms": None,
            "financing_condition_present": None,
            "financing_source": None,
            "post_close_realized_premium_to_announce_pct": None,
            "source_announce": f["primary_doc_url"],
            "source_close": None,
            "confidence": 0.50,
        }
        # MAC parse — best effort
        try:
            mac = parse_definitive_agreement(f["primary_doc_url"])
            deal.update({
                "mac_clause_quoted": mac.get("mac_clause_quoted"),
                "mac_carve_outs": mac.get("mac_carve_outs", []),
                "mac_tightness_score": mac.get("mac_tightness_score"),
                "break_fee_terms": mac.get("break_fee_terms"),
                "financing_condition_present": mac.get("financing_condition_present"),
            })
        except Exception:
            pass
        deals.append(deal)
    return deals


# ---------- AGGREGATE METRICS ----------


def compute_aggregates(deals: List[Dict]) -> Dict:
    n_total = len(deals)
    n_closed = sum(1 for d in deals if d.get("outcome_status") in ("closed", "repriced_then_closed"))
    n_withdrawn = sum(1 for d in deals if d.get("outcome_status") == "withdrawn")
    n_blocked = sum(1 for d in deals if d.get("outcome_status") == "blocked")
    n_repriced = sum(1 for d in deals if d.get("outcome_status") == "repriced_then_closed")
    n_active = sum(1 for d in deals if d.get("outcome_status") in ("active", "manual_review"))
    n_resolved = n_closed + n_withdrawn + n_blocked
    close_rate = (n_closed / n_resolved) if n_resolved else None
    ci_lo, ci_hi = wilson_ci(n_closed, n_resolved) if n_resolved else (0.0, 1.0)

    times_to_close = [d["time_to_close_days"] for d in deals if d.get("time_to_close_days") is not None]
    premia = [d["premium_to_30d_vwap_pct"] for d in deals if d.get("premium_to_30d_vwap_pct") is not None]

    financing_dist = {"all_cash": 0, "all_stock": 0, "cash_and_stock": 0, "debt_funded": 0, "other": 0}
    for d in deals:
        ct = (d.get("consideration_type") or "").lower()
        if ct in financing_dist:
            financing_dist[ct] += 1
        elif ct:
            financing_dist["other"] += 1

    # Aggregate per-jurisdiction outcomes
    reg_agg: Dict[str, Dict[str, int]] = {}
    for d in deals:
        for j, block in (d.get("regulatory_outcomes") or {}).items():
            if j not in reg_agg:
                reg_agg[j] = {"deals_reviewed": 0, "cleared_unconditional": 0, "cleared_with_remedies": 0, "blocked": 0, "withdrawn_pre_decision": 0, "review_status_unknown": 0}
            reg_agg[j]["deals_reviewed"] += 1
            outcome = (block.get("outcome") if isinstance(block, dict) else "review_status_unknown") or "review_status_unknown"
            if outcome in reg_agg[j]:
                reg_agg[j][outcome] += 1
            else:
                reg_agg[j]["review_status_unknown"] += 1

    # MAC clause patterns
    mac_patterns = {
        "deals_with_mac_carve_outs_for_pandemic_or_war": sum(1 for d in deals if "pandemic_war" in (d.get("mac_carve_outs") or [])),
        "deals_with_financing_condition_carve_outs": sum(1 for d in deals if d.get("financing_condition_present") is False),
        "deals_with_no_mac_clause": sum(1 for d in deals if d.get("mac_clause_quoted") is None and d.get("mac_tightness_score") is None and d.get("outcome_status") not in ("manual_review",)),
        "deals_with_invoked_mac": sum(1 for d in deals if d.get("mac_invoked") is True),
        "trend": "tightening" if len([d for d in deals if d.get("mac_tightness_score") == "target_friendly"]) > len([d for d in deals if d.get("mac_tightness_score") == "acquirer_friendly"]) else "stable",
    }

    return {
        "n_prior_deals": n_total,
        "n_closed": n_closed,
        "n_withdrawn": n_withdrawn,
        "n_blocked": n_blocked,
        "n_repriced": n_repriced,
        "n_active": n_active,
        "n_resolved": n_resolved,
        "close_rate": close_rate,
        "close_rate_ci": [round(ci_lo, 3), round(ci_hi, 3)] if n_resolved else None,
        "avg_time_to_close_days": round(sum(times_to_close) / len(times_to_close)) if times_to_close else None,
        "median_time_to_close_days": int(median(times_to_close)) if times_to_close else None,
        "avg_premium_pct": round(sum(premia) / len(premia), 2) if premia else None,
        "median_premium_pct": round(median(premia), 2) if premia else None,
        "mac_invocation_rate": (sum(1 for d in deals if d.get("mac_invoked")) / n_resolved) if n_resolved else 0.0,
        "break_fee_paid_count": sum(1 for d in deals if (d.get("break_fee_terms") or "").strip()),
        "financing_distribution": financing_dist,
        "regulatory_outcomes_by_jurisdiction": reg_agg,
        "mac_clause_patterns": mac_patterns,
    }


# ---------- MARKDOWN RENDER ----------


def render_markdown(sidecar: Dict) -> str:
    lines: List[str] = []
    lines.append(f"# Acquirer M&A History — {sidecar['acquirer_name']}")
    lines.append("")
    lines.append(f"**Acquirer ID type:** {sidecar['acquirer_id_type']}")
    lines.append(f"**CIK:** {sidecar['acquirer_cik'] or 'n/a'}")
    lines.append(f"**LEI:** {sidecar.get('acquirer_lei') or 'n/a'}")
    lines.append(f"**Aliases:** {', '.join(sidecar.get('acquirer_aliases') or [])}")
    lines.append(f"**Country:** {sidecar.get('acquirer_country') or 'n/a'}")
    lines.append(f"**As of:** {sidecar['as_of']}")
    lines.append(f"**Lookback:** {sidecar['lookback_years']}y")
    ct = sidecar.get("current_target", {}) or {}
    lines.append(
        f"**Current target under review:** {ct.get('ticker')} ({ct.get('company_name')}) — "
        f"jurisdiction {ct.get('jurisdiction')}"
    )
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    cr = sidecar.get("close_rate")
    cr_ci = sidecar.get("close_rate_ci")
    cr_pct = f"{cr * 100:.1f}%" if cr is not None else "n/a"
    cr_ci_str = f"{cr_ci[0] * 100:.0f}%–{cr_ci[1] * 100:.0f}%" if cr_ci else "n/a"
    lines.append("| Metric | Value | n | CI95 | Source |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| Prior deals | {sidecar['n_prior_deals']} | – | – | EDGAR + international regulator feeds |")
    lines.append(f"| Closed | {sidecar['n_closed']} | – | – | derived |")
    lines.append(f"| Withdrawn | {sidecar['n_withdrawn']} | – | – | derived |")
    lines.append(f"| Blocked | {sidecar['n_blocked']} | – | – | derived |")
    lines.append(f"| Re-priced | {sidecar['n_repriced']} | – | – | derived |")
    lines.append(f"| Active | {sidecar['n_active']} | – | – | derived |")
    lines.append(f"| Close rate | {cr_pct} | {sidecar['n_closed']}/{sidecar['n_resolved']} | {cr_ci_str} | derived |")
    lines.append(f"| Avg time-to-close | {sidecar.get('avg_time_to_close_days')}d | – | – | derived |")
    lines.append(f"| Avg premium to 30d VWAP | {sidecar.get('avg_premium_pct')}% | – | – | derived |")
    mac_rate = sidecar.get("mac_invocation_rate") or 0.0
    lines.append(f"| MAC invocation rate | {mac_rate * 100:.1f}% | {sum(1 for d in sidecar['deals'] if d.get('mac_invoked'))}/{sidecar['n_resolved']} | – | parsed defs |")
    lines.append("")
    lines.append("## Tier classification")
    lines.append("")
    lines.append(f"**{sidecar['tier_classification']}** — {sidecar.get('tier_classification_rationale', '')}")
    lines.append("")
    lines.append("Versus tier-1 strategic / PE benchmark:")
    tb = sidecar.get("tier_benchmarks") or {}
    lines.append(f"- Tier-1 minimum deals (strategic/PE): {tb.get('tier_1_strategic_minimum_deals')}/{tb.get('tier_1_pe_minimum_deals')}")
    lines.append(f"- Tier-1 minimum close rate (strategic/PE): {tb.get('tier_1_strategic_minimum_close_rate')}/{tb.get('tier_1_pe_minimum_close_rate')}")
    lines.append(f"- This acquirer kind: {tb.get('this_acquirer_kind')}")
    lines.append(f"- Verdict: {tb.get('this_acquirer_vs_tier_1')}")
    lines.append("")
    lines.append("## Deals")
    lines.append("")
    for i, d in enumerate(sidecar["deals"], 1):
        lines.append(f"### Deal {i} — {d.get('target_ticker')} ({d.get('target_company_name')})")
        lines.append("")
        lines.append(f"- Sector: {d.get('sector')}")
        lines.append(f"- Country: {d.get('target_country')}")
        lines.append(f"- Announced: {d.get('announced_date')}")
        lines.append(f"- Deal value: ${d.get('deal_value_usd_mm')}M" if d.get("deal_value_usd_mm") is not None else "- Deal value: n/a")
        lines.append(f"- Consideration: {d.get('consideration_type')}")
        lines.append(f"- Structure: {d.get('structure')}")
        lines.append(f"- Premium to 30d VWAP: {d.get('premium_to_30d_vwap_pct')}%")
        lines.append(f"- Definitive agreement: [link]({d.get('definitive_agreement_url')})")
        lines.append(f"- Regulatory jurisdictions: {', '.join(d.get('regulatory_jurisdictions') or []) or 'n/a'}")
        if d.get("regulatory_outcomes"):
            lines.append("- Per-jurisdiction outcomes:")
            for jur, block in (d.get("regulatory_outcomes") or {}).items():
                if isinstance(block, dict):
                    lines.append(
                        f"    - {jur}: {block.get('outcome')} "
                        f"({block.get('agency')}, {block.get('decision_date') or 'date n/a'}, "
                        f"[source]({block.get('source')}))"
                    )
        lines.append(f"- Outcome: {d.get('outcome_status')} ({d.get('outcome_event_date') or 'n/a'})")
        lines.append(f"- Time to close: {d.get('time_to_close_days')}d")
        lines.append(f"- MAC invoked: {'yes' if d.get('mac_invoked') else 'no'}")
        lines.append(f"- MAC carve-outs: {', '.join(d.get('mac_carve_outs') or []) or 'none'}")
        lines.append(f"- MAC tightness: {d.get('mac_tightness_score')}")
        lines.append(f"- Break fee: {d.get('break_fee_terms') or 'n/a'}")
        lines.append(f"- Financing source: {d.get('financing_source') or 'n/a'}")
        lines.append(f"- Confidence: {d.get('confidence')}")
        lines.append("")
    lines.append("## Regulatory clearance summary by jurisdiction")
    lines.append("")
    lines.append("| Jurisdiction | Reviewed | Cleared unconditional | Cleared w/ remedies | Blocked | Withdrawn pre-decision |")
    lines.append("|---|---|---|---|---|---|")
    for j, agg in (sidecar.get("regulatory_outcomes_by_jurisdiction") or {}).items():
        lines.append(
            f"| {j} | {agg.get('deals_reviewed', 0)} | {agg.get('cleared_unconditional', 0)} | "
            f"{agg.get('cleared_with_remedies', 0)} | {agg.get('blocked', 0)} | "
            f"{agg.get('withdrawn_pre_decision', 0)} |"
        )
    lines.append("")
    lines.append("## Financing distribution")
    lines.append("")
    lines.append("| Consideration | Deals |")
    lines.append("|---|---|")
    for k, v in (sidecar.get("financing_distribution") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## MAC clause patterns")
    lines.append("")
    mp = sidecar.get("mac_clause_patterns") or {}
    lines.append(f"- Deals with pandemic / war carve-outs: {mp.get('deals_with_mac_carve_outs_for_pandemic_or_war')}")
    lines.append(f"- Deals with financing-condition carve-outs: {mp.get('deals_with_financing_condition_carve_outs')}")
    lines.append(f"- Deals with no MAC clause: {mp.get('deals_with_no_mac_clause')}")
    lines.append(f"- Deals with invoked MAC: {mp.get('deals_with_invoked_mac')}")
    lines.append(f"- Trend over lookback: {mp.get('trend')}")
    lines.append("")
    lines.append("## Data-quality notes")
    lines.append("")
    for n in sidecar.get("data_quality_notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append(f"- Acquirer source: {sidecar.get('source')}")
    lines.append(f"- Tier-1 benchmark reference: helpers/tier1_acquirer_benchmark.py")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Skill: research-acquirer-history.*")
    return "\n".join(lines) + "\n"


# ---------- ORCHESTRATOR ----------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--acquirer", required=True)
    p.add_argument("--target-ticker", required=True)
    p.add_argument("--target-cik", default=None)
    p.add_argument("--target-company-name", default=None)
    p.add_argument("--jurisdiction", required=True, help="ISO-2 jurisdiction of current deal")
    p.add_argument("--lookback-years", type=int, default=10)
    p.add_argument("--output-dir", default=os.path.join(HERE, "..", "outputs"))
    p.add_argument("--offline", action="store_true")
    p.add_argument("--reference-folder", default=None, help="Optional path to reference folder for HALT_FLAG check")
    args = p.parse_args()

    started = time.time()

    # HALT_FLAG check (read-only reference folder)
    if args.reference_folder:
        halt_path = os.path.join(args.reference_folder, "02_System", "engine", "health", "HALT_FLAG")
        if os.path.exists(halt_path) and not args.offline:
            print(json.dumps({"status": "halted", "halt_flag_path": halt_path}))
            sys.exit(0)

    # Step 1 — resolve acquirer
    resolution = resolve_acquirer(args.acquirer, offline=args.offline)
    if resolution.get("id_type") == "unknown" or (resolution.get("confidence") or 0.0) < 0.30:
        print(json.dumps({
            "status": "no_acquirer_resolved",
            "acquirer_name": args.acquirer,
            "confidence": 0.0,
        }))
        sys.exit(2)

    # Step 2/3/4 — pull filings, group into deals
    if args.offline:
        deals = build_deals_offline(args.acquirer)
    else:
        deals = build_deals_online(resolution, args.acquirer, args.lookback_years)

    # Step 6 — per-jurisdiction outcomes for any deals lacking them (best-effort)
    if not args.offline:
        for d in deals:
            for jur in d.get("regulatory_jurisdictions") or []:
                if jur in (d.get("regulatory_outcomes") or {}):
                    continue
                year = int(d["announced_date"][:4]) if d.get("announced_date") else 1970
                d.setdefault("regulatory_outcomes", {})
                d["regulatory_outcomes"][jur] = lookup_decision(
                    jur, args.acquirer, d.get("target_company_name") or "", year, offline=args.offline
                )

    # Step 8 — aggregates
    aggregates = compute_aggregates(deals)

    # Step 9 — tier classification
    is_pe = is_pe_acquirer(args.acquirer)
    cls = classify(
        args.acquirer,
        aggregates["n_prior_deals"],
        aggregates["close_rate"] or 0.0,
        current_target_in_list=False,
        aliases=resolution.get("aliases") or [],
        force_kind="pe" if is_pe else "strategic",
    )
    bench = benchmark_vs_tier_1(
        aggregates["n_prior_deals"], aggregates["close_rate"], kind=cls["kind"]
    )

    data_quality_notes = list(resolution.get("data_quality_notes") or [])
    if aggregates["n_prior_deals"] < 10:
        data_quality_notes.append("n_prior_deals < 10 — close rate weakly anchored")
    if resolution.get("id_type") == "name_only":
        data_quality_notes.append(
            f"acquirer is non-US filer; EDGAR resolution returned no CIK — "
            f"primary sources are international regulator feeds and acquirer IR site"
        )
    if args.offline:
        data_quality_notes.append("offline mode — values illustrative, deal facts hand-curated")

    sidecar = {
        "acquirer_name": args.acquirer,
        "acquirer_id_type": resolution.get("id_type"),
        "acquirer_cik": resolution.get("cik"),
        "acquirer_lei": None,
        "acquirer_aliases": resolution.get("aliases") or [],
        "acquirer_country": "AT" if "bawag" in args.acquirer.lower() else None,
        "as_of": datetime.utcnow().isoformat() + "Z",
        "lookback_years": args.lookback_years,
        "current_target": {
            "ticker": args.target_ticker,
            "cik": args.target_cik,
            "company_name": args.target_company_name or args.target_ticker,
            "jurisdiction": args.jurisdiction,
            "in_deal_list": False,
        },
        **aggregates,
        "tier_classification": cls["tier_classification"],
        "tier_classification_rationale": cls["rationale"],
        "tier_benchmarks": bench,
        "deals": deals,
        "international_extensions": {
            "uk_cma_decisions": [],
            "eu_ec_decisions": [],
            "au_firb_decisions": [],
            "cn_mofcom_decisions": [],
            "hk_sfc_takeover_panel": [],
            "kr_fsc_decisions": [],
            "in_sebi_decisions": [],
            "br_cade_decisions": [],
            "data_quality_notes": ["international_extensions_not_queried" if not args.offline else "offline mode"],
        },
        "confidence": min(0.85, max(0.40, (resolution.get("confidence") or 0.5) * 0.6 + (0.3 if aggregates["n_prior_deals"] >= 5 else 0.1))),
        "source": "SEC EDGAR + international regulator feeds + acquirer IR press releases",
        "data_quality_notes": data_quality_notes,
    }

    # Step 10 — atomic-write outputs
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    slug = _slugify(args.acquirer)
    json_path = os.path.join(output_dir, f"{slug}_deals.json")
    md_path = os.path.join(output_dir, f"{slug}_ma_history.md")

    atomic_write_text(json_path, json.dumps(sidecar, indent=2, ensure_ascii=False))
    atomic_write_text(md_path, render_markdown(sidecar))

    duration_s = round(time.time() - started, 3)

    summary = {
        "status": "ok",
        "acquirer": args.acquirer,
        "cik": resolution.get("cik"),
        "prior_deals": aggregates["n_prior_deals"],
        "close_rate": aggregates["close_rate"],
        "avg_time_to_close_days": aggregates["avg_time_to_close_days"],
        "avg_premium_pct": aggregates["avg_premium_pct"],
        "mac_invocation_rate": aggregates["mac_invocation_rate"],
        "tier_classification": cls["tier_classification"],
        "confidence": sidecar["confidence"],
        "output_md": md_path,
        "output_json": json_path,
        "duration_s": duration_s,
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
