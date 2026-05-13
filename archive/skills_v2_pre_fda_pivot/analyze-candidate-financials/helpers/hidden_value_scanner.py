"""hidden_value_scanner.py — Surface assets where carrying ≠ market value.

Scans 10-K-extracted balance-sheet and footnote data for items that may carry
hidden value (or hidden risk). Outputs a structured ledger of candidate
hidden-value items with confidence and source per item.

Inputs (JSON via --input-json or --stdin)
-----------------------------------------
{
  "ticker": "RPAY",
  "fiscal_year_end": "2025-12-31",
  "tenK_accession": "0001193125-26-098518",
  "items": {
    "real_estate_carrying_usd_mm": 4.5,
    "real_estate_locations": ["Atlanta GA HQ", "Toronto ON"],
    "intangibles_gross_usd_mm": 240.0,
    "intangibles_amortization_avg_life_yrs": 8,
    "intangibles_net_usd_mm": 180.0,
    "deferred_tax_assets_gross_usd_mm": 35.0,
    "deferred_tax_valuation_allowance_usd_mm": 28.0,
    "equity_method_investments_carrying_usd_mm": 0.0,
    "vie_max_exposure_usd_mm": 0.0,
    "operating_lease_rou_usd_mm": 12.0,
    "pension_funded_status_pct": null,
    "purchase_obligations_usd_mm": 5.0
  }
}

Outputs JSON ledger with one entry per item, atomic write if --output-path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile


def scan_real_estate(items: dict, source: str) -> dict | None:
    re_val = items.get("real_estate_carrying_usd_mm")
    if re_val is None:
        return None
    locs = items.get("real_estate_locations", [])
    return {
        "category": "real_estate",
        "carrying_usd_mm": re_val,
        "estimated_market_usd_mm": None,
        "estimate_method": "requires_appraisal_or_local_comp",
        "locations": locs,
        "confidence": 0.3 if re_val > 0 else 1.0,
        "source": source,
        "note": (
            "Real-estate market value cannot be estimated without local comp. "
            "Requires manual appraisal or county-record lookup."
            if re_val > 0
            else "no material real-estate holdings"
        ),
    }


def scan_intangibles(items: dict, source: str) -> dict | None:
    net = items.get("intangibles_net_usd_mm")
    if net is None:
        return None
    return {
        "category": "intangibles_and_goodwill",
        "net_carrying_usd_mm": net,
        "amortization_avg_life_yrs": items.get("intangibles_amortization_avg_life_yrs"),
        "interpretation": (
            "Intangibles often carry FAR below replacement cost (brand, customer "
            "lists). Conversely, goodwill from past acquisitions is a candidate "
            "for impairment if revenue/profit is declining."
        ),
        "confidence": 0.95,
        "source": source,
    }


def scan_dta(items: dict, source: str) -> dict | None:
    gross = items.get("deferred_tax_assets_gross_usd_mm")
    va = items.get("deferred_tax_valuation_allowance_usd_mm", 0.0) or 0.0
    if gross is None:
        return None
    realizable = max(gross - va, 0)
    flag = "potential_unlock" if va > 0 and gross > 10 else "none"
    return {
        "category": "deferred_tax_assets",
        "gross_dta_usd_mm": gross,
        "valuation_allowance_usd_mm": va,
        "realizable_today_usd_mm": realizable,
        "flag": flag,
        "interpretation": (
            "Material DTAs with full valuation allowance can become live if "
            "the firm returns to durable profitability — a quiet hidden asset."
            if flag == "potential_unlock"
            else "DTA position not material or fully recognized"
        ),
        "confidence": 1.0,
        "source": source,
    }


def scan_equity_method(items: dict, source: str) -> dict | None:
    val = items.get("equity_method_investments_carrying_usd_mm")
    if val is None:
        return None
    return {
        "category": "unconsolidated_affiliates",
        "carrying_usd_mm": val,
        "interpretation": (
            "Equity-method investments may carry materially below fair value. "
            "Compare carrying to share-of-earnings or recent transactions in "
            "underlying entity."
            if val > 0
            else "no material equity-method investments"
        ),
        "confidence": 1.0,
        "source": source,
    }


def scan_vies(items: dict, source: str) -> dict | None:
    val = items.get("vie_max_exposure_usd_mm")
    if val is None:
        return None
    return {
        "category": "variable_interest_entities",
        "max_exposure_usd_mm": val,
        "interpretation": (
            "VIE exposure is downside-risk, not upside-value. Ensure max "
            "exposure is fully reserved and disclosed."
            if val > 0
            else "no material VIE exposure"
        ),
        "confidence": 1.0,
        "source": source,
    }


def scan_lease_rou(items: dict, source: str) -> dict | None:
    val = items.get("operating_lease_rou_usd_mm")
    if val is None:
        return None
    return {
        "category": "operating_lease_rou",
        "carrying_usd_mm": val,
        "interpretation": (
            "Post-ASC 842, operating leases are on balance sheet — not hidden. "
            "Captured here for completeness; relevant for adjusted leverage."
        ),
        "confidence": 1.0,
        "source": source,
    }


def scan_pension(items: dict, source: str) -> dict | None:
    pct = items.get("pension_funded_status_pct")
    if pct is None:
        return None
    if pct < 80:
        flag = "underfunded_liability"
    elif pct > 110:
        flag = "excess_assets_potential_value"
    else:
        flag = "neutral"
    return {
        "category": "pension_funding",
        "funded_status_pct": pct,
        "flag": flag,
        "confidence": 1.0,
        "source": source,
    }


def scan_purchase_obligations(items: dict, source: str) -> dict | None:
    val = items.get("purchase_obligations_usd_mm")
    if val is None:
        return None
    return {
        "category": "purchase_obligations",
        "carrying_usd_mm": val,
        "interpretation": "Off-balance-sheet purchase obligations from commitments note",
        "confidence": 0.9,
        "source": source,
    }


SCANNERS = [
    scan_real_estate,
    scan_intangibles,
    scan_dta,
    scan_equity_method,
    scan_vies,
    scan_lease_rou,
    scan_pension,
    scan_purchase_obligations,
]


def atomic_write(path: str, content: str) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dir_, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def main() -> int:
    p = argparse.ArgumentParser(description="Hidden value scanner")
    p.add_argument("--input-json")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--output-path")
    args = p.parse_args()

    try:
        if args.stdin:
            payload = json.load(sys.stdin)
        elif args.input_json:
            with open(args.input_json, encoding="utf-8") as f:
                payload = json.load(f)
        else:
            print(json.dumps({"status": "error", "error_class": "no_input"}))
            return 1
        ticker = payload.get("ticker", "UNKNOWN")
        items = payload.get("items", {})
        accession = payload.get("tenK_accession", "")
        fye = payload.get("fiscal_year_end", "")
    except (KeyError, json.JSONDecodeError, FileNotFoundError) as e:
        print(json.dumps({"status": "error", "error_class": e.__class__.__name__}))
        return 1

    source = f"10-K accession {accession}, fiscal year ended {fye}"
    results = []
    for scanner in SCANNERS:
        r = scanner(items, source)
        if r is not None:
            results.append(r)

    out = {
        "status": "ok",
        "ticker": ticker,
        "fiscal_year_end": fye,
        "tenK_accession": accession,
        "items": results,
        "summary": {
            "total_categories_scanned": len(SCANNERS),
            "categories_with_data": len(results),
            "flagged": [r["category"] for r in results if r.get("flag") and r["flag"] not in ("none", "neutral")],
        },
        "confidence": 0.8,
        "source": "computed via hidden_value_scanner.py",
    }

    s = json.dumps(out, indent=2)
    if args.output_path:
        atomic_write(args.output_path, s)
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
