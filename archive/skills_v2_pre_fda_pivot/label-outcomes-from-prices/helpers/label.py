#!/usr/bin/env python3
"""label.py - Orchestrator for the label-outcomes-from-prices skill.

Reads an events ledger produced by harvest-historical-events (M1), fetches
per-event price history, computes forward returns at the requested windows,
applies the profile-specific HIT/MISS/PARTIAL classifier, and atomic-writes
the outcomes ledger + checkpoint + summary.

Bounded runtime, resumable, HALT_FLAG honored on online path. Offline mode
uses synthetic deterministic returns derived from event_id (for smoke tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HELPERS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HELPERS_DIR))

import atomic_write  # noqa: E402
import yfinance_fetch  # noqa: E402
import profile_thresholds  # noqa: E402
import corporate_actions  # noqa: E402

SKILL_DIR = HELPERS_DIR.parent
OUTPUTS_DIR = SKILL_DIR / "outputs"

WORKING_ROOT = SKILL_DIR.parent.parent  # .../Investment tool backup skills/
REFERENCE_ROOT = WORKING_ROOT.parent / "Investment tool backup"

HALT_PATHS = [
    WORKING_ROOT / "02_System" / "engine" / "health" / "HALT_FLAG",
    REFERENCE_ROOT / "02_System" / "engine" / "health" / "HALT_FLAG",
]

WALL_CLOCK_BUDGET_S = 25.0
DEFAULT_BATCH_SIZE = 10
THROTTLE_SLEEP_S = 0.6


def emit(payload):
    print(json.dumps(payload, separators=(",", ":")))
    sys.stdout.flush()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def halt_flag_present():
    for p in HALT_PATHS:
        try:
            if p.exists():
                return True
        except Exception:
            pass
    return False


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def deterministic_offline_returns(event_id, windows):
    h = hashlib.sha256(event_id.encode("utf-8")).digest()
    out = {"anchor_close": 10.0 + (h[0] / 255.0) * 90.0, "anchor_ts": 0}
    for i, w in enumerate(windows):
        b = h[(i + 1) % len(h)]
        ret = (b / 255.0) * 0.65 - 0.30
        out["ret_" + str(int(w)) + "d"] = round(ret, 6)
        try:
            ann = (1.0 + ret) ** (365.0 / float(w)) - 1.0
            if ann != ann or abs(ann) > 1e6:
                ann = None
            else:
                ann = round(ann, 6)
        except Exception:
            ann = None
        out["ret_" + str(int(w)) + "d_annualized"] = ann
    return out


def derive_run_id(ledger):
    return ledger.get("run_id") or "no_run_id"


def derive_output_paths(profile, run_id):
    base = profile + "_" + run_id
    return (
        OUTPUTS_DIR / (base + "_outcomes.json"),
        OUTPUTS_DIR / (base + "_outcomes_checkpoint.json"),
        OUTPUTS_DIR / (base + "_outcomes_summary.md"),
    )


def base_confidence_for(history_source, used_corp_action):
    if used_corp_action:
        return 0.70
    if history_source == "adjclose":
        return 0.85
    if history_source == "quote_close":
        return 0.75
    if history_source == "offline_synthetic":
        # Offline synthetic: low but nonzero so downstream filters can
        # distinguish "synthetic placeholder" from "totally unresolved".
        return 0.50
    return 0.0


def compose_summary_md(profile, run_id, label_counts, return_windows, canonical_window, events_total):
    lines = []
    keys = sorted(label_counts.keys())
    for k in keys:
        lines.append("- " + k + ": " + str(label_counts[k]))
    body = (
        "# Outcomes summary - " + profile + "\n\n"
        + "**Run ID:** " + run_id + "\n\n"
        + "**Events total:** " + str(events_total) + "\n\n"
        + "**Return windows (days):** " + ", ".join(str(w) for w in return_windows) + "\n\n"
        + "**Canonical window (days):** " + str(canonical_window) + "\n\n"
        + "## Label distribution (cumulative)\n\n"
        + ("\n".join(lines) if lines else "_no events labeled_")
        + "\n\n"
        + "Generated: " + utc_now_iso() + "\n"
    )
    return body


def normalize_filed_at(filed_at):
    if not filed_at:
        return ""
    return str(filed_at)[:10]


def parse_anchor(filed_at_iso):
    return yfinance_fetch.parse_iso_date(filed_at_iso)


def resolve_ticker(event, mode, ticker_cache):
    ticker = (event.get("ticker") or "").strip().upper()
    cik = (event.get("cik") or "").strip()
    if ticker:
        return ticker, "direct"
    if mode == "offline":
        return None, "no_ticker_offline"
    if cik:
        if cik in ticker_cache:
            return ticker_cache[cik], "cached"
        t = yfinance_fetch.lookup_ticker_from_cik(cik)
        ticker_cache[cik] = t
        time.sleep(THROTTLE_SLEEP_S)
        if t:
            return t, "cik_lookup"
    return None, "unresolved"


def process_event_online(event, return_windows, ticker_cache):
    ticker, _ = resolve_ticker(event, "online", ticker_cache)
    filed_at = normalize_filed_at(event.get("filed_at"))
    anchor = parse_anchor(filed_at)
    if anchor is None:
        return ({}, "", None, "UNRESOLVABLE_DATE", "")
    if not ticker:
        return ({}, "", None, "UNRESOLVABLE_TICKER", "")
    start_dt = anchor - timedelta(days=5)
    end_dt = anchor + timedelta(days=max(return_windows) + 14)
    history, src = yfinance_fetch.fetch_yahoo_history(ticker, start_dt, end_dt)
    if not history:
        cik = (event.get("cik") or "").strip()
        ca = corporate_actions.resolve_corporate_action(cik, filed_at, ticker)
        if ca and ca.get("fallback_ticker"):
            time.sleep(THROTTLE_SLEEP_S)
            history, src = yfinance_fetch.fetch_yahoo_history(
                ca["fallback_ticker"], start_dt, end_dt
            )
            if history:
                rets = yfinance_fetch.compute_forward_returns(history, anchor, return_windows)
                if rets:
                    return (
                        rets,
                        src,
                        ca,
                        None,
                        yfinance_fetch.yahoo_chart_source_url(ca["fallback_ticker"]),
                    )
        return ({}, "", ca, "UNRESOLVABLE_PRICE", "")
    rets = yfinance_fetch.compute_forward_returns(history, anchor, return_windows)
    if not rets:
        return ({}, src, None, "UNRESOLVABLE_PRICE", "")
    return (rets, src, None, None, yfinance_fetch.yahoo_chart_source_url(ticker))


def process_event_offline(event, return_windows):
    eid = event.get("event_id") or json.dumps(event, sort_keys=True)[:32]
    rets = deterministic_offline_returns(eid, return_windows)
    return rets, "offline_synthetic", None, None, "offline://deterministic-from-event_id"


def build_output_envelope(profile, run_id, return_windows, canonical_window, out_events, label_counts):
    n_hit = label_counts.get("HIT", 0)
    n_miss = label_counts.get("MISS", 0)
    n_partial = label_counts.get("PARTIAL", 0)
    n_pending = label_counts.get("PENDING_WINDOW", 0)
    n_unres = sum(v for k, v in label_counts.items() if k.startswith("UNRESOLVABLE"))
    return {
        "schema_version": 1,
        "profile": profile,
        "run_id": run_id,
        "labeled_at": utc_now_iso(),
        "labeler": "label-outcomes-from-prices.v1",
        "events_total": len(out_events),
        "n_hit": n_hit,
        "n_miss": n_miss,
        "n_partial": n_partial,
        "n_unresolvable": n_unres,
        "n_pending_window": n_pending,
        "return_windows_days": return_windows,
        "canonical_window_days": canonical_window,
        "events": out_events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-ledger", required=True)
    ap.add_argument("--profile", required=True, choices=list(profile_thresholds._PROFILE_DISPATCH.keys()))
    ap.add_argument("--return-windows", default=None)
    ap.add_argument("--mode", default="online", choices=["online", "offline"])
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--max-events", type=int, default=200)
    ap.add_argument("--enable-partial", action="store_true")
    args = ap.parse_args()

    started = time.time()

    if args.mode == "online" and halt_flag_present():
        emit({"status": "halted", "reason": "HALT_FLAG present", "recoverable": True})
        return 0

    ledger_path = Path(args.events_ledger)
    if not ledger_path.is_absolute():
        ledger_path = WORKING_ROOT / args.events_ledger
    if not ledger_path.exists():
        emit({"status": "error", "error_class": "missing_ledger", "error_msg": str(ledger_path), "recoverable": True})
        return 1

    try:
        ledger = load_json(ledger_path)
    except Exception as e:
        emit({"status": "error", "error_class": "ledger_parse_error", "error_msg": str(e), "recoverable": False})
        return 1

    if ledger.get("profile") and ledger["profile"] != args.profile:
        emit({
            "status": "error",
            "error_class": "profile_mismatch",
            "error_msg": "ledger.profile=" + str(ledger.get("profile")) + " != input.profile=" + args.profile,
            "recoverable": False,
        })
        return 1

    if args.return_windows:
        return_windows = [int(w.strip()) for w in args.return_windows.split(",") if w.strip()]
    else:
        return_windows = profile_thresholds.DEFAULT_WINDOWS_DAYS[args.profile]
    canonical_window = profile_thresholds.CANONICAL_WINDOW_DAYS[args.profile]
    if canonical_window not in return_windows:
        return_windows = sorted(set(return_windows + [canonical_window]))

    run_id = derive_run_id(ledger)
    out_path, ckpt_path, summary_path = derive_output_paths(args.profile, run_id)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    events = ledger.get("events", []) or []
    events_total = len(events)
    if events_total == 0:
        emit({"status": "completed", "summary": "no events to label", "events_total": 0, "recoverable": True})
        return 0

    last_idx = -1
    if ckpt_path.exists():
        try:
            ckpt = load_json(ckpt_path)
            if ckpt.get("events_total") == events_total and ckpt.get("run_id") == run_id:
                last_idx = ckpt.get("last_processed_index", -1)
        except Exception:
            pass

    existing_out = None
    if out_path.exists():
        try:
            existing_out = load_json(out_path)
        except Exception:
            existing_out = None
    out_events = (existing_out or {}).get("events") or [{**ev} for ev in events]
    by_id = {ev.get("event_id"): i for i, ev in enumerate(out_events)}
    for src_ev in events:
        eid = src_ev.get("event_id")
        if eid not in by_id:
            out_events.append({**src_ev})
            by_id[eid] = len(out_events) - 1

    n_resolved = 0
    n_unresolvable = 0
    n_pending = 0
    delta_counts = {}
    ticker_cache = {}

    end_idx = min(events_total, last_idx + 1 + args.max_events)
    processed_this_run = 0

    for idx in range(last_idx + 1, end_idx):
        if (time.time() - started) > WALL_CLOCK_BUDGET_S:
            break
        ev = out_events[by_id[events[idx].get("event_id")]]
        prior = ev.get("outcome") or {}
        prior_label = prior.get("label")
        if prior_label and prior_label != "PENDING_WINDOW" and not prior_label.startswith("UNRESOLVABLE"):
            n_resolved += 1
            processed_this_run += 1
            continue

        if args.mode == "offline":
            fr, hist_src, corp_action, err_label, source_url = process_event_offline(ev, return_windows)
        else:
            fr, hist_src, corp_action, err_label, source_url = process_event_online(ev, return_windows, ticker_cache)

        if err_label:
            ev["forward_returns"] = {}
            ev["outcome"] = {
                "label": err_label,
                "return_pct": None,
                "return_pct_annualized": None,
                "window_days": canonical_window,
                "criterion": err_label.lower(),
                "corporate_action": (corp_action.get("action") if isinstance(corp_action, dict) else None),
                "confidence": 0.0,
                "resolved_at": utc_now_iso(),
                "source": source_url or ev.get("source") or "",
            }
            ev["confidence"] = 0.0
            ev["source"] = source_url or ev.get("source") or ""
            delta_counts[err_label] = delta_counts.get(err_label, 0) + 1
            n_unresolvable += 1
            processed_this_run += 1
            continue

        ev["forward_returns"] = fr
        opts = {}
        if args.enable_partial:
            opts["enable_partial"] = True
        result = profile_thresholds.classify(args.profile, ev, fr, opts)
        label = result["label"]
        criterion = result["criterion"]
        conf_delta = result["confidence_delta"]
        extras = result["extras"]

        used_corp_action = bool(corp_action and corp_action.get("action") not in (None, "unresolvable"))
        base_conf = base_confidence_for(hist_src, used_corp_action)
        conf = max(0.0, min(1.0, base_conf + conf_delta))
        if label == "PENDING_WINDOW":
            conf = 0.50

        canonical_ret = fr.get("ret_" + str(canonical_window) + "d")
        canonical_ret_ann = fr.get("ret_" + str(canonical_window) + "d_annualized")

        ev["outcome"] = {
            "label": label,
            "return_pct": (round(canonical_ret * 100, 4) if canonical_ret is not None else None),
            "return_pct_annualized": (round(canonical_ret_ann * 100, 4) if canonical_ret_ann is not None else None),
            "window_days": canonical_window,
            "criterion": criterion,
            "corporate_action": (corp_action.get("action") if isinstance(corp_action, dict) else None),
            "confidence": round(conf, 4),
            "resolved_at": utc_now_iso(),
            "source": source_url,
            "extras": extras,
        }
        ev["confidence"] = round(conf, 4)
        ev["source"] = source_url

        delta_counts[label] = delta_counts.get(label, 0) + 1
        if label == "PENDING_WINDOW":
            n_pending += 1
        elif label.startswith("UNRESOLVABLE"):
            n_unresolvable += 1
        else:
            n_resolved += 1
        processed_this_run += 1

        if processed_this_run % args.batch_size == 0:
            cum = {}
            for e in out_events:
                lab = (e.get("outcome") or {}).get("label")
                if lab:
                    cum[lab] = cum.get(lab, 0) + 1
            ledger_out = build_output_envelope(args.profile, run_id, return_windows, canonical_window, out_events, cum)
            atomic_write.atomic_write_json(str(out_path), ledger_out)
            atomic_write.atomic_write_json(str(ckpt_path), {
                "run_id": run_id,
                "profile": args.profile,
                "events_total": events_total,
                "last_processed_index": idx,
                "n_resolved": n_resolved,
                "n_unresolvable": n_unresolvable,
                "n_pending": n_pending,
                "checkpointed_at": utc_now_iso(),
            })

    cumulative_counts = {}
    for ev in out_events:
        lab = (ev.get("outcome") or {}).get("label")
        if lab:
            cumulative_counts[lab] = cumulative_counts.get(lab, 0) + 1

    ledger_out = build_output_envelope(args.profile, run_id, return_windows, canonical_window, out_events, cumulative_counts)
    atomic_write.atomic_write_json(str(out_path), ledger_out)
    final_idx = last_idx + processed_this_run
    if processed_this_run == 0:
        final_idx = last_idx
    atomic_write.atomic_write_json(str(ckpt_path), {
        "run_id": run_id,
        "profile": args.profile,
        "events_total": events_total,
        "last_processed_index": final_idx,
        "n_resolved": n_resolved,
        "n_unresolvable": n_unresolvable,
        "n_pending": n_pending,
        "checkpointed_at": utc_now_iso(),
    })
    summary_md = compose_summary_md(args.profile, run_id, cumulative_counts, return_windows, canonical_window, events_total)
    atomic_write.atomic_write_text(str(summary_path), summary_md)

    elapsed = round(time.time() - started, 3)
    final_status = "completed" if final_idx + 1 >= events_total else "in_progress"
    emit({
        "status": final_status,
        "step": "label-outcomes-from-prices",
        "profile": args.profile,
        "run_id": run_id,
        "events_total": events_total,
        "last_processed_index": final_idx,
        "n_resolved": n_resolved,
        "n_unresolvable": n_unresolvable,
        "n_pending_window": n_pending,
        "delta_counts": delta_counts,
        "cumulative_counts": cumulative_counts,
        "elapsed_s": elapsed,
        "deliverables": [
            str(out_path.relative_to(WORKING_ROOT)),
            str(ckpt_path.relative_to(WORKING_ROOT)),
            str(summary_path.relative_to(WORKING_ROOT)),
        ],
        "recoverable": True,
    })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        emit({
            "status": "error",
            "step": "label-outcomes-from-prices",
            "error_class": type(e).__name__,
            "error_msg": str(e),
            "recoverable": True,
        })
        sys.exit(1)
