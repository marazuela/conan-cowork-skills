"""harvest.py — orchestrator for harvest-historical-events skill (M1).

Resumable, atomic-write, rate-limit-respecting historical event harvester.
Generic across profiles per SKILL.md.

Modes:
  - online (default): hits EDGAR full-text search live
  - offline: synthesizes events from a local fixture (the iter-4 features
    JSON in the reference repo) — used for smoke tests when the sandbox has
    no SEC connectivity.

CLI:
    python harvest.py \
        --profile merger_arb \
        --filing-types DEFM14A,S-4 \
        --date-range 2020-01-01..2024-12-31 \
        --target-n 20 \
        [--mode offline] \
        [--run-id custom_id] \
        [--user-agent "..."]

Final stdout line: structured JSON summary per SKILL.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
OUTPUTS_DIR = SKILL_DIR / "outputs"

# Allow running as a script: ensure helpers dir is on sys.path
sys.path.insert(0, str(HERE))

from atomic_write import atomic_write_json  # noqa: E402
from profile_defaults import defaults_for  # noqa: E402
from edgar_fulltext_search import search_form_month, parse_hit_to_event  # noqa: E402
from event_dedupe import dedupe_against_existing, load_events_file  # noqa: E402

DEFAULT_USER_AGENT = "Investment-Tool-Skill harvest-historical-events research@local"
WALL_CLOCK_BUDGET_S = 20.0
MAX_EVENTS_PER_INVOCATION = 150

REFERENCE_HALT_FLAG = Path(
    "/sessions/pensive-wonderful-ramanujan/mnt/Investment tool backup/02_System/engine/health/HALT_FLAG"
)
REFERENCE_HALT_FLAG_WIN = Path(
    r"C:\Users\javie\OneDrive\Desktop\Claude Cowork\Investment tool backup\02_System\engine\health\HALT_FLAG"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")))
    sys.stdout.flush()


def halt_flag_present() -> dict | None:
    for cand in (REFERENCE_HALT_FLAG, REFERENCE_HALT_FLAG_WIN):
        try:
            if cand.is_file():
                try:
                    return json.loads(cand.read_text(encoding="utf-8"))
                except Exception:
                    return {"reason": "HALT_FLAG present (unparseable)", "path": str(cand)}
        except Exception:
            continue
    return None


def parse_date_range(s: str) -> tuple[str, str]:
    a, b = s.split("..")
    return a.strip(), b.strip()


def derive_run_id(profile: str, date_range: str) -> str:
    h = hashlib.sha1(date_range.encode()).hexdigest()[:8]
    return f"{profile}_{date_range.split('..')[0]}_{date_range.split('..')[1]}_{h}"


def build_bucket_queue(filing_types: list[str], start_date: str, end_date: str) -> list[dict]:
    """Year-month × form_type cursor list, oldest-first."""
    sy, sm, _ = (int(x) for x in start_date.split("-"))
    ey, em, _ = (int(x) for x in end_date.split("-"))
    queue = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        for ft in filing_types:
            queue.append({"source": "edgar", "form_type": ft, "year": y, "month": m, "page": 0})
        m += 1
        if m > 12:
            m = 1
            y += 1
    return queue


def load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def offline_synthesize_events(profile: str, target_n: int, run_id: str) -> list[dict]:
    """Read the reference iter-4 features JSON and project N events into the
    M1 events.json shape. Used for smoke-tests when no SEC connectivity.
    """
    fixture_paths = [
        Path(
            "/sessions/pensive-wonderful-ramanujan/mnt/Investment tool backup/02_System/engine/training/iteration_4_merger_arb_features.json"
        ),
        Path(
            r"C:\Users\javie\OneDrive\Desktop\Claude Cowork\Investment tool backup\02_System\engine\training\iteration_4_merger_arb_features.json"
        ),
    ]
    src = None
    for p in fixture_paths:
        try:
            if p.exists():
                src = p
                break
        except Exception:
            continue
    if src is None:
        return []

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw = (data or {}).get("events", [])[:target_n]
    out = []
    bucket = defaults_for(profile)["bucket"] if profile in {"merger_arb", "activist_governance", "binary_catalyst", "litigation", "insider"} else "ma"
    for r in raw:
        feat = r.get("rich_features", {}) or {}
        cik = str(r.get("cik", "")).zfill(10)
        accession = r.get("accession", "")
        form = r.get("form_type", "")
        eid = hashlib.sha1(f"sec|{accession}|{cik}|{form}".encode()).hexdigest()[:24]
        ticker = feat.get("ticker")
        confidence = 0.95 if ticker else 0.85
        primary_url = (r.get("source") or {}).get("primary_source_url") or ""
        out.append({
            "event_id": eid,
            "bucket": bucket,
            "form_type": form,
            "filed_at": r.get("filed_at", ""),
            "cik": cik,
            "ticker": ticker,
            "figi": None,
            "company_name": r.get("company_name", ""),
            "accession_number_or_id": accession,
            "primary_source_url": primary_url,
            "features": {
                "form": form,
                "is_definitive_form": int(feat.get("is_definitive_form", 0)),
                "is_amendment_form": int(feat.get("is_amendment_form", 0)),
                "target_market_cap_usd": feat.get("target_market_cap_usd"),
                "primary_sic_2dig": None,
                "_source_mode": "offline_iter4_fixture",
            },
            "confidence": confidence,
            "source": primary_url,
            "harvested_at": utc_now_iso(),
            "harvester": "harvest-historical-events.v1.offline",
            "_profile": profile,
            "_run_id": run_id,
        })
    return out


def harvest_online(
    profile: str,
    filing_types: list[str],
    date_range: str,
    target_n: int,
    user_agent: str,
    run_id: str,
    events_path: Path,
    checkpoint_path: Path,
    wall_clock_budget_s: float,
    max_events_per_invocation: int,
) -> dict:
    """Online harvest path. Mutates events_path + checkpoint_path atomically."""
    started = time.monotonic()
    started_iso = utc_now_iso()

    bucket_cfg = defaults_for(profile)
    bucket = bucket_cfg["bucket"]

    existing = load_events_file(str(events_path))
    existing_ids = {e.get("event_id") for e in existing if e.get("event_id")}

    cp = load_checkpoint(checkpoint_path) or {
        "profile": profile,
        "run_id": run_id,
        "started_at": started_iso,
        "buckets_completed": [],
        "events_written": len(existing),
        "target_n": target_n,
        "status": "in_progress",
        "errors": [],
    }
    completed_keys = {(b["form_type"], b["year"], b["month"]) for b in cp.get("buckets_completed", [])}

    start_date, end_date = parse_date_range(date_range)
    queue = build_bucket_queue(filing_types, start_date, end_date)
    queue = [b for b in queue if (b["form_type"], b["year"], b["month"]) not in completed_keys]

    new_events: list[dict] = []
    rate_limit_hits = 0

    for cursor in queue:
        # Budget checks
        if (time.monotonic() - started) > wall_clock_budget_s:
            break
        if (cp["events_written"] + len(new_events)) >= target_n:
            break
        if len(new_events) >= max_events_per_invocation:
            break

        result = search_form_month(
            cursor["form_type"], cursor["year"], cursor["month"], user_agent=user_agent
        )
        if result.get("errors"):
            for err in result["errors"]:
                if err.get("http") in (429, 503):
                    rate_limit_hits += 1
            cp["errors"].extend(result["errors"])

        for hit in result.get("hits", []):
            ev = parse_hit_to_event(hit, profile, bucket)
            if ev is None:
                continue
            if ev["event_id"] in existing_ids:
                continue
            new_events.append(ev)
            existing_ids.add(ev["event_id"])
            if len(new_events) >= max_events_per_invocation:
                break
            if (cp["events_written"] + len(new_events)) >= target_n:
                break

        cp["buckets_completed"].append({
            "form_type": cursor["form_type"], "year": cursor["year"], "month": cursor["month"]
        })

    # Persist
    merged = existing + dedupe_against_existing(new_events, existing)
    cp["events_written"] = len(merged)
    cp["last_updated_at"] = utc_now_iso()
    cp["status"] = "completed" if cp["events_written"] >= target_n else "in_progress"

    payload = {
        "schema_version": 1,
        "profile": profile,
        "run_id": run_id,
        "started_at": cp["started_at"],
        "last_updated_at": cp["last_updated_at"],
        "events_total": len(merged),
        "target_n": target_n,
        "status": cp["status"],
        "events": merged,
    }
    atomic_write_json(str(events_path), payload)
    atomic_write_json(str(checkpoint_path), cp)

    return {
        "status": cp["status"],
        "events_written_this_invocation": len(new_events),
        "events_total": len(merged),
        "target_n": target_n,
        "duration_s": round(time.monotonic() - started, 3),
        "rate_limit_hits": rate_limit_hits,
        "errors": len(cp["errors"]),
    }


def harvest_offline(
    profile: str,
    target_n: int,
    run_id: str,
    events_path: Path,
    checkpoint_path: Path,
) -> dict:
    """Offline (fixture) harvest path. Used for smoke tests."""
    started = time.monotonic()
    started_iso = utc_now_iso()
    events = offline_synthesize_events(profile, target_n, run_id)

    payload = {
        "schema_version": 1,
        "profile": profile,
        "run_id": run_id,
        "started_at": started_iso,
        "last_updated_at": utc_now_iso(),
        "events_total": len(events),
        "target_n": target_n,
        "status": "completed" if len(events) >= target_n else "partial_offline_fixture",
        "events": events,
        "_mode": "offline",
    }
    cp = {
        "profile": profile,
        "run_id": run_id,
        "started_at": started_iso,
        "last_updated_at": utc_now_iso(),
        "events_written": len(events),
        "target_n": target_n,
        "status": payload["status"],
        "errors": [],
        "_mode": "offline",
    }
    atomic_write_json(str(events_path), payload)
    atomic_write_json(str(checkpoint_path), cp)

    return {
        "status": payload["status"],
        "events_written_this_invocation": len(events),
        "events_total": len(events),
        "target_n": target_n,
        "duration_s": round(time.monotonic() - started, 3),
        "rate_limit_hits": 0,
        "errors": 0,
        "mode": "offline",
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="harvest-historical-events orchestrator (M1)")
    p.add_argument("--profile", required=True)
    p.add_argument("--filing-types", default=None, help="Comma-sep list; default per profile")
    p.add_argument("--date-range", required=True, help="YYYY-MM-DD..YYYY-MM-DD")
    p.add_argument("--target-n", type=int, required=True)
    p.add_argument("--mode", choices=["online", "offline"], default="online")
    p.add_argument("--run-id", default=None)
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--wall-clock-budget-s", type=float, default=WALL_CLOCK_BUDGET_S)
    p.add_argument("--max-events-per-invocation", type=int, default=MAX_EVENTS_PER_INVOCATION)
    args = p.parse_args(argv)

    halt = halt_flag_present()
    if halt and args.mode != "offline":
        emit({"status": "halted", "halt": halt})
        return 0

    try:
        cfg = defaults_for(args.profile)
    except KeyError as e:
        emit({"status": "error", "error_class": "unknown_profile", "error_msg": str(e), "recoverable": False})
        return 1

    filing_types = (
        [s.strip() for s in args.filing_types.split(",") if s.strip()]
        if args.filing_types else cfg["filing_types"]
    )
    run_id = args.run_id or derive_run_id(args.profile, args.date_range)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    events_path = OUTPUTS_DIR / f"{args.profile}_{run_id}_events.json"
    checkpoint_path = OUTPUTS_DIR / f"{args.profile}_{run_id}_checkpoint.json"

    try:
        if args.mode == "offline":
            summary = harvest_offline(
                args.profile, args.target_n, run_id, events_path, checkpoint_path
            )
        else:
            if cfg["source"] != "edgar_fulltext":
                emit({
                    "status": "error",
                    "error_class": "source_not_yet_wired",
                    "error_msg": f"Online source {cfg['source']!r} requires the {args.profile} adapter; only edgar_fulltext is wired in v1. Use --mode offline for smoke testing.",
                    "recoverable": True,
                    "missing_adapter": cfg["source"],
                })
                return 1
            summary = harvest_online(
                args.profile,
                filing_types,
                args.date_range,
                args.target_n,
                args.user_agent,
                run_id,
                events_path,
                checkpoint_path,
                args.wall_clock_budget_s,
                args.max_events_per_invocation,
            )
    except Exception as e:
        emit({
            "status": "error",
            "error_class": e.__class__.__name__,
            "error_msg": str(e),
            "recoverable": True,
        })
        return 1

    summary["events_path"] = str(events_path)
    summary["checkpoint_path"] = str(checkpoint_path)
    summary["profile"] = args.profile
    summary["run_id"] = run_id
    emit(summary)
    return 0 if summary["status"] in ("completed", "in_progress", "partial_offline_fixture") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
