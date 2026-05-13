"""endpoint_integrity_check.py

Light-weight forensic checks on a trial record, producing structured findings
that downstream synthesis can combine into a probability adjustment.

Each check returns a dict:
    {
        "dimension": "primary_endpoint_achievement" | "sap_integrity" |
                     "safety_profile" | "population" | "trial_design",
        "finding": str,
        "signal": "positive" | "neutral" | "negative",
        "magnitude_pp": int,           # suggested adjustment in percentage points
        "evidence": str,
        "source": str | None,
        "confidence": float in [0, 1]
    }

The functions are conservative — when evidence is sparse, they emit `neutral`
with low confidence rather than fabricating a finding.

Usage:
    from endpoint_integrity_check import run_forensics
    findings = run_forensics(trial_records)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List


def _check_primary_endpoint(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trials:
        return {
            "dimension": "primary_endpoint_achievement",
            "finding": "no trial records available",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "trial set empty",
            "source": None,
            "confidence": 0.20,
        }
    n_pivotal = 0
    n_positive = 0
    for t in trials:
        phase = (t.get("phase") or "").upper()
        if "PHASE3" in phase or "PHASE 3" in phase or "PHASE_3" in phase:
            n_pivotal += 1
            if t.get("primary_endpoint_hit"):
                n_positive += 1
    if n_pivotal == 0:
        return {
            "dimension": "primary_endpoint_achievement",
            "finding": "no Phase 3 trials in the resolved set",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "trial set lacks Phase 3 records",
            "source": None,
            "confidence": 0.30,
        }
    hit_rate = n_positive / n_pivotal
    if hit_rate >= 0.75:
        return {
            "dimension": "primary_endpoint_achievement",
            "finding": f"{n_positive}/{n_pivotal} Phase 3 trials hit primary endpoint",
            "signal": "positive",
            "magnitude_pp": 5,
            "evidence": f"hit rate {hit_rate:.2f}",
            "source": "ClinicalTrials.gov",
            "confidence": 0.85,
        }
    if hit_rate >= 0.5:
        return {
            "dimension": "primary_endpoint_achievement",
            "finding": f"{n_positive}/{n_pivotal} Phase 3 trials hit primary endpoint",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": f"hit rate {hit_rate:.2f}",
            "source": "ClinicalTrials.gov",
            "confidence": 0.75,
        }
    return {
        "dimension": "primary_endpoint_achievement",
        "finding": f"{n_positive}/{n_pivotal} Phase 3 trials hit primary endpoint",
        "signal": "negative",
        "magnitude_pp": -8,
        "evidence": f"hit rate {hit_rate:.2f}",
        "source": "ClinicalTrials.gov",
        "confidence": 0.85,
    }


def _check_sap_integrity(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    has_results = sum(1 for t in trials if t.get("has_results"))
    if has_results == 0:
        return {
            "dimension": "sap_integrity",
            "finding": "no trial in set has posted results — SAP integrity not verifiable from CT.gov alone",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "results-not-posted",
            "source": "ClinicalTrials.gov",
            "confidence": 0.30,
        }
    return {
        "dimension": "sap_integrity",
        "finding": f"{has_results} trial(s) have posted results — operator can verify SAP from registry",
        "signal": "neutral",
        "magnitude_pp": 0,
        "evidence": "registry results posted; full SAP review requires reading the CSR",
        "source": "ClinicalTrials.gov",
        "confidence": 0.50,
    }


def _check_population(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    enrolled = [t.get("enrollment") for t in trials if t.get("enrollment") is not None]
    if not enrolled:
        return {
            "dimension": "population",
            "finding": "enrollment counts unavailable",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "no enrollment field on trial records",
            "source": None,
            "confidence": 0.30,
        }
    total = sum(int(e) for e in enrolled if isinstance(e, int))
    if total >= 1500:
        return {
            "dimension": "population",
            "finding": f"aggregate enrollment {total} (≥1500) — strong statistical power",
            "signal": "positive",
            "magnitude_pp": 2,
            "evidence": "aggregate sample-size threshold met",
            "source": "ClinicalTrials.gov",
            "confidence": 0.70,
        }
    if total >= 500:
        return {
            "dimension": "population",
            "finding": f"aggregate enrollment {total} — moderate power",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "sample size adequate but not large",
            "source": "ClinicalTrials.gov",
            "confidence": 0.65,
        }
    return {
        "dimension": "population",
        "finding": f"aggregate enrollment {total} — small sample",
        "signal": "negative",
        "magnitude_pp": -3,
        "evidence": "low aggregate sample size raises type-II risk",
        "source": "ClinicalTrials.gov",
        "confidence": 0.65,
    }


def _check_design(trials: List[Dict[str, Any]]) -> Dict[str, Any]:
    multi_pivotal = sum(1 for t in trials if "PHASE 3" in (t.get("phase") or "").upper() or "PHASE3" in (t.get("phase") or "").upper())
    if multi_pivotal >= 2:
        return {
            "dimension": "trial_design",
            "finding": f"{multi_pivotal} pivotal trials — independent replication mitigates single-trial variance",
            "signal": "positive",
            "magnitude_pp": 3,
            "evidence": "multi-pivotal program",
            "source": "ClinicalTrials.gov",
            "confidence": 0.80,
        }
    if multi_pivotal == 1:
        return {
            "dimension": "trial_design",
            "finding": "single pivotal trial — no replication",
            "signal": "neutral",
            "magnitude_pp": 0,
            "evidence": "single-pivotal program; FDA has approved on single P3 (e.g., lecanemab CLARITY-AD) but it raises bar on data quality",
            "source": "ClinicalTrials.gov",
            "confidence": 0.70,
        }
    return {
        "dimension": "trial_design",
        "finding": "no pivotal trials identified in set",
        "signal": "neutral",
        "magnitude_pp": 0,
        "evidence": "phase data missing",
        "source": None,
        "confidence": 0.30,
    }


def run_forensics(trials: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        _check_primary_endpoint(trials),
        _check_sap_integrity(trials),
        _check_population(trials),
        _check_design(trials),
    ]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run forensic checks on a trial-records JSON file")
    p.add_argument("--input", required=True, help="Path to JSON file containing 'trials' list")
    p.add_argument("--out", default=None, help="Output JSON path (default stdout)")
    args = p.parse_args(argv)
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    findings = run_forensics(data.get("trials") or data.get("trial_set") or [])
    text = json.dumps({"findings": findings}, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
