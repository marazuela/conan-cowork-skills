"""sweep_active_dossiers.py — orchestrator for the monitor-kill-conditions skill.

Walks the read-only `<reference>/01_Opportunities/active/` folder, parses each
dossier, dispatches the appropriate profile-specific checker, aggregates the
results, and writes the markdown sweep report + JSONL action ledger to the
working folder.

Usage:
    python sweep_active_dossiers.py \
        --dossier-root "<reference>/01_Opportunities/active" \
        --output-dir "<working>/skills/monitor-kill-conditions/outputs" \
        --as-of 2026-04-29 \
        [--scope all_active|single] \
        [--dossier-id AXSM_ADA_PDUFA] \
        [--dry-run]

Constraints (per project CLAUDE.md):
  - Reads from --dossier-root (read-only).
  - Writes ONLY to --output-dir.
  - Atomic-write (temp + rename) for all outputs.
  - Confidence + source on every reported condition.
  - Exits 0 on success, 1 on hard error; prints structured JSON summary on
    final stdout line.

The orchestrator is bounded: it enforces a soft per-dossier deadline (default
12s) so that an 8-dossier sweep stays under the project 60s typical / 120s
hard caps.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

# Ensure the helpers directory is on sys.path when invoked directly.
_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)

from atomic_write import atomic_write_text  # noqa: E402
from dossier_parser import parse_dossier  # noqa: E402

# Profile checkers
import kill_checks_merger_arb  # noqa: E402
import kill_checks_activist_governance  # noqa: E402
import kill_checks_binary_catalyst  # noqa: E402
import kill_checks_litigation  # noqa: E402
import kill_checks_insider  # noqa: E402


_PROFILE_DISPATCH = {
    "merger_arb": kill_checks_merger_arb.check,
    "activist_governance": kill_checks_activist_governance.check,
    "binary_catalyst": kill_checks_binary_catalyst.check,
    "litigation": kill_checks_litigation.check,
    "insider": kill_checks_insider.check,
}


_SIGNAL_CATEGORY_TO_PROFILE = {
    "fda_pdufa": "binary_catalyst",
    "pdufa_binary": "binary_catalyst",
    "binary_catalyst": "binary_catalyst",
    "activist": "activist_governance",
    "governance": "activist_governance",
    "activism": "activist_governance",
    "takeover": "merger_arb",
    "merger": "merger_arb",
    "merger_arb": "merger_arb",
    "litigation": "litigation",
    "enforcement": "litigation",
    "insider": "insider",
}


def _infer_profile(fm: Dict[str, Any]) -> Optional[str]:
    if "scoring_profile" in fm and fm["scoring_profile"]:
        sp = str(fm["scoring_profile"]).strip().lower()
        if sp in _PROFILE_DISPATCH:
            return sp
    cat = str(fm.get("signal_category", "") or "").strip().lower()
    if cat in _SIGNAL_CATEGORY_TO_PROFILE:
        return _SIGNAL_CATEGORY_TO_PROFILE[cat]
    sig = str(fm.get("signal_type", "") or "").strip().lower()
    for key, prof in _SIGNAL_CATEGORY_TO_PROFILE.items():
        if key in sig:
            return prof
    return None


def _parse_iso_date(s) -> Optional[_dt.date]:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _universal_flags(fm: Dict[str, Any], as_of: _dt.date) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    last_updated = _parse_iso_date(fm.get("last_updated"))
    catalyst = _parse_iso_date(fm.get("primary_catalyst_date"))
    score = fm.get("score") or fm.get("score_total")
    if catalyst and (as_of - catalyst).days > 14 and last_updated and (as_of - last_updated).days > 14:
        flags.append(
            {
                "flag": "universal_catalyst_passed_no_update",
                "detail": f"catalyst={catalyst}, last_updated={last_updated}, as_of={as_of}",
            }
        )
    try:
        s = float(score) if score is not None else None
    except (TypeError, ValueError):
        s = None
    if s is not None and s < 20:
        flags.append({"flag": "universal_score_below_active_band", "detail": f"score={s} (<20)"})
    if last_updated and (as_of - last_updated).days > 30:
        flags.append({"flag": "universal_stale_dossier", "detail": f"last_updated={last_updated}"})
    status = str(fm.get("status", "") or "").strip().lower()
    if status and status != "active":
        flags.append({"flag": "universal_status_drift", "detail": f"status={status}"})
    return flags


def _aggregate_recommendation(
    triggered: List[Dict[str, Any]],
    cleared: List[Dict[str, Any]],
    unverifiable: List[Dict[str, Any]],
    universal_flags: List[Dict[str, Any]],
) -> str:
    high_conf_triggers = [t for t in triggered if (t.get("confidence") or 0) >= 0.85]
    converging = [t for t in triggered if (t.get("confidence") or 0) >= 0.60]
    if high_conf_triggers:
        return "archive"
    if len(converging) >= 2:
        kinds = {t.get("kind") for t in converging}
        if len(kinds) >= 2:
            return "archive"
    if any(f["flag"] in ("universal_score_below_active_band", "universal_stale_dossier") for f in universal_flags):
        return "de_rate"
    if any(t.get("status") == "unverifiable" for t in unverifiable):
        return "manual_review"
    return "hold"


def _check_halt_flag(reference_root: Optional[str]) -> Optional[str]:
    if not reference_root:
        return None
    candidate = os.path.join(reference_root, "02_System", "engine", "health", "HALT_FLAG")
    if os.path.exists(candidate):
        return candidate
    return None


def _process_dossier(
    dossier_dir: str,
    as_of: _dt.date,
    soft_deadline_s: float,
) -> Dict[str, Any]:
    started = time.time()
    dossier_id = os.path.basename(dossier_dir.rstrip(os.sep))
    md_path = os.path.join(dossier_dir, "dossier.md")
    parsed = parse_dossier(md_path)
    fm = parsed.get("frontmatter", {}) or {}
    profile = _infer_profile(fm)
    record: Dict[str, Any] = {
        "dossier_id": dossier_id,
        "dossier_path": md_path,
        "ticker": fm.get("ticker_local") or fm.get("ticker"),
        "company_name": fm.get("company_name_en") or fm.get("company_name_local"),
        "profile": profile,
        "score": fm.get("score") or fm.get("score_total"),
        "as_of": as_of.isoformat(),
        "data_quality": [],
        "universal_flags": [],
        "kill_conditions_section_present": parsed.get("kill_conditions_section_present", False),
        "checked_conditions": [],
        "primary_sources_consulted": [],
    }
    if not parsed.get("ok"):
        record["data_quality"].append({"issue": "parse_failed", "detail": parsed.get("error")})
        record["recommendation"] = "manual_review"
        record["duration_s"] = round(time.time() - started, 3)
        return record

    if not parsed.get("kill_conditions_section_present"):
        record["data_quality"].append({"issue": "kill_conditions_missing"})

    if profile is None:
        record["data_quality"].append({"issue": "profile_undetermined"})

    record["universal_flags"] = _universal_flags(fm, as_of)

    # Profile-specific checker
    conds = parsed.get("kill_conditions", []) or []
    checks: List[Dict[str, Any]] = []
    if profile and profile in _PROFILE_DISPATCH and conds:
        try:
            checks = _PROFILE_DISPATCH[profile](
                {"frontmatter": fm}, conds, as_of.isoformat()
            )
        except Exception as e:  # pragma: no cover — defensive
            record["data_quality"].append(
                {"issue": "checker_exception", "detail": f"{type(e).__name__}: {e}"}
            )

    # Aggregate sources actually consulted
    src_endpoints: set = set()
    for c in checks:
        if c.get("source_url"):
            try:
                host = c["source_url"].split("/")[2]
                src_endpoints.add(host)
            except Exception:
                pass
    record["primary_sources_consulted"] = sorted(src_endpoints)

    record["checked_conditions"] = checks
    triggered = [c for c in checks if c.get("status") == "triggered"]
    cleared = [c for c in checks if c.get("status") == "clear"]
    unverifiable = [c for c in checks if c.get("status") == "unverifiable"]
    manual = [c for c in checks if c.get("status") == "manual_review"]
    record["triggered_count"] = len(triggered)
    record["cleared_count"] = len(cleared)
    record["unverifiable_count"] = len(unverifiable)
    record["manual_review_count"] = len(manual)
    record["recommendation"] = _aggregate_recommendation(
        triggered, cleared, unverifiable + manual, record["universal_flags"]
    )

    # Soft deadline check (informational only)
    elapsed = time.time() - started
    if elapsed > soft_deadline_s:
        record["data_quality"].append({"issue": "soft_deadline_exceeded", "elapsed_s": round(elapsed, 3)})
    record["duration_s"] = round(elapsed, 3)
    return record


def _render_markdown(records: List[Dict[str, Any]], as_of: _dt.date) -> str:
    archive_n = sum(1 for r in records if r.get("recommendation") == "archive")
    derate_n = sum(1 for r in records if r.get("recommendation") == "de_rate")
    review_n = sum(1 for r in records if r.get("recommendation") == "manual_review")
    hold_n = sum(1 for r in records if r.get("recommendation") == "hold")

    lines: List[str] = []
    lines.append(f"# Kill-Condition Sweep — {as_of.isoformat()}")
    lines.append("")
    lines.append(f"**As of:** {as_of.isoformat()}T00:00:00Z")
    lines.append(f"**Dossiers processed:** {len(records)}")
    lines.append(f"**Kill recommendations (archive):** {archive_n}")
    lines.append(f"**De-rate recommendations:** {derate_n}")
    lines.append(f"**Manual-review flags:** {review_n}")
    lines.append(f"**Hold:** {hold_n}")
    lines.append("")
    lines.append("## Summary table")
    lines.append("")
    lines.append("| Dossier | Profile | Score | Triggered / Cleared / Unverifiable | Recommendation |")
    lines.append("|---|---|---|---|---|")
    for r in records:
        lines.append(
            "| {dossier} ({ticker}) | {profile} | {score} | {t} / {c} / {u}+{m} | {rec} |".format(
                dossier=r.get("dossier_id"),
                ticker=r.get("ticker") or "?",
                profile=r.get("profile") or "?",
                score=r.get("score") or "n/a",
                t=r.get("triggered_count", 0),
                c=r.get("cleared_count", 0),
                u=r.get("unverifiable_count", 0),
                m=r.get("manual_review_count", 0),
                rec=r.get("recommendation"),
            )
        )
    lines.append("")
    lines.append("## Per-dossier details")
    lines.append("")
    for r in records:
        lines.append(f"### {r.get('dossier_id')} ({r.get('ticker') or '?'} — {r.get('company_name') or 'company name n/a'})")
        lines.append("")
        lines.append(f"- **Profile:** {r.get('profile') or '?'}")
        lines.append(f"- **Score:** {r.get('score') or 'n/a'}")
        lines.append(f"- **Recommendation:** {r.get('recommendation')}")
        lines.append(f"- **Duration:** {r.get('duration_s')}s")
        lines.append(f"- **Primary sources consulted:** {', '.join(r.get('primary_sources_consulted', [])) or 'none'}")
        if r.get("universal_flags"):
            lines.append("")
            lines.append("**Universal flags:**")
            for f in r["universal_flags"]:
                lines.append(f"- `{f['flag']}` — {f.get('detail', '')}")
        if r.get("data_quality"):
            lines.append("")
            lines.append("**Data-quality issues:**")
            for d in r["data_quality"]:
                lines.append(f"- {d}")
        checks = r.get("checked_conditions", [])
        if checks:
            lines.append("")
            lines.append("| # | Condition (verbatim) | Kind | Status | Confidence | Evidence | Source |")
            lines.append("|---|---|---|---|---|---|---|")
            for c in checks:
                raw = (c.get("raw_text") or "").replace("|", "\\|")
                if len(raw) > 100:
                    raw = raw[:97] + "..."
                ev = (c.get("evidence") or "").replace("|", "\\|")
                if len(ev) > 100:
                    ev = ev[:97] + "..."
                src = c.get("source_url") or ""
                lines.append(
                    "| {i} | {raw} | {kind} | {status} | {conf} | {ev} | {src} |".format(
                        i=c.get("index"),
                        raw=raw,
                        kind=c.get("kind"),
                        status=c.get("status"),
                        conf=round(c.get("confidence") or 0, 2),
                        ev=ev,
                        src=src,
                    )
                )
        else:
            lines.append("")
            lines.append("_No structured conditions checked (kill_conditions section missing or profile undetermined)._")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Skill: monitor-kill-conditions. Confidence floor: 0.85 single-trigger, 0.60×2 converging.*")
    return "\n".join(lines) + "\n"


def _render_actions_jsonl(records: List[Dict[str, Any]], as_of: _dt.date, run_id: str) -> str:
    lines: List[str] = []
    for r in records:
        rec = {
            "as_of": as_of.isoformat(),
            "skill_run_id": run_id,
            "dossier_id": r.get("dossier_id"),
            "dossier_path": r.get("dossier_path"),
            "ticker": r.get("ticker"),
            "company_name": r.get("company_name"),
            "profile": r.get("profile"),
            "score": r.get("score"),
            "recommendation": r.get("recommendation"),
            "triggered_conditions": [
                c for c in r.get("checked_conditions", []) if c.get("status") == "triggered"
            ],
            "cleared_conditions": [
                c for c in r.get("checked_conditions", []) if c.get("status") == "clear"
            ],
            "unverifiable_conditions": [
                c for c in r.get("checked_conditions", []) if c.get("status") == "unverifiable"
            ],
            "manual_review_conditions": [
                c for c in r.get("checked_conditions", []) if c.get("status") == "manual_review"
            ],
            "universal_flags": r.get("universal_flags", []),
            "data_quality": r.get("data_quality", []),
            "duration_s": r.get("duration_s"),
            "primary_sources_consulted": r.get("primary_sources_consulted", []),
        }
        lines.append(json.dumps(rec, default=str))
    return "\n".join(lines) + ("\n" if lines else "")


def _render_archive_memo(record: Dict[str, Any], as_of: _dt.date) -> str:
    triggers = [c for c in record.get("checked_conditions", []) if c.get("status") == "triggered"]
    out = [
        f"# Archive Recommendation — {record.get('dossier_id')}",
        "",
        f"**As of:** {as_of.isoformat()}",
        f"**Ticker:** {record.get('ticker')} ({record.get('company_name')})",
        f"**Profile:** {record.get('profile')}",
        f"**Score:** {record.get('score')}",
        "",
        "## Firing kill criteria",
        "",
    ]
    for t in triggers:
        out.append(f"- **#{t.get('index')}** — {t.get('raw_text')}")
        out.append(f"  - Kind: `{t.get('kind')}`")
        out.append(f"  - Confidence: {round(t.get('confidence') or 0, 2)}")
        out.append(f"  - Evidence: {t.get('evidence')}")
        out.append(f"  - Source: {t.get('source_url') or '(none)'}")
    out.append("")
    out.append("## Suggested destination")
    out.append("")
    out.append(
        "- `delivered_recent/` if outcome is positive resolution (deal closed, FDA approval, judgment for plaintiff thesis-aligned)."
    )
    out.append(
        "- `candidates_archive/` if outcome is negative (deal terminated, CRL, dismissal). Pair with a brief lessons-learned memo."
    )
    out.append("")
    out.append("## Reminder")
    out.append("")
    out.append(
        "This skill never moves files in the reference folder. The actual archive move must be performed by an operator or a downstream task."
    )
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run kill-condition sweep across active dossiers")
    p.add_argument("--dossier-root", required=True, help="Path to active/ folder (read-only)")
    p.add_argument("--output-dir", required=True, help="Output directory in working folder")
    p.add_argument("--as-of", default=_dt.date.today().isoformat(), help="ISO date")
    p.add_argument("--scope", choices=["all_active", "single"], default="all_active")
    p.add_argument("--dossier-id", default=None, help="Required when --scope=single")
    p.add_argument("--reference-root", default=None, help="Optional reference root for HALT_FLAG check")
    p.add_argument("--soft-deadline", type=float, default=12.0, help="Per-dossier soft deadline in seconds")
    p.add_argument("--dry-run", action="store_true", help="Skip primary-source HTTP calls")
    args = p.parse_args(argv)

    halt = _check_halt_flag(args.reference_root)
    if halt:
        print(json.dumps({"status": "halted", "halt_flag": halt}))
        return 0

    if args.dry_run:
        os.environ["KILL_SWEEP_DRY_RUN"] = "1"

    as_of = _parse_iso_date(args.as_of) or _dt.date.today()
    run_id = uuid.uuid4().hex[:12]
    started = time.time()

    # Discover dossiers
    if not os.path.isdir(args.dossier_root):
        print(json.dumps({"status": "error", "error_class": "path_missing", "path": args.dossier_root}))
        return 1
    if args.scope == "single":
        if not args.dossier_id:
            print(json.dumps({"status": "error", "error_class": "missing_arg", "detail": "--dossier-id required when --scope=single"}))
            return 1
        targets = [os.path.join(args.dossier_root, args.dossier_id)]
    else:
        targets = []
        for name in sorted(os.listdir(args.dossier_root)):
            if name.startswith("_") or ".bak" in name or ".pre_" in name:
                continue
            d = os.path.join(args.dossier_root, name)
            if not os.path.isdir(d):
                continue
            if not os.path.exists(os.path.join(d, "dossier.md")):
                continue
            targets.append(d)

    records: List[Dict[str, Any]] = []
    for d in targets:
        rec = _process_dossier(d, as_of, args.soft_deadline)
        records.append(rec)

    md_path = os.path.join(args.output_dir, f"{as_of.isoformat()}_kill_sweep.md")
    jsonl_path = os.path.join(args.output_dir, f"{as_of.isoformat()}_actions.jsonl")
    md = _render_markdown(records, as_of)
    jl = _render_actions_jsonl(records, as_of, run_id)
    atomic_write_text(md_path, md)
    atomic_write_text(jsonl_path, jl)

    archive_recs = [r for r in records if r.get("recommendation") == "archive"]
    if archive_recs:
        arch_dir = os.path.join(args.output_dir, f"{as_of.isoformat()}_archive_recommendations")
        os.makedirs(arch_dir, exist_ok=True)
        for r in archive_recs:
            atomic_write_text(
                os.path.join(arch_dir, f"{r.get('dossier_id')}.md"),
                _render_archive_memo(r, as_of),
            )

    duration = round(time.time() - started, 3)
    summary = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "skill_run_id": run_id,
        "dossiers_processed": len(records),
        "kill_triggered": sum(1 for r in records if r.get("recommendation") == "archive"),
        "de_rate_recommended": sum(1 for r in records if r.get("recommendation") == "de_rate"),
        "manual_review": sum(1 for r in records if r.get("recommendation") == "manual_review"),
        "hold": sum(1 for r in records if r.get("recommendation") == "hold"),
        "output_md": md_path,
        "output_jsonl": jsonl_path,
        "duration_s": duration,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
