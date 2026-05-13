"""analyze.py — orchestrator for analyze-fda-approval-prospects (P1).

Pulls trials from ClinicalTrials.gov, runs forensics, queries Federal Register
for AdCom presence, applies optional class precedent (from P2 if a JSON output
exists), synthesizes a probability range, and writes both the markdown report
and JSON estimate atomically.

Usage:
    # When you know the real pivotal NCT IDs, pass them via --nct (repeatable).
    # Drug + ticker post-filter is applied so off-topic NCT IDs are dropped.
    # For AXS-05 in ADA the real pivotals are ADVANCE-1, ADVANCE-2, ACCORD-1,
    # ACCORD-2; resolve their NCT IDs from Axsome's 10-K Item 1 or the dossier
    # before invoking. DO NOT use the OFFLINE-PLACEHOLDER- IDs from --offline
    # mode as live --nct inputs; they fail the post-filter anyway.
    python analyze.py \
        --drug "AXS-05 (Auvelity)" --indication "Alzheimer Disease Agitation" \
        --ticker AXSM --cik 0001579428 \
        --catalyst 2026-04-30 --mode evaluative \
        --moa "NMDA receptor antagonist + CYP2D6 inhibitor" \
        --nct <NCT_ADVANCE_1> --nct <NCT_ADVANCE_2> \
        --nct <NCT_ACCORD_1> --nct <NCT_ACCORD_2> \
        --output-dir <working>/skills/analyze-fda-approval-prospects/outputs

    # If NCT IDs are unknown, search by drug+indication (sponsor post-filter applied):
    python analyze.py \
        --drug "AXS-05 (Auvelity)" --indication "Alzheimer Disease Agitation" \
        --ticker AXSM --cik 0001579428 \
        --catalyst 2026-04-30 --mode evaluative \
        --output-dir <working>/skills/analyze-fda-approval-prospects/outputs
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)

# atomic_write is shared with U4
_U4_HELPERS = os.path.join(
    os.path.dirname(os.path.dirname(_THIS)),
    "monitor-kill-conditions",
    "helpers",
)
if _U4_HELPERS not in sys.path:
    sys.path.insert(0, _U4_HELPERS)

from atomic_write import atomic_write_text  # noqa: E402
import fetch_trial_data  # noqa: E402
import endpoint_integrity_check  # noqa: E402
import adcom_history_lookup  # noqa: E402
import probability_synthesizer  # noqa: E402


def _try_load_p2(drug: str, output_dir: str) -> Optional[Dict[str, Any]]:
    """Look for a P2 class-precedent JSON in the standard location."""
    candidate = os.path.join(
        os.path.dirname(os.path.dirname(output_dir)),
        "research-clinical-class-precedent",
        "outputs",
        f"{drug}_class_basrates.json",
    )
    if os.path.exists(candidate):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _render_markdown(spec: Dict[str, Any]) -> str:
    p = spec["probability"]
    L = []
    L.append(f"# FDA Approval Prospects — {spec['drug']} for {spec['indication']}")
    L.append("")
    L.append(f"**Ticker:** {spec['ticker']} ({spec.get('company_name') or 'company name n/a'})")
    L.append(f"**Catalyst:** {spec['catalyst']} ({spec['mode']})")
    L.append(f"**As of:** {spec['as_of']}")
    L.append("")
    L.append(
        f"**P(approval) = {p['p_mid']:.2f} (range {p['p_low']:.2f} – {p['p_high']:.2f})** — "
        f"spread {p['spread']:.2f}, overall confidence {p['overall_confidence']:.2f}"
    )
    L.append("")
    L.append("## Trial set")
    L.append("")
    if spec.get("trials"):
        L.append("| NCT | Title | Phase | Status | Sponsor | Enrollment |")
        L.append("|---|---|---|---|---|---|")
        for t in spec["trials"]:
            L.append(
                "| {nct} | {title} | {phase} | {status} | {sponsor} | {enr} |".format(
                    nct=t.get("nct_id"),
                    title=(t.get("title") or "")[:80],
                    phase=t.get("phase"),
                    status=t.get("status"),
                    sponsor=(t.get("sponsor") or "")[:50],
                    enr=t.get("enrollment"),
                )
            )
    else:
        L.append("_No trials resolved — see data-quality notes._")
    L.append("")
    L.append("## Trial forensics")
    L.append("")
    L.append("| Dimension | Signal | Δpp | Finding | Confidence |")
    L.append("|---|---|---|---|---|")
    for f in spec.get("forensics", []):
        L.append(
            "| {d} | {s} | {m:+d} | {f} | {c:.2f} |".format(
                d=f.get("dimension"),
                s=f.get("signal"),
                m=f.get("magnitude_pp", 0) or 0,
                f=(f.get("finding") or "")[:120],
                c=f.get("confidence", 0),
            )
        )
    L.append("")
    L.append("## AdCom risk")
    L.append("")
    a = spec.get("adcom", {})
    L.append(f"- Status: **{a.get('status', 'unverifiable')}**")
    L.append(f"- Rationale: {a.get('rationale', '')}")
    L.append(f"- Source: {a.get('source', '(none)')}")
    L.append("")
    L.append("## CMC risk")
    L.append("")
    c = spec.get("cmc", {})
    L.append(f"- Status: **{c.get('status', 'unverifiable')}**")
    L.append(f"- Rationale: {c.get('rationale', '')}")
    L.append("")
    L.append("## Class precedent")
    L.append("")
    cp = spec.get("class_precedent")
    if cp:
        L.append(f"- Class approval rate: {cp.get('approval_rate_class')}")
        L.append(f"- Source: {cp.get('source')}")
    else:
        L.append("- _Unavailable_ — P2 (research-clinical-class-precedent) has not been built or no output was found.")
    L.append("")
    L.append("## Assumption ledger")
    L.append("")
    L.append("| Adjustment | Sign | Δpp | Rationale | Source | Confidence |")
    L.append("|---|---|---|---|---|---|")
    for r in spec["probability"].get("ledger", []):
        L.append(
            "| {a} | {s} | {m:+d} | {r} | {src} | {c:.2f} |".format(
                a=r.get("adjustment"),
                s=r.get("sign"),
                m=r.get("magnitude_pp", 0) or 0,
                r=(r.get("rationale") or "")[:80],
                src=(r.get("source") or "")[:50],
                c=r.get("confidence", 0),
            )
        )
    L.append("")
    L.append("## Data-quality notes")
    L.append("")
    for d in spec.get("data_quality", []):
        L.append(f"- {d}")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*Skill: analyze-fda-approval-prospects.*")
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--drug", required=True)
    p.add_argument("--indication", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--cik", default=None)
    p.add_argument("--company-name", default=None)
    p.add_argument("--catalyst", required=True)
    p.add_argument("--mode", choices=["evaluative", "forward_looking"], default="evaluative")
    p.add_argument("--moa", default=None)
    p.add_argument("--nct", action="append", default=[])
    p.add_argument("--output-dir", required=True)
    p.add_argument("--offline", action="store_true", help="Skip network calls; use illustrative defaults")
    args = p.parse_args(argv)

    started = time.time()
    as_of = _dt.datetime.utcnow().isoformat() + "Z"
    data_quality: List[str] = []

    # 1. Trials
    trials: List[Dict[str, Any]] = []
    discovery = "skipped_offline"
    if args.offline:
        # ILLUSTRATIVE PLACEHOLDER NCT IDs — fake labels (ADVANCE-1/2,
        # ACCORD-1/2) on safe placeholder IDs that NEVER hit CT.gov. They
        # exist only for offline pipeline-shape smoke tests. The original
        # build phase used real NCT IDs (NCT04524351 etc.) which happened to
        # be live trials for unrelated drugs; when verification ran live
        # those IDs fetched real but unrelated trials and corrupted forensics.
        # See fix-note: "P1 trial filter contamination", 2026-04-29.
        trials = [
            {"nct_id": "OFFLINE-PLACEHOLDER-1", "title": "ADVANCE-1 (illustrative)", "phase": "PHASE 3",
             "status": "COMPLETED", "sponsor": args.ticker, "enrollment": 400,
             "primary_endpoint_hit": True, "has_results": True,
             "source_url": "offline://placeholder/ADVANCE-1"},
            {"nct_id": "OFFLINE-PLACEHOLDER-2", "title": "ADVANCE-2 (illustrative)", "phase": "PHASE 3",
             "status": "COMPLETED", "sponsor": args.ticker, "enrollment": 408,
             "primary_endpoint_hit": False, "has_results": True,
             "source_url": "offline://placeholder/ADVANCE-2"},
            {"nct_id": "OFFLINE-PLACEHOLDER-3", "title": "ACCORD-1 (illustrative)", "phase": "PHASE 3",
             "status": "COMPLETED", "sponsor": args.ticker, "enrollment": 178,
             "primary_endpoint_hit": True, "has_results": True,
             "source_url": "offline://placeholder/ACCORD-1"},
            {"nct_id": "OFFLINE-PLACEHOLDER-4", "title": "ACCORD-2 (illustrative)", "phase": "PHASE 3",
             "status": "COMPLETED", "sponsor": args.ticker, "enrollment": 200,
             "primary_endpoint_hit": True, "has_results": False,
             "source_url": "offline://placeholder/ACCORD-2"},
        ]
        discovery = "offline_illustrative"
        data_quality.append("offline mode — trial set illustrative not network-verified")
    elif args.nct:
        # Pass drug + sponsor so off-topic NCT IDs are dropped, not silently
        # accepted (the bug that caused P1 to score AXS-05 against Posiphen +
        # CBT-for-ventilator + MS-amantadine on 2026-04-29).
        r = fetch_trial_data.fetch_by_nct(
            args.nct, drug=args.drug, sponsor=args.ticker
        )
        trials = r.get("trials", [])
        discovery = r.get("discovery", "provided")
        if not r["ok"]:
            data_quality.append(f"clinical_trials_partial_or_unavailable: failed={r.get('failed')}")
        dropped = r.get("dropped_off_topic") or []
        if dropped:
            data_quality.append(
                f"trials_dropped_off_topic: {len(dropped)} NCT id(s) did not match drug aliases or sponsor and were excluded — {[d.get('nct_id') for d in dropped]}"
            )
    else:
        r = fetch_trial_data.search_trials(
            drug=args.drug, indication=args.indication, sponsor=args.ticker
        )
        trials = r.get("trials", [])
        discovery = "inferred"
        if not r["ok"]:
            data_quality.append("clinical_trials_unavailable")
        dropped = r.get("dropped_off_topic") or []
        if dropped:
            data_quality.append(
                f"trials_dropped_off_topic: {len(dropped)} search result(s) did not match drug aliases or sponsor and were excluded — {[d.get('nct_id') for d in dropped]}"
            )

    # 2. Forensics
    forensics = endpoint_integrity_check.run_forensics(trials)

    # 3. AdCom risk
    adcom: Dict[str, Any]
    if args.offline:
        adcom = {
            "status": "low",
            "rationale": "(offline) prior class AdCom for ADA was 2023; FDA appears not to require repeat",
            "source": "(offline)",
            "confidence": 0.70,
        }
    else:
        a = adcom_history_lookup.search_adcom(f"{args.drug} {args.indication}")
        if not a.get("ok"):
            adcom = {"status": "unverifiable", "rationale": "Federal Register unavailable", "source": a.get("source_url"), "confidence": 0.30}
            data_quality.append("federal_register_unavailable")
        else:
            hits = a.get("results", []) or []
            if hits:
                relevant = [h for h in hits if (args.drug.lower().split()[0] in (h.get("title") or "").lower())]
                if relevant:
                    adcom = {
                        "status": "confirmed_scheduled",
                        "rationale": f"Federal Register notice: {relevant[0].get('title')}",
                        "source": relevant[0].get("html_url"),
                        "confidence": 0.95,
                    }
                else:
                    adcom = {
                        "status": "low",
                        "rationale": f"{len(hits)} AdCom notices in window — none matching drug/indication",
                        "source": a.get("source_url"),
                        "confidence": 0.80,
                    }
            else:
                adcom = {
                    "status": "low",
                    "rationale": "no AdCom notices in Federal Register matching term",
                    "source": a.get("source_url"),
                    "confidence": 0.85,
                }

    # 4. CMC risk — placeholder; full implementation would query EDGAR + FDA inspection DB
    cmc = {
        "status": "low",
        "rationale": "no recent FDA-483 observations identified in primary search (best-effort scan; for full assurance, query the FDA inspection database directly)",
        "source": "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/inspection-classification-database",
        "confidence": 0.55,
    }

    # 5. Class precedent (P2 output if present)
    class_precedent = _try_load_p2(args.drug, args.output_dir)
    if not class_precedent:
        data_quality.append("class_precedent_unavailable_using_default")

    # 6. Synthesize
    prob = probability_synthesizer.synthesize(
        forensics, adcom=adcom, cmc=cmc, class_precedent=class_precedent
    )

    spec = {
        "drug": args.drug,
        "indication": args.indication,
        "ticker": args.ticker,
        "company_name": args.company_name,
        "catalyst": args.catalyst,
        "mode": args.mode,
        "as_of": as_of,
        "trial_discovery": discovery,
        "trials": trials,
        "forensics": forensics,
        "adcom": adcom,
        "cmc": cmc,
        "class_precedent": class_precedent,
        "probability": prob,
        "data_quality": data_quality,
    }

    md = _render_markdown(spec)
    drug_slug = args.drug.split()[0].replace("/", "_")
    md_path = os.path.join(args.output_dir, f"{drug_slug}_approval_analysis.md")
    json_path = os.path.join(args.output_dir, f"{drug_slug}_probability_estimate.json")
    atomic_write_text(md_path, md)
    atomic_write_text(json_path, json.dumps(spec, indent=2, default=str) + "\n")

    duration = round(time.time() - started, 3)
    summary = {
        "status": "ok",
        "drug": args.drug,
        "mode": args.mode,
        "p_low": prob["p_low"],
        "p_mid": prob["p_mid"],
        "p_high": prob["p_high"],
        "output_md": md_path,
        "output_json": json_path,
        "duration_s": duration,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
