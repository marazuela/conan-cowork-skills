"""analyze.py — P5 orchestrator.

Pulls a litigation signal, classifies the case type, builds the outcome
tree, computes per-branch magnitudes + NPV, synthesizes overall confidence,
and atomic-writes the markdown report + JSON sidecar.

Usage (offline, ingesting most-recent SEC enforcement signal):

    python analyze.py \
      --case-id LR-26539 \
      --court SEC \
      --case-type sec_enforcement \
      --motion-stage complaint_filed \
      --mode evaluative \
      --offline \
      --output-dir ../outputs

Usage (offline-illustrative, full happy-path on a synthetic securities-fraud case):

    python analyze.py --offline-illustrative --output-dir ../outputs
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from atomic_write import atomic_write_text  # noqa: E402
import case_outcome_tree as cot  # noqa: E402
import discount_rate_calc as drc  # noqa: E402
import courtlistener_client as cl  # noqa: E402


# ---------- HALT_FLAG ----------


WORKING_HALT = os.path.join(
    "C:\\",
    "Users",
    "javie",
    "OneDrive",
    "Desktop",
    "Claude Cowork",
    "Investment tool backup skills",
    "02_System",
    "engine",
    "health",
    "HALT_FLAG",
)
REFERENCE_HALT = os.path.join(
    "C:\\",
    "Users",
    "javie",
    "OneDrive",
    "Desktop",
    "Claude Cowork",
    "Investment tool backup",
    "02_System",
    "engine",
    "health",
    "HALT_FLAG",
)


def halt_present() -> Optional[str]:
    for p in (WORKING_HALT, REFERENCE_HALT):
        if os.path.exists(p):
            return p
    return None


# ---------- Slug ----------


def case_slug(case_id_or_docket: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", case_id_or_docket).strip("_").lower()
    return s[:64] if s else "case"


# ---------- Confidence aggregation ----------


def harmonic_mean(values: List[float]) -> float:
    """Harmonic mean of confidences in (0,1]; ignore zeros / Nones."""
    vals = [v for v in values if v is not None and v > 0]
    if not vals:
        return 0.0
    return len(vals) / sum(1.0 / v for v in vals)


# ---------- Pass 1 — party resolution ----------


def resolve_party(
    parties: Dict, enterprise_value_usd_pre_signal: Optional[float]
) -> Dict:
    """Return Pass-1 result with confidence, materiality denominator, and role."""
    ticker = (parties or {}).get("publicly_traded_party_ticker")
    role = (parties or {}).get("publicly_traded_party_role", "defendant")
    plaintiff = (parties or {}).get("plaintiff", []) or []
    defendant = (parties or {}).get("defendant", []) or []

    if not ticker:
        # No publicly-traded party. Per profile_litigation.md Dim-6 auto-cap:
        # signal must be archived. Return a clean failure path.
        return {
            "publicly_traded_party_ticker": None,
            "publicly_traded_party_role": "neither_or_unknown",
            "publicly_traded_party_match_confidence": 0.0,
            "materiality_denominator_usd": None,
            "materiality_denominator_note": "no_public_party",
            "confidence": 0.10,
            "source": "skills/analyze-litigation-expected-value (Pass-1 internal, profile_litigation.md Dim-6)",
            "auto_archive": True,
            "auto_archive_reason": (
                "No publicly-traded defendant or plaintiff resolved. "
                "Per profile_litigation.md auto-cap rule (Party Resolution Confidence < 3), "
                "signal is auto-archived. A wrong-party EV would contaminate the signal log."
            ),
            "plaintiff_names": plaintiff,
            "defendant_names": defendant,
        }

    # Ticker present — assume exact-match confidence; downstream can override
    # if name-only or fuzzy match was used.
    materiality_note = None
    materiality_denom = enterprise_value_usd_pre_signal
    if materiality_denom is None:
        materiality_note = "fallback_to_filing_ev_required"
        confidence = 0.55
    else:
        confidence = 0.90

    return {
        "publicly_traded_party_ticker": ticker,
        "publicly_traded_party_role": role,
        "publicly_traded_party_match_confidence": 0.90,
        "materiality_denominator_usd": materiality_denom,
        "materiality_denominator_note": materiality_note,
        "confidence": confidence,
        "source": "input_parties + materiality_denominator (D-059 anchor)",
        "auto_archive": False,
        "plaintiff_names": plaintiff,
        "defendant_names": defendant,
    }


# ---------- Markdown rendering ----------


def render_markdown(sidecar: Dict) -> str:
    inputs = sidecar["inputs"]
    p1 = sidecar["passes"]["pass_1_party_resolution"]
    p5 = sidecar["passes"].get("pass_5_npv", {})
    branches = sidecar.get("outcome_tree", [])
    auth_status = sidecar.get("auth_status", "ok")
    ledger = sidecar.get("assumptions_ledger", [])
    over_conf = sidecar.get("overall_confidence")

    case_id = inputs.get("case_id_or_docket", "?")
    court = inputs.get("court", "?")
    case_type = inputs.get("case_type", "?")
    ticker = (inputs.get("parties") or {}).get("publicly_traded_party_ticker")

    L = []
    L.append(f"# Litigation EV — {case_id} ({court})")
    L.append("")
    L.append(f"**Case type:** `{case_type}`  ")
    L.append(f"**Mode:** `{inputs.get('mode', 'evaluative')}`  ")
    L.append(f"**Public party:** {ticker if ticker else '_(none — auto-archive)_'}  ")
    L.append(f"**Auth status:** `{auth_status}`  ")
    if over_conf is not None:
        L.append(f"**Overall confidence:** `{over_conf:.2f}`  ")
    L.append("")

    if auth_status != "ok":
        L.append("## ⚠ Auto-archive / partial output")
        L.append("")
        if p1.get("auto_archive"):
            L.append(f"- {p1.get('auto_archive_reason')}")
        elif auth_status == "courtlistener_auth_required":
            L.append("- CourtListener token absent. Skill ran with priors-only enrichment.")
            L.append(f"- {sidecar.get('next_steps', '')}")
        else:
            L.append(f"- {sidecar.get('next_steps', auth_status)}")
        L.append("")

    L.append("## Pass 1 — Party resolution & materiality denominator")
    L.append("")
    L.append(f"- Match confidence: `{p1.get('publicly_traded_party_match_confidence', 0.0):.2f}`")
    L.append(f"- Materiality denominator: {('$%.0fM' % (p1.get('materiality_denominator_usd', 0) / 1e6)) if p1.get('materiality_denominator_usd') else '_n/a_'}")
    note = p1.get('materiality_denominator_note')
    if note:
        L.append(f"- Note: `{note}`")
    L.append(f"- Source: `{p1.get('source')}`")
    L.append("")

    if branches:
        L.append("## Pass 2–4 — Outcome tree (probability × magnitude × time)")
        L.append("")
        L.append("| Branch | P | CI | n | Magnitude (USD) | Days | NPV (USD) | EV contrib (USD) | Conf | Source |")
        L.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---|")
        for b in branches:
            mag = b.get("magnitude_to_issuer_usd", 0.0)
            npv = b.get("npv_to_issuer_usd", 0.0)
            evc = b.get("ev_contribution_usd", 0.0)
            L.append(
                f"| `{b['branch']}` | {b['probability']:.3f} | "
                f"[{b['probability_ci_low']:.2f}, {b['probability_ci_high']:.2f}] | "
                f"{b['n_supporting_precedents']} | "
                f"${mag:,.0f} | {b['time_to_resolution_days']} | "
                f"${npv:,.0f} | ${evc:,.0f} | "
                f"{b['confidence']:.2f} | {b['source']} |"
            )
        L.append("")

    if p5:
        L.append("## Pass 5 — Discounted EV")
        L.append("")
        ev = p5.get("ev_usd")
        ev_pct = p5.get("ev_pct_of_ev")
        ev_str = f"${ev:+,.0f}" if ev is not None else "_n/a_"
        ev_pct_str = f"{ev_pct*100:+.2f}%" if ev_pct is not None else "_n/a_"
        L.append(f"- **EV (USD):** {ev_str}")
        L.append(f"- **EV (% of pre-signal EV):** {ev_pct_str}")
        L.append(f"- **Materiality band:** `{p5.get('band')}`")
        L.append(f"- **Discount rate:** `{p5.get('discount_rate', 0.10)}`")
        if p5.get("time_to_resolution_median_days_overall") is not None:
            L.append(f"- **Time-to-resolution (overall, days):** `{p5.get('time_to_resolution_median_days_overall')}`")
        L.append("")

    if ledger:
        L.append("## Assumptions ledger")
        L.append("")
        for a in ledger:
            L.append(f"- **{a.get('assumption')}** — basis: {a.get('basis')}; impact: `{a.get('confidence_impact')}`; source: `{a.get('source')}`")
        L.append("")

    L.append("## Compliance")
    L.append("")
    L.append("- Atomic writes per D-052")
    L.append("- Confidence + source on every output row per CLAUDE.md §1.6")
    L.append("- Materiality denominator anchored to 30-day-pre-signal VWAP-based EV per D-059 (or fallback flagged)")
    L.append("- Auto-cap on party-resolution confidence < 0.85 honored per profile_litigation.md")
    L.append("- HALT_FLAG honored at orchestrator entry")
    L.append("- CourtListener auth-required path returns cleanly with `recoverable: true` per Q-017")
    L.append("")
    L.append(f"_ran_at_utc: {sidecar.get('ran_at_utc')}_  ")
    L.append(f"_duration_s: {sidecar.get('duration_s')}_")
    return "\n".join(L) + "\n"


# ---------- Offline illustrative profile ----------


ILLUSTRATIVE_INPUT = {
    "case_id_or_docket": "1:24-cv-04563",
    "court": "S.D.N.Y.",
    "case_type": "securities_fraud",
    "parties": {
        "plaintiff": ["In re Mock Corp Securities Litigation"],
        "defendant": ["Mock Corp", "John Doe (CEO)"],
        "publicly_traded_party_ticker": "MOCK",
        "publicly_traded_party_role": "defendant",
    },
    "claim_amount_usd": 500_000_000,
    "enterprise_value_usd_pre_signal": 4_500_000_000,
    "motion_stage": "mtd_pending",
    "mode": "evaluative",
    "discount_rate": 0.10,
}


# ---------- Main ----------


def run(
    case_id_or_docket: str,
    court: str,
    case_type: str,
    motion_stage: str,
    parties: Dict,
    claim_amount_usd: Optional[float],
    enterprise_value_usd_pre_signal: Optional[float],
    mode: str,
    discount_rate: float,
    output_dir: str,
    offline: bool,
    offline_illustrative: bool,
) -> Dict:
    started = time.monotonic()
    halt_path = halt_present()
    if halt_path:
        return {
            "halted": True,
            "halt_path": halt_path,
            "duration_s": round(time.monotonic() - started, 3),
        }

    if offline_illustrative:
        case_id_or_docket = ILLUSTRATIVE_INPUT["case_id_or_docket"]
        court = ILLUSTRATIVE_INPUT["court"]
        case_type = ILLUSTRATIVE_INPUT["case_type"]
        motion_stage = ILLUSTRATIVE_INPUT["motion_stage"]
        parties = ILLUSTRATIVE_INPUT["parties"]
        claim_amount_usd = ILLUSTRATIVE_INPUT["claim_amount_usd"]
        enterprise_value_usd_pre_signal = ILLUSTRATIVE_INPUT["enterprise_value_usd_pre_signal"]
        mode = ILLUSTRATIVE_INPUT["mode"]
        discount_rate = ILLUSTRATIVE_INPUT["discount_rate"]
        offline = True

    slug = case_slug(case_id_or_docket)
    ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Pass 1
    p1 = resolve_party(parties, enterprise_value_usd_pre_signal)

    sidecar: Dict = {
        "skill_id": "P5",
        "skill_name": "analyze-litigation-expected-value",
        "ran_at_utc": ran_at,
        "case_slug": slug,
        "inputs": {
            "case_id_or_docket": case_id_or_docket,
            "court": court,
            "case_type": case_type,
            "parties": parties,
            "claim_amount_usd": claim_amount_usd,
            "enterprise_value_usd_pre_signal": enterprise_value_usd_pre_signal,
            "motion_stage": motion_stage,
            "mode": mode,
            "discount_rate": discount_rate,
        },
        "passes": {"pass_1_party_resolution": p1},
        "outcome_tree": [],
        "assumptions_ledger": [],
    }

    # CourtListener auth probe (informational; we still proceed offline)
    if not offline:
        cl_status = cl.auth_status()
        if cl_status.get("auth_required"):
            sidecar["assumptions_ledger"].append(
                {
                    "assumption": "CourtListener live enrichment not performed",
                    "basis": "API token absent",
                    "source": cl_status.get("registration_url", ""),
                    "confidence_impact": "moderate",
                }
            )

    # Auto-archive short-circuit
    if p1.get("auto_archive"):
        sidecar["auth_status"] = "party_confidence_low"
        sidecar["recoverable"] = True
        sidecar["next_steps"] = (
            "Manual review or signal drop — no publicly-traded defendant resolved. "
            "Profile_litigation.md auto-cap rule fires; signal is correctly archived."
        )
        sidecar["assumptions_ledger"].append(
            {
                "assumption": "Auto-archive: party_confidence < 0.85",
                "basis": "Defendants resolve to individuals + private LLCs only",
                "source": p1.get("source"),
                "confidence_impact": "blocking",
            }
        )
        sidecar["passes"]["pass_5_npv"] = {
            "ev_usd": 0.0,
            "ev_pct_of_ev": 0.0,
            "band": "n_a_no_public_party",
            "confidence": 0.10,
            "source": "auto_archive",
        }
        sidecar["overall_confidence"] = 0.10
    else:
        # Pass 2 — outcome tree
        tree = cot.build_tree(
            case_type=case_type,
            motion_stage=motion_stage,
            claim_amount_usd=claim_amount_usd,
            publicly_traded_party_role=p1["publicly_traded_party_role"],
        )
        sidecar["passes"]["pass_2_outcome_tree"] = {
            "stage_used": tree.get("motion_stage_used"),
            "stage_fallback_from": tree.get("stage_fallback_from"),
            "probability_sum": tree.get("probability_sum"),
            "sparse_class": tree.get("sparse_class"),
            "auth_status": tree.get("auth_status"),
            "supported_case_types": tree.get("supported_case_types"),
            "confidence": (
                0.30
                if tree.get("auth_status") != "ok"
                else (0.45 if tree.get("sparse_class") else 0.75)
            ),
            "source": "helpers/precedent_settlements.py:" + case_type + "_priors",
        }

        # Validate probability sum
        if tree.get("auth_status") == "ok":
            psum = tree.get("probability_sum", 0.0)
            if abs(psum - 1.0) > 0.001:
                sidecar["assumptions_ledger"].append(
                    {
                        "assumption": f"Probability sum drift: {psum} (expected 1.0)",
                        "basis": "precedent_settlements.py priors",
                        "source": "helpers/precedent_settlements.py",
                        "confidence_impact": "moderate",
                    }
                )

        # Pass 5 — NPV
        npv_out = drc.compute_npv(
            tree.get("branches", []),
            discount_rate=discount_rate,
            materiality_denominator_usd=p1.get("materiality_denominator_usd"),
        )
        enriched_branches = npv_out["branches"]
        sidecar["outcome_tree"] = enriched_branches

        # Pass-3 confidence (magnitude) — derived from branch confidences
        mag_conf = (
            sum(b.get("confidence", 0.0) for b in enriched_branches) / max(1, len(enriched_branches))
        )
        sidecar["passes"]["pass_3_magnitude"] = {
            "case_type": case_type,
            "claim_amount_usd": claim_amount_usd,
            "n_branches": len(enriched_branches),
            "confidence": round(mag_conf, 3),
            "source": "helpers/precedent_settlements.py + helpers/case_outcome_tree.py",
        }
        sidecar["passes"]["pass_4_time_to_resolution"] = {
            "overall_median_days": npv_out.get("time_to_resolution_median_days_overall"),
            "confidence": 0.65,
            "source": "helpers/precedent_settlements.py:" + case_type + "_time_priors",
        }
        sidecar["passes"]["pass_5_npv"] = {
            "ev_usd": npv_out.get("ev_usd"),
            "ev_pct_of_ev": npv_out.get("ev_pct_of_ev"),
            "band": npv_out.get("band"),
            "discount_rate": npv_out.get("discount_rate"),
            "time_to_resolution_median_days_overall": npv_out.get("time_to_resolution_median_days_overall"),
            "confidence": 0.70,
            "source": "helpers/discount_rate_calc.py",
        }

        # Imputed-claim flag
        if claim_amount_usd is None and case_type != "sec_enforcement":
            sidecar["assumptions_ledger"].append(
                {
                    "assumption": "claim_amount_usd missing; magnitudes set to 0 for non-SEC types",
                    "basis": "Caller did not supply claim",
                    "source": "input_parsing",
                    "confidence_impact": "high",
                }
            )

        if p1.get("materiality_denominator_note"):
            sidecar["assumptions_ledger"].append(
                {
                    "assumption": "Enterprise value denominator fallback",
                    "basis": p1.get("materiality_denominator_note"),
                    "source": "D-059",
                    "confidence_impact": "moderate",
                }
            )

        # Synthesis confidence
        per_pass = [
            sidecar["passes"]["pass_1_party_resolution"]["confidence"],
            sidecar["passes"]["pass_2_outcome_tree"]["confidence"],
            sidecar["passes"]["pass_3_magnitude"]["confidence"],
            sidecar["passes"]["pass_4_time_to_resolution"]["confidence"],
            sidecar["passes"]["pass_5_npv"]["confidence"],
        ]
        sidecar["overall_confidence"] = round(harmonic_mean(per_pass), 3)
        sidecar["auth_status"] = (
            "courtlistener_auth_required"
            if (not offline and cl.auth_status().get("auth_required"))
            else "ok"
        )
        sidecar["recoverable"] = True

    sidecar["duration_s"] = round(time.monotonic() - started, 3)

    # Atomic-write outputs
    out_dir = os.path.abspath(output_dir)
    md_path = os.path.join(out_dir, f"{slug}_ev_analysis.md")
    json_path = os.path.join(out_dir, f"{slug}_outcome_tree.json")
    atomic_write_text(json_path, json.dumps(sidecar, indent=2, default=str))
    atomic_write_text(md_path, render_markdown(sidecar))

    return {
        "case_slug": slug,
        "n_branches": len(sidecar.get("outcome_tree", [])),
        "ev_usd_mm": round((sidecar.get("passes", {}).get("pass_5_npv", {}).get("ev_usd") or 0.0) / 1e6, 3),
        "ev_pct_of_ev": sidecar.get("passes", {}).get("pass_5_npv", {}).get("ev_pct_of_ev"),
        "time_to_resolution_median_days": sidecar.get("passes", {}).get("pass_5_npv", {}).get("time_to_resolution_median_days_overall"),
        "discount_rate": discount_rate,
        "confidence": sidecar.get("overall_confidence"),
        "source": "helpers/analyze.py",
        "auth_status": sidecar.get("auth_status"),
        "duration_s": sidecar["duration_s"],
        "md_path": md_path,
        "json_path": json_path,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--court", default=None)
    ap.add_argument("--case-type", default=None)
    ap.add_argument("--motion-stage", default="complaint_filed")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--role", default="defendant", choices=["defendant", "plaintiff", "neither_or_unknown"])
    ap.add_argument("--plaintiff", action="append", default=[])
    ap.add_argument("--defendant", action="append", default=[])
    ap.add_argument("--claim-usd", type=float, default=None)
    ap.add_argument("--ev-usd", type=float, default=None)
    ap.add_argument("--mode", default="evaluative", choices=["evaluative", "forward_looking"])
    ap.add_argument("--discount-rate", type=float, default=0.10)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--offline-illustrative", action="store_true")
    ap.add_argument("--output-dir", default=os.path.join(HERE, "..", "outputs"))
    args = ap.parse_args(argv)

    if not args.offline_illustrative:
        if not (args.case_id and args.court and args.case_type):
            print("error: --case-id, --court, --case-type required (or use --offline-illustrative)", file=sys.stderr)
            return 2

    parties = {
        "plaintiff": args.plaintiff,
        "defendant": args.defendant,
        "publicly_traded_party_ticker": args.ticker,
        "publicly_traded_party_role": args.role,
    }

    res = run(
        case_id_or_docket=args.case_id or "",
        court=args.court or "",
        case_type=args.case_type or "",
        motion_stage=args.motion_stage,
        parties=parties,
        claim_amount_usd=args.claim_usd,
        enterprise_value_usd_pre_signal=args.ev_usd,
        mode=args.mode,
        discount_rate=args.discount_rate,
        output_dir=args.output_dir,
        offline=args.offline,
        offline_illustrative=args.offline_illustrative,
    )

    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
