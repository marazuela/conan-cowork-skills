"""capital_allocation_scorecard.py — 5-10 year capital allocation grading.

Grades a company's capital allocation across four levers: buybacks, M&A,
dividends, net issuance. Inputs come from cash-flow statement, 8-K M&A
disclosures, and price history.

Inputs
------
JSON dict via --input-json or --stdin:

{
  "ticker": "RPAY",
  "lookback_years": 5,
  "current_share_price": 4.05,
  "yearly_data": [
    {
      "fiscal_year": 2021,
      "buybacks_usd_mm": 5.0,
      "weighted_avg_buyback_price": 23.0,
      "stock_issuance_usd_mm": 8.0,
      "dividends_paid_usd_mm": 0.0,
      "fcf_usd_mm": 60.0,
      "acquisitions_count": 1,
      "acquisitions_consideration_usd_mm": 75.0,
      "goodwill_impairment_usd_mm": 0.0
    },
    ...
  ]
}

Output: JSON with grade A-F, per-lever sub-scores, narrative rationale,
confidence, source.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile


def grade_buybacks(yearly: list[dict], current_price: float) -> dict:
    """Score buyback discipline by IRR vs current price."""
    total_spent = 0.0
    weighted_price_sum = 0.0
    for y in yearly:
        amount = y.get("buybacks_usd_mm", 0.0) or 0.0
        price = y.get("weighted_avg_buyback_price", 0.0) or 0.0
        total_spent += amount
        weighted_price_sum += amount * price
    if total_spent <= 0:
        return {
            "total_spent_usd_mm": 0.0,
            "weighted_avg_price": None,
            "current_price": current_price,
            "implied_return_pct": None,
            "grade": "N/A",
            "note": "no buybacks in window",
        }
    weighted_avg_price = weighted_price_sum / total_spent
    implied_return = (current_price - weighted_avg_price) / weighted_avg_price * 100.0
    if implied_return > 30:
        g = "A"
    elif implied_return > 0:
        g = "B"
    elif implied_return > -20:
        g = "C"
    elif implied_return > -50:
        g = "D"
    else:
        g = "F"
    return {
        "total_spent_usd_mm": round(total_spent, 2),
        "weighted_avg_price": round(weighted_avg_price, 4),
        "current_price": current_price,
        "implied_return_pct": round(implied_return, 2),
        "grade": g,
    }


def grade_ma(yearly: list[dict]) -> dict:
    total_consid = 0.0
    total_count = 0
    total_impair = 0.0
    for y in yearly:
        total_consid += y.get("acquisitions_consideration_usd_mm", 0.0) or 0.0
        total_count += y.get("acquisitions_count", 0) or 0
        total_impair += y.get("goodwill_impairment_usd_mm", 0.0) or 0.0
    if total_consid == 0:
        return {
            "deal_count": 0,
            "total_consideration_usd_mm": 0.0,
            "goodwill_impairment_usd_mm": 0.0,
            "impairment_ratio": None,
            "grade": "N/A",
            "note": "no acquisitions in window",
        }
    impair_ratio = total_impair / total_consid
    if impair_ratio == 0:
        g = "B"  # default neutral; A reserved for proven ROIC (not measurable here)
    elif impair_ratio < 0.10:
        g = "C"
    elif impair_ratio < 0.30:
        g = "D"
    else:
        g = "F"
    return {
        "deal_count": total_count,
        "total_consideration_usd_mm": round(total_consid, 2),
        "goodwill_impairment_usd_mm": round(total_impair, 2),
        "impairment_ratio": round(impair_ratio, 4),
        "grade": g,
    }


def grade_dividends(yearly: list[dict]) -> dict:
    total_div = sum((y.get("dividends_paid_usd_mm", 0.0) or 0.0) for y in yearly)
    total_fcf = sum((y.get("fcf_usd_mm", 0.0) or 0.0) for y in yearly)
    if total_div == 0:
        return {
            "total_dividends_usd_mm": 0.0,
            "fcf_coverage_ratio": None,
            "grade": "N/A",
            "note": "no dividends in window",
        }
    if total_fcf <= 0:
        return {
            "total_dividends_usd_mm": round(total_div, 2),
            "fcf_coverage_ratio": None,
            "grade": "F",
            "note": "dividends paid with negative cumulative FCF",
        }
    coverage = total_fcf / total_div  # >1 = covered
    if coverage > 2.5:
        g = "A"
    elif coverage > 1.5:
        g = "B"
    elif coverage > 1.0:
        g = "C"
    else:
        g = "D"
    return {
        "total_dividends_usd_mm": round(total_div, 2),
        "fcf_coverage_ratio": round(coverage, 4),
        "grade": g,
    }


def grade_issuance(yearly: list[dict]) -> dict:
    total_buyback = sum((y.get("buybacks_usd_mm", 0.0) or 0.0) for y in yearly)
    total_issuance = sum((y.get("stock_issuance_usd_mm", 0.0) or 0.0) for y in yearly)
    net = total_buyback - total_issuance
    if total_issuance == 0 and total_buyback == 0:
        return {"net_issuance_usd_mm": 0.0, "grade": "N/A"}
    if net > 0:
        g = "A" if net > total_issuance else "B"
    elif net == 0:
        g = "C"
    elif abs(net) < total_buyback:
        g = "D"
    else:
        g = "F"
    return {
        "net_issuance_usd_mm": round(net, 2),
        "total_buybacks_usd_mm": round(total_buyback, 2),
        "total_issuance_usd_mm": round(total_issuance, 2),
        "grade": g,
    }


GRADE_TO_NUM = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0, "N/A": None}


def composite_grade(*subs: dict) -> str:
    nums = []
    for s in subs:
        g = s.get("grade")
        if g and g in GRADE_TO_NUM and GRADE_TO_NUM[g] is not None:
            nums.append(GRADE_TO_NUM[g])
    if not nums:
        return "N/A"
    avg = sum(nums) / len(nums)
    if avg >= 3.5:
        return "A"
    if avg >= 2.5:
        return "B"
    if avg >= 1.5:
        return "C"
    if avg >= 0.75:
        return "D"
    return "F"


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
    p = argparse.ArgumentParser(description="Capital allocation scorecard")
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
        yearly = payload["yearly_data"]
        current_price = float(payload["current_share_price"])
    except (KeyError, json.JSONDecodeError, ValueError, FileNotFoundError) as e:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_class": e.__class__.__name__,
                    "error_msg": str(e),
                }
            )
        )
        return 1

    bb = grade_buybacks(yearly, current_price)
    ma = grade_ma(yearly)
    div = grade_dividends(yearly)
    iss = grade_issuance(yearly)
    overall = composite_grade(bb, ma, div, iss)

    out = {
        "status": "ok",
        "ticker": ticker,
        "lookback_years": payload.get("lookback_years", len(yearly)),
        "buyback_lever": bb,
        "ma_lever": ma,
        "dividend_lever": div,
        "net_issuance_lever": iss,
        "composite_grade": overall,
        "confidence": 0.8,
        "source": "computed via capital_allocation_scorecard.py",
    }

    s = json.dumps(out, indent=2)
    if args.output_path:
        atomic_write(args.output_path, s)
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
