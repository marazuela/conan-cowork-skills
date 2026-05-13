"""extract-event-features orchestrator.

Reads M2 outcomes ledger, dispatches to per-profile feature extractor, runs leakage check,
emits features matrix JSON and feature dictionary markdown atomically.

Usage:
    python extract.py --outcomes-ledger <path> --profile merger_arb [--enrichment-sidecar <path>] [--mode offline|online]

Exits 0 on success, 1 on recoverable error, 2 on unrecoverable error.
Final stdout line is structured JSON status per CLAUDE.md §3.4.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from atomic_write import atomic_write_json, atomic_write_text  # noqa: E402

import feature_extractors_merger_arb as fe_ma  # noqa: E402
import feature_extractors_activist_governance as fe_ag  # noqa: E402
import feature_extractors_insider as fe_in  # noqa: E402
import feature_extractors_binary_catalyst as fe_bc  # noqa: E402
import feature_extractors_litigation as fe_lit  # noqa: E402
import leakage_check as lc  # noqa: E402
import feature_dictionary_writer as fdw  # noqa: E402

EXTRACTORS = {
    "merger_arb": (fe_ma.extract, fe_ma.NUMERIC_KEYS, fe_ma.TOKEN_KEYS),
    "activist_governance": (fe_ag.extract, fe_ag.NUMERIC_KEYS, fe_ag.TOKEN_KEYS),
    "insider": (fe_in.extract, fe_in.NUMERIC_KEYS, fe_in.TOKEN_KEYS),
    "binary_catalyst": (fe_bc.extract, fe_bc.NUMERIC_KEYS, []),
    "litigation": (fe_lit.extract, fe_lit.NUMERIC_KEYS, fe_lit.TOKEN_KEYS),
}

OUTPUTS_DIR = HERE.parent / "outputs"

HALT_FLAG = Path(r"C:\Users\javie\OneDrive\Desktop\Claude Cowork\Investment tool backup\02_System\engine\health\HALT_FLAG")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def confidence_for_row(enrichment_source: str, imputed: list[str], leakage_present_for_row: bool) -> float:
    if leakage_present_for_row:
        return 0.10
    if imputed and len(imputed) > 0:
        return 0.25
    if enrichment_source.startswith("iter4_sidecar"):
        return 0.85
    if enrichment_source == "m2_inline":
        return 0.45
    if enrichment_source == "m2_inline_only":
        return 0.45
    return 0.30


def build_sidecar_index(profile: str, sidecar_path: Path | None) -> dict:
    """Returns lookup dict keyed by accession (or event_id for binary_catalyst Option-B)."""
    if not sidecar_path or not sidecar_path.exists():
        return {}
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: sidecar JSON malformed: {e}", file=sys.stderr)
        return {}
    if profile == "binary_catalyst":
        # Option-B schema: {by_event_id: {eid: {feats}}}
        return data.get("by_event_id") or {}
    # Option-A schema: {events: [{accession, rich_features, ...}]}
    idx = {}
    for row in data.get("events") or []:
        acc = row.get("accession")
        if acc:
            idx[acc] = row
    return idx


def lookup_sidecar(profile: str, idx: dict, event: dict) -> dict | None:
    if profile == "binary_catalyst":
        return idx.get(event.get("event_id"))
    return idx.get(event.get("accession_number_or_id"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcomes-ledger", required=True)
    ap.add_argument("--profile", required=True, choices=list(EXTRACTORS.keys()))
    ap.add_argument("--enrichment-sidecar", default=None)
    ap.add_argument("--mode", choices=["online", "offline"], default="offline")
    args = ap.parse_args()

    if args.mode == "online" and HALT_FLAG.exists():
        out = {"status": "halted", "reason": "HALT_FLAG present", "skill": "extract-event-features"}
        print(json.dumps(out))
        return 0

    led_path = Path(args.outcomes_ledger)
    if not led_path.exists():
        out = {"status": "error", "error_class": "input_missing", "error_msg": f"outcomes ledger not found: {led_path}", "recoverable": True}
        print(json.dumps(out))
        return 1
    try:
        ledger = json.loads(led_path.read_text(encoding="utf-8"))
    except Exception as e:
        out = {"status": "error", "error_class": "input_malformed", "error_msg": str(e), "recoverable": False}
        print(json.dumps(out))
        return 2

    profile = args.profile
    extractor_fn, num_keys, tok_keys = EXTRACTORS[profile]
    sidecar_path = Path(args.enrichment_sidecar) if args.enrichment_sidecar else None
    sidecar_idx = build_sidecar_index(profile, sidecar_path)

    out_events = []
    label_dist = {"HIT": 0, "MISS": 0, "PARTIAL": 0, "UNRESOLVABLE": 0, "PENDING": 0}
    extractor_errors = 0
    sidecar_matched = 0

    for ev in ledger.get("events") or []:
        # Filter to labeled events with HIT/MISS/PARTIAL outcomes (mirrors learning_loop.calibrate_profile)
        outcome = ev.get("outcome") or {}
        label = outcome.get("label")
        if label in label_dist:
            label_dist[label] = label_dist.get(label, 0) + 1
        side_row = lookup_sidecar(profile, sidecar_idx, ev)
        if side_row is not None:
            sidecar_matched += 1
        try:
            rich_features, imputed, src = extractor_fn(ev, side_row)
        except Exception as e:
            extractor_errors += 1
            rich_features = {k: 0 for k in num_keys}
            for k in tok_keys:
                rich_features[k] = ""
            imputed = list(num_keys) + list(tok_keys)
            src = f"extractor_error:{type(e).__name__}"

        # Per-row leakage check (binary_catalyst only matters here)
        leakage_set = lc.RESOLVER_LEAKAGE_FEATURES.get(profile, set())
        row_has_leakage = any(
            isinstance(rich_features.get(k), (int, float)) and rich_features.get(k) != 0
            for k in leakage_set
        )

        conf = confidence_for_row(src, imputed, row_has_leakage)

        out_events.append({
            "event_id": ev.get("event_id"),
            "profile": profile,
            "accession": ev.get("accession_number_or_id"),
            "cik": ev.get("cik"),
            "filed_at": ev.get("filed_at"),
            "company_name": ev.get("company_name"),
            "outcome_label": label,
            "rich_features": rich_features,
            "source": {
                "primary_source_url": ev.get("primary_source_url"),
                "enrichment_source": (
                    f"iter4_sidecar:{sidecar_path}" if src.startswith("iter4_sidecar") and sidecar_path
                    else src
                ),
            },
            "confidence": conf,
            "_imputed_features": imputed,
            "harvested_at": now_iso(),
            "harvester": "extract-event-features.v1",
        })

    feature_keys = list(num_keys) + list(tok_keys)
    leakage_verdict = lc.check(profile, feature_keys, out_events)

    run_id = ledger.get("run_id") or "unknown"
    out_path = OUTPUTS_DIR / f"{profile}_{run_id}_features.json"
    dict_path = OUTPUTS_DIR / f"{profile}_feature_dictionary.md"

    matrix = {
        "schema_version": 1,
        "profile": profile,
        "run_id": run_id,
        "source_outcomes_ledger": str(led_path),
        "source_enrichment_sidecar": str(sidecar_path) if sidecar_path else None,
        "extracted_at": now_iso(),
        "extractor": "extract-event-features.v1",
        "events_total": len(out_events),
        "label_distribution": {k: v for k, v in label_dist.items() if v > 0},
        "feature_keys_numeric": list(num_keys),
        "feature_keys_token": list(tok_keys),
        "sidecar_matched_count": sidecar_matched,
        "extractor_errors": extractor_errors,
        "leakage_check": leakage_verdict,
        "events": out_events,
    }

    atomic_write_json(out_path, matrix)
    md = fdw.write(profile, feature_keys, out_events, leakage_verdict)
    atomic_write_text(dict_path, md)

    summary = {
        "status": "ok",
        "skill": "extract-event-features",
        "profile": profile,
        "run_id": run_id,
        "events_total": len(out_events),
        "sidecar_matched_count": sidecar_matched,
        "extractor_errors": extractor_errors,
        "label_distribution": {k: v for k, v in label_dist.items() if v > 0},
        "leakage_verdict": leakage_verdict.get("verdict"),
        "confidence_floor": leakage_verdict.get("confidence_floor"),
        "outputs": {
            "features_json": str(out_path),
            "feature_dictionary_md": str(dict_path),
        },
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
