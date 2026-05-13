"""sloan_accruals.py — Compute Sloan (1996) operating accruals ratio.

Purpose
-------
Operating accruals predict future returns negatively. High-accrual firms tend
to underperform; low-accrual firms tend to outperform. Used as one input
into the analyze-candidate-financials skill (universal §2.3).

Inputs
------
A JSON dict (or stdin JSON via --stdin) with these required keys for the
two most recent fiscal years:

{
  "ticker": "RPAY",
  "current_year": {
    "fiscal_year_end": "2025-12-31",
    "current_assets": 350.0,
    "cash_and_st_investments": 60.0,
    "current_liabilities": 280.0,
    "short_term_debt": 0.0,
    "taxes_payable": 5.0,
    "depreciation_amortization": 30.0,
    "total_assets": 920.0
  },
  "prior_year": {
    "fiscal_year_end": "2024-12-31",
    "current_assets": 320.0,
    "cash_and_st_investments": 55.0,
    "current_liabilities": 260.0,
    "short_term_debt": 0.0,
    "taxes_payable": 4.0,
    "total_assets": 880.0
  }
}

(All dollar values in $ millions or any consistent unit; ratio is unit-free.)

Output
------
JSON to stdout:

{
  "ticker": "RPAY",
  "accruals_usd_mm": -36.0,
  "avg_total_assets_usd_mm": 900.0,
  "accruals_ratio": -0.040,
  "decile_interpretation": "neutral_middle_8",
  "flag": "none",
  "confidence": 0.85,
  "source": "computed via sloan_accruals.py from input JSON",
  "computation_steps": { ... }
}

Atomic writes if --output-path is given (writes <path>.tmp then renames).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any


def compute_accruals(current: dict, prior: dict) -> dict:
    """Compute Sloan accruals from two fiscal-year balance sheets.

    Returns dict with:
      accruals_usd_mm: dollar accruals
      avg_total_assets_usd_mm: average TA across two years
      accruals_ratio: accruals / avg_TA
      computation_steps: explicit deltas for audit trail
    """
    delta_ca = current["current_assets"] - prior["current_assets"]
    delta_cash = current["cash_and_st_investments"] - prior["cash_and_st_investments"]
    delta_cl = current["current_liabilities"] - prior["current_liabilities"]
    delta_std = current["short_term_debt"] - prior["short_term_debt"]
    delta_tax = current["taxes_payable"] - prior["taxes_payable"]
    da = current["depreciation_amortization"]

    accruals = (delta_ca - delta_cash) - (delta_cl - delta_std - delta_tax) - da
    avg_ta = (current["total_assets"] + prior["total_assets"]) / 2.0
    if avg_ta <= 0:
        return {
            "error": "non_positive_avg_total_assets",
            "avg_total_assets_usd_mm": avg_ta,
        }
    ratio = accruals / avg_ta

    return {
        "accruals_usd_mm": round(accruals, 4),
        "avg_total_assets_usd_mm": round(avg_ta, 4),
        "accruals_ratio": round(ratio, 4),
        "computation_steps": {
            "delta_current_assets": round(delta_ca, 4),
            "delta_cash": round(delta_cash, 4),
            "delta_current_liabilities": round(delta_cl, 4),
            "delta_short_term_debt": round(delta_std, 4),
            "delta_taxes_payable": round(delta_tax, 4),
            "depreciation_amortization": round(da, 4),
            "formula": "((dCA - dCash) - (dCL - dSTD - dTaxes)) - DA",
        },
    }


def interpret_decile(ratio: float) -> tuple[str, str]:
    """Decile interpretation per Sloan convention.

    The strict deciles depend on cross-sectional sample. As a rule of thumb:
      ratio > +0.10  → top decile, strong red flag (high accruals → underperform)
      ratio < -0.10  → bottom decile, green flag
      otherwise     → neutral
    """
    if ratio > 0.10:
        return "top_decile_high_accruals", "red_flag"
    if ratio < -0.10:
        return "bottom_decile_low_accruals", "green_flag"
    return "neutral_middle_8", "none"


def atomic_write(path: str, content: str) -> None:
    """Atomic write: temp file in same dir, fsync, rename."""
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
    p = argparse.ArgumentParser(description="Compute Sloan operating accruals ratio")
    p.add_argument("--input-json", help="Path to input JSON file")
    p.add_argument("--stdin", action="store_true", help="Read JSON from stdin")
    p.add_argument("--output-path", help="Write result JSON to this path (atomic)")
    p.add_argument("--ticker", default=None, help="Override ticker in output")
    args = p.parse_args()

    try:
        if args.stdin:
            payload = json.load(sys.stdin)
        elif args.input_json:
            with open(args.input_json, encoding="utf-8") as f:
                payload = json.load(f)
        else:
            print(
                json.dumps(
                    {"status": "error", "error_class": "no_input", "recoverable": False}
                )
            )
            return 1

        ticker = args.ticker or payload.get("ticker", "UNKNOWN")
        current = payload["current_year"]
        prior = payload["prior_year"]
    except (KeyError, json.JSONDecodeError, FileNotFoundError) as e:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_class": e.__class__.__name__,
                    "error_msg": str(e),
                    "recoverable": False,
                }
            )
        )
        return 1

    result = compute_accruals(current, prior)
    if "error" in result:
        result["status"] = "error"
        result["recoverable"] = False
        print(json.dumps(result))
        return 1

    decile, flag = interpret_decile(result["accruals_ratio"])
    out: dict[str, Any] = {
        "status": "ok",
        "ticker": ticker,
        "accruals_usd_mm": result["accruals_usd_mm"],
        "avg_total_assets_usd_mm": result["avg_total_assets_usd_mm"],
        "accruals_ratio": result["accruals_ratio"],
        "decile_interpretation": decile,
        "flag": flag,
        "confidence": 0.85,
        "source": "computed via sloan_accruals.py from input balance sheet data",
        "computation_steps": result["computation_steps"],
    }

    payload_str = json.dumps(out, indent=2)
    if args.output_path:
        atomic_write(args.output_path, payload_str)
    print(payload_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
