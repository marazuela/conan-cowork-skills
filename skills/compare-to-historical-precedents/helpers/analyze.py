"""compare-to-historical-precedents — orchestrator.

Loads historical_events_ledger.json + iter-4 sidecar (per profile),
computes candidate features (caller-provided / M3-extracted / inline),
runs profile-weighted K-NN, writes precedents.md + knn.json atomically.

CLI:
    python analyze.py \\
        --candidate_id RPAY \\
        --profile activist_governance \\
        [--candidate_features '{"target_market_cap_log10": 8.43, ...}'] \\
        [--candidate_features_path /path/to/features.json] \\
        [--k 5] \\
        [--mode online|offline] \\
        [--reference_root /path/to/Investment\\ tool\\ backup] \\
        [--output_root /path/to/Investment\\ tool\\ backup\\ skills]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow importing peer helpers
HELPERS_DIR = Path(__file__).resolve().parent
if str(HELPERS_DIR) not in sys.path:
    sys.path.insert(0, str(HELPERS_DIR))

from atomic_write import atomic_write_json, atomic_write_text  # noqa: E402
from knn_distance import (  # noqa: E402
    compute_distance,
    profile_weights,
    similarity,
    standardize_universe,
)
from reference_class_aggregator import aggregate_neighbors  # noqa: E402
from sparse_handling import evaluate_density  # noqa: E402


# ---------- profile -> bucket / sidecar map ----------

PROFILE_TO_BUCKET: dict[str, str] = {
    "merger_arb": "ma",
    "activist_governance": "activist",
    "insider": "insider",
    "binary_catalyst": "biotech",
    "litigation": "litigation",
}

PROFILE_TO_SIDECAR: dict[str, str | None] = {
    "merger_arb": "iteration_4_merger_arb_features.json",
    "activist_governance": "iteration_4_activist_features.json",
    "insider": "iteration_4_insider_features.json",
    "binary_catalyst": "iteration_4_biotech_prospective_features.json",
    "litigation": None,  # no on-disk iter-4 sidecar yet
}

OUT_OF_SCOPE_PROFILES = {"short_positioning"}


# ---------- IO helpers ----------

def find_reference_root() -> Path:
    """Find 'Investment tool backup' relative to this file."""
    cur = Path(__file__).resolve()
    for p in cur.parents:
        ref = p.parent / "Investment tool backup"
        if ref.exists():
            return ref
    raise FileNotFoundError("Could not locate 'Investment tool backup' reference folder")


def find_output_root() -> Path:
    """Find 'Investment tool backup skills' (working folder, contains this file)."""
    cur = Path(__file__).resolve()
    for p in cur.parents:
        if p.name == "Investment tool backup skills":
            return p
    raise FileNotFoundError("Could not locate 'Investment tool backup skills' working folder")


def load_ledger(reference_root: Path) -> dict[str, Any]:
    p = reference_root / "02_System" / "engine" / "training" / "historical_events_ledger.json"
    if not p.exists():
        raise FileNotFoundError(f"historical_events_ledger.json not found at {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_sidecar(reference_root: Path, profile: str) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Load iter-4 sidecar and normalize into {event_id: rich_features}.

    Supports both Schema A (by_event_id) and Schema B (events list).
    Returns (sidecar_index, sidecar_path_str or None if not configured).
    """
    fname = PROFILE_TO_SIDECAR.get(profile)
    if not fname:
        return {}, None
    p = reference_root / "02_System" / "engine" / "training" / fname
    if not p.exists():
        return {}, str(p)
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    # Schema A: by_event_id
    if "by_event_id" in d and isinstance(d["by_event_id"], dict):
        return {eid: feats for eid, feats in d["by_event_id"].items()}, str(p)
    # Schema B: events list
    if "events" in d and isinstance(d["events"], list):
        index: dict[str, dict[str, Any]] = {}
        for row in d["events"]:
            eid = row.get("event_id")
            rich = row.get("rich_features", {})
            if eid:
                index[eid] = rich
        return index, str(p)
    return {}, str(p)


def merge_event_features(
    base_features: dict[str, Any],
    sidecar_features: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge base ledger features with iter-4 sidecar overlay.

    Drops _-prefixed keys and RAW_SCALE_DROP_FROM_DESIGN keys
    (per learning_loop.augmented_features convention).
    Drops redundant tokens (bucket/form/ticker) that are constant across
    the filtered universe or already encoded via one-hots, plus raw-USD
    scales that would saturate the distance function.
    """
    out = {k: v for k, v in (base_features or {}).items() if not k.startswith("_")}
    if sidecar_features:
        for k, v in sidecar_features.items():
            if k.startswith("_"):
                continue
            if k == "RAW_SCALE_DROP_FROM_DESIGN":
                continue
            out[k] = v
    # Drop raw-USD scales (RAW_SCALE_DROP_FROM_DESIGN lesson from iter-4)
    for k in [
        "target_market_cap_usd",
        "value_usd",
        "market_cap_usd",
        "avg_volume",
        "short_volume",
        "total_volume",
    ]:
        out.pop(k, None)
    # Drop redundant tokens — bucket is constant after profile filter; form
    # and ticker are already captured by one-hot features (form_is_*) or
    # carry no signal (ticker is event identity, not a feature).
    for k in [
        "bucket", "form", "ticker", "no_price_data",
        # Drop record-metadata that does not carry signal
        "has_ticker", "cik_known",
    ]:
        out.pop(k, None)
    return out


# ---------- candidate feature computation ----------

def compute_candidate_features_inline(
    candidate_id: str, profile: str, mode: str
) -> tuple[dict[str, Any], list[str], float]:
    """Inline computation when no caller features and no path provided.

    For offline mode, this returns a minimal feature dict with imputed flags
    so the smoke test can proceed. For online mode (not implemented in v1),
    yfinance lookups would populate price/cap/sector features.

    Returns (features, imputed_keys, base_confidence).
    """
    imputed: list[str] = []
    feats: dict[str, Any] = {}

    if mode == "offline":
        # Synthetic baseline: zeros + imputation flags. The smoke test
        # demonstrates the K-NN pipeline mechanics; for production use
        # the candidate features are always caller-provided or M3-extracted.
        if profile == "activist_governance":
            feats = {
                "target_market_cap_log10": 8.43,  # log10($268M ~ RPAY)
                "has_market_cap": 1,
                "price_60d_pre_event": -0.05,
                "has_60d_ret": 1,
                "price_252d_pre_event": -0.42,
                "has_252d_ret": 1,
                "is_underperformer": 0,
                "form_is_initial_13d": 0,
                "form_is_13d_amendment": 1,
                "target_sector_token": "Technology",
                "sector_is_tech": 1,
                "sector_is_healthcare": 0,
                "sector_is_financial": 0,
                "sector_is_industrial": 0,
                "sector_is_consumer": 0,
            }
        elif profile == "merger_arb":
            feats = {
                "target_market_cap_log10": 8.0,
                "has_market_cap": 1,
                "price_runup_30d_to_5d": 0.0,
                "has_runup": 1,
                "price_5d_post_filed": 0.0,
                "has_post5": 1,
                "is_definitive_form": 1,
                "is_amendment_form": 0,
                "target_sector_token": "Industrials",
                "sector_is_tech": 0,
                "sector_is_healthcare": 0,
                "sector_is_financial": 0,
                "sector_is_industrial": 1,
            }
        elif profile == "insider":
            feats = {
                "is_purchase": 1,
                "is_sale": 0,
                "is_award": 0,
                "is_exercise": 0,
                "is_role_director": 1,
                "is_role_multi": 0,
                "is_officer": 0,
                "value_usd_log10": 5.5,
                "trade_pct_log10": -3.0,
                "is_large_trade": 0,
                "price_30d_pre_event": -0.05,
                "price_5d_pre_event": -0.01,
                "is_buy_after_dip": 1,
                "avg_volume_log10": 6.0,
                "market_cap_log10": 9.0,
            }
        elif profile == "binary_catalyst":
            feats = {
                "sponsor_p3_track_record": 0.5,
                "sponsor_p3_prior_count_log": 0.0,
                "indication_p3_success_rate": 0.5,
                "indication_p3_pool_size_log": 1.0,
                "enrollment_zscore_vs_indication": 0.0,
                "phase2_readout_strength": 0.5,
                "phase2_prior_count": 1,
                "sponsor_biorxiv_volume_log": 0.0,
            }
        elif profile == "litigation":
            feats = {
                "is_securities": 1,
                "is_patent": 0,
                "is_antitrust": 0,
                "is_class_action": 1,
                "is_district": 1,
                "is_appellate": 0,
                "n_parties": 5,
                "n_attorneys": 8,
            }
        imputed = list(feats.keys())  # all imputed-from-defaults in offline mode
        return feats, imputed, 0.40  # low baseline because synthetic
    else:
        # Online mode would call yfinance + EDGAR. v1: not wired.
        raise NotImplementedError(
            "Online inline candidate-feature computation is scaffolded but not "
            "wired in v1. Provide --candidate_features or --candidate_features_path."
        )


def load_candidate_features_from_path(path: str) -> tuple[dict[str, Any], float]:
    """Load a single-row M3 features file and extract the rich_features dict.

    Returns (features, base_confidence).
    """
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    # Try the M3 schema first
    if "events" in d and isinstance(d["events"], list) and d["events"]:
        return d["events"][0].get("rich_features", {}) or d["events"][0], 0.85
    # Fallback: assume flat features dict
    if "rich_features" in d:
        return d["rich_features"], 0.85
    if isinstance(d, dict):
        return d, 0.75
    raise ValueError(f"Could not parse features from {path}")


# ---------- main analysis pipeline ----------

def build_reference_universe(
    ledger: dict[str, Any], sidecar_index: dict[str, dict[str, Any]], bucket: str
) -> list[dict[str, Any]]:
    """Filter ledger to bucket, drop PENDING, attach sidecar overlay."""
    out: list[dict[str, Any]] = []
    for e in ledger.get("events", []):
        if e.get("bucket") != bucket:
            continue
        outcome = e.get("outcome", {})
        label = outcome.get("label")
        if label not in ("HIT", "MISS", "PARTIAL"):
            continue
        eid = e.get("event_id")
        sidecar_features = sidecar_index.get(eid) if eid else None
        merged = merge_event_features(e.get("features", {}), sidecar_features)
        out.append(
            {
                "event_id": eid,
                "company_name": e.get("company_name"),
                "ticker": e.get("ticker"),
                "filed_at": e.get("filed_at"),
                "form_type": e.get("form_type"),
                "outcome_label": label,
                "return_pct": outcome.get("return_pct"),
                "outcome_confidence": outcome.get("confidence"),
                "primary_source_url": e.get("primary_source_url") or e.get("source"),
                "merged_features": merged,
                "sidecar_present": sidecar_features is not None and bool(sidecar_features),
            }
        )
    return out


def run_knn(
    candidate_features: dict[str, Any],
    universe: list[dict[str, Any]],
    profile: str,
    k: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Compute distances + return top-K neighbors with similarity scores."""
    # Standardize using universe + candidate so distance is on common scale
    feature_rows = [n["merged_features"] for n in universe] + [candidate_features]
    means, stds = standardize_universe(feature_rows)

    # Determine numeric vs token feature keys from universe
    numeric_keys: set[str] = set()
    token_keys: set[str] = set()
    for row in feature_rows:
        for kk, vv in row.items():
            if kk.startswith("_"):
                continue
            if isinstance(vv, str):
                token_keys.add(kk)
            elif isinstance(vv, (int, float, bool)):
                numeric_keys.add(kk)

    weights = profile_weights(profile, sorted(numeric_keys), sorted(token_keys))

    ranked: list[dict[str, Any]] = []
    for n in universe:
        d, top_3 = compute_distance(
            candidate_features, n["merged_features"], means, stds, weights
        )
        sim_raw = similarity(d)
        # Sidecar-missing downgrade
        sim = sim_raw if n["sidecar_present"] else sim_raw * 0.7
        ranked.append(
            {
                **n,
                "distance": round(d, 4),
                "similarity_score": round(sim, 4),
                "delta_features": top_3,
            }
        )

    ranked.sort(key=lambda r: r["distance"])
    return ranked[:k], weights


def render_markdown(
    candidate_id: str,
    profile: str,
    candidate_features: dict[str, Any],
    candidate_features_source: str,
    candidate_features_imputed: list[str],
    neighbors: list[dict[str, Any]],
    aggregates: dict[str, Any],
    density: dict[str, Any],
    universe_meta: dict[str, Any],
    feature_weights: dict[str, float],
    confidence: float,
    harvested_at: str,
) -> str:
    def fmt_num(x: Any, pct: bool = False, places: int = 2) -> str:
        if x is None:
            return "—"
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return str(x)
        if pct:
            return f"{xf*100:+.{places}f}%"
        return f"{xf:.{places}f}"

    def render_ticker(ticker: Any, name: Any) -> str:
        # Numeric ticker => render with company name (CLAUDE.md §1.7)
        t = str(ticker) if ticker else ""
        n = str(name) if name else ""
        if t and t.isdigit():
            return f"{t} ({n})" if n else t
        if t and n:
            return f"{n} ({t})"
        return n or t or "—"

    lines: list[str] = []
    lines.append(f"# Reference class for {candidate_id} ({profile})")
    lines.append("")
    lines.append(f"_Generated: {harvested_at}_")
    lines.append(f"_Skill: compare-to-historical-precedents v1_")
    lines.append(f"_Confidence: {confidence:.2f}_")
    lines.append("")
    lines.append("## Reference universe")
    lines.append(f"- Bucket: `{universe_meta['bucket']}`")
    lines.append(f"- Total events in bucket: {universe_meta['n_events_total']}")
    lines.append(f"- Resolved (HIT/MISS/PARTIAL): {universe_meta['n_events_resolved']}")
    lines.append(f"- With iter-4 sidecar overlay: {universe_meta['n_events_with_sidecar']}")
    lines.append(f"- Ledger: `{universe_meta['ledger_path']}`")
    sidecar_path = universe_meta.get("sidecar_path") or "(none for this profile)"
    lines.append(f"- Sidecar: `{sidecar_path}`")
    lines.append("")
    lines.append("## Candidate features")
    lines.append(f"_Source: {candidate_features_source}_")
    if candidate_features_imputed:
        lines.append(f"_Imputed (default-zero or synthetic): {', '.join(candidate_features_imputed)}_")
    lines.append("")
    lines.append("| Feature | Value |")
    lines.append("|---|---|")
    for k in sorted(candidate_features.keys()):
        v = candidate_features[k]
        if isinstance(v, float):
            lines.append(f"| `{k}` | {v:.4f} |")
        else:
            lines.append(f"| `{k}` | `{v}` |")
    lines.append("")

    lines.append(f"## K={len(neighbors)} nearest neighbors")
    lines.append("")
    lines.append("| Rank | Event | Filed | Form | Outcome | Return | Distance | Similarity |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, n in enumerate(neighbors, 1):
        label = n.get("outcome_label", "?")
        ret = n.get("return_pct")
        ret_str = fmt_num(ret, pct=True, places=1) if ret is not None else "—"
        ticker_disp = render_ticker(n.get("ticker"), n.get("company_name"))
        lines.append(
            f"| {i} | {ticker_disp} | {n.get('filed_at','—')} | {n.get('form_type','—')} | "
            f"{label} | {ret_str} | {n.get('distance','—')} | {n.get('similarity_score','—')} |"
        )
    lines.append("")
    lines.append("### Per-neighbor delta highlights (top-3 contributions)")
    for i, n in enumerate(neighbors, 1):
        deltas = n.get("delta_features", [])
        if not deltas:
            continue
        lines.append(f"")
        lines.append(f"**Rank {i}: {render_ticker(n.get('ticker'), n.get('company_name'))}** ({n.get('outcome_label','?')})")
        for d in deltas:
            cv = d.get("candidate")
            nv = d.get("neighbor")
            lines.append(
                f"- `{d['feature']}`: candidate={cv}, neighbor={nv}, "
                f"contribution={d.get('contribution')}"
            )

    lines.append("")
    lines.append("## Aggregate base rates")
    lines.append("")
    od = aggregates.get("outcome_distribution", {})
    od_str = ", ".join(f"{k}={v}" for k, v in od.items()) if od else "—"
    lines.append(f"- **Outcome distribution**: {od_str}")
    hr = aggregates.get("hit_rate")
    if hr is not None:
        lines.append(f"- **Hit rate**: {hr*100:.1f}%")
    hrsw = aggregates.get("hit_rate_similarity_weighted")
    if hrsw is not None:
        lines.append(f"- **Similarity-weighted hit rate**: {hrsw*100:.1f}%")
    mr = aggregates.get("median_return")
    if mr is not None:
        lines.append(f"- **Median return**: {mr*100:+.2f}%")
    mrsw = aggregates.get("mean_return_similarity_weighted")
    if mrsw is not None:
        lines.append(f"- **Similarity-weighted mean return**: {mrsw*100:+.2f}%")
    p10 = aggregates.get("return_pct_p10")
    p90 = aggregates.get("return_pct_p90")
    if p10 is not None and p90 is not None:
        lines.append(f"- **Return distribution**: p10={p10*100:+.2f}%, p90={p90*100:+.2f}%")
    nwr = aggregates.get("n_with_return", 0)
    lines.append(f"- **Neighbors with concrete `return_pct`**: {nwr} of {len(neighbors)}")
    if aggregates.get("synthetic_return_imputation"):
        lines.append("- ⚠ **Synthetic-return imputation applied** — >60% of neighbors lacked concrete return_pct, label-mapped synthetic returns used.")
    lines.append("")

    if density.get("warnings"):
        lines.append("## Caveats / warnings")
        for w in density["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Primary sources")
    for n in neighbors:
        url = n.get("primary_source_url") or "—"
        lines.append(f"- {render_ticker(n.get('ticker'), n.get('company_name'))} ({n.get('filed_at','—')}): {url}")
    lines.append("")

    lines.append("## Feature weights used in distance")
    lines.append("")
    lines.append("| Feature | Weight |")
    lines.append("|---|---|")
    for k, w in sorted(feature_weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{k}` | {w:.4f} |")
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-NN reference-class lookup")
    parser.add_argument("--candidate_id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--candidate_features", default=None, help="JSON string of feature dict")
    parser.add_argument("--candidate_features_path", default=None)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", default="online", choices=["online", "offline"])
    parser.add_argument("--reference_root", default=None)
    parser.add_argument("--output_root", default=None)
    args = parser.parse_args(argv)

    profile = args.profile
    if profile in OUT_OF_SCOPE_PROFILES:
        out = {
            "status": "error",
            "skill": "compare-to-historical-precedents",
            "error_class": "unknown_profile",
            "error_msg": f"profile {profile} is out of scope per skill_build_plan.json",
            "recoverable": False,
        }
        print(json.dumps(out))
        return 2

    if profile not in PROFILE_TO_BUCKET:
        out = {
            "status": "error",
            "skill": "compare-to-historical-precedents",
            "error_class": "unknown_profile",
            "error_msg": f"profile {profile} not recognized; expected one of {sorted(PROFILE_TO_BUCKET)}",
            "recoverable": False,
        }
        print(json.dumps(out))
        return 2

    # Resolve roots
    try:
        reference_root = Path(args.reference_root) if args.reference_root else find_reference_root()
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": "reference_root_missing", "error_msg": str(e),
                          "recoverable": False}))
        return 2
    try:
        output_root = Path(args.output_root) if args.output_root else find_output_root()
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": "output_root_missing", "error_msg": str(e),
                          "recoverable": False}))
        return 2

    # HALT_FLAG check (online path only)
    if args.mode == "online":
        halt = reference_root / "02_System" / "engine" / "health" / "HALT_FLAG"
        if halt.exists():
            print(json.dumps({"status": "halted", "skill": "compare-to-historical-precedents",
                              "error_class": "halt_flag_present", "error_msg": str(halt),
                              "recoverable": True}))
            return 3

    # Load ledger
    try:
        ledger = load_ledger(reference_root)
    except FileNotFoundError as e:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": "ledger_unavailable", "error_msg": str(e),
                          "recoverable": False}))
        return 2

    sidecar_index, sidecar_path = load_sidecar(reference_root, profile)
    bucket = PROFILE_TO_BUCKET[profile]

    universe_full = build_reference_universe(ledger, sidecar_index, bucket)
    n_events_total = sum(1 for e in ledger.get("events", []) if e.get("bucket") == bucket)
    n_events_resolved = len(universe_full)
    n_events_with_sidecar = sum(1 for n in universe_full if n["sidecar_present"])

    # Resolve candidate features
    candidate_features_imputed: list[str] = []
    candidate_features_source: str = "unknown"
    base_conf: float = 0.85

    if args.candidate_features:
        try:
            candidate_features = json.loads(args.candidate_features)
            candidate_features_source = "caller_provided"
            base_conf = 0.95
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                              "error_class": "bad_candidate_features_json", "error_msg": str(e),
                              "recoverable": True}))
            return 2
    elif args.candidate_features_path:
        try:
            candidate_features, base_conf = load_candidate_features_from_path(
                args.candidate_features_path
            )
            candidate_features_source = f"m3_extracted:{args.candidate_features_path}"
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                              "error_class": "candidate_features_load_failed", "error_msg": str(e),
                              "recoverable": True}))
            return 2
    else:
        try:
            candidate_features, candidate_features_imputed, base_conf = (
                compute_candidate_features_inline(args.candidate_id, profile, args.mode)
            )
            candidate_features_source = f"inline_computed:{args.mode}"
        except NotImplementedError as e:
            print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                              "error_class": "inline_features_not_wired", "error_msg": str(e),
                              "recoverable": True}))
            return 2

    if not candidate_features:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": "insufficient_features",
                          "error_msg": "candidate_features is empty after resolution",
                          "recoverable": True}))
        return 2

    # Run K-NN
    neighbors, feature_weights = run_knn(candidate_features, universe_full, profile, args.k)

    # Density check
    density = evaluate_density(
        n_neighbors_kept=len(neighbors),
        n_neighbors_resolved=n_events_resolved,
        n_with_sidecar=n_events_with_sidecar,
        k_requested=args.k,
    )

    if not density["use"]:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": density["error_class"] or "no_neighbors",
                          "error_msg": "; ".join(density["warnings"]),
                          "recoverable": True}))
        return 2

    aggregates = aggregate_neighbors(neighbors)

    # Confidence: harmonic mean of base candidate-feature confidence + density confidence
    if base_conf > 0 and density["confidence"] > 0:
        confidence = round(2 * base_conf * density["confidence"] / (base_conf + density["confidence"]), 4)
    else:
        confidence = density["confidence"]

    universe_meta = {
        "bucket": bucket,
        "n_events_total": n_events_total,
        "n_events_resolved": n_events_resolved,
        "n_events_with_sidecar": n_events_with_sidecar,
        "ledger_path": str(reference_root / "02_System" / "engine" / "training" / "historical_events_ledger.json"),
        "sidecar_path": sidecar_path,
    }

    harvested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Build sidecar output
    out_json = {
        "schema_version": 1,
        "skill": "compare-to-historical-precedents",
        "skill_version": "v1",
        "candidate_id": args.candidate_id,
        "profile": profile,
        "k_requested": args.k,
        "k_returned": len(neighbors),
        "reference_universe": universe_meta,
        "candidate_features": candidate_features,
        "candidate_features_source": candidate_features_source,
        "candidate_features_imputed": candidate_features_imputed,
        "feature_weights": {k: round(v, 6) for k, v in feature_weights.items()},
        "neighbors": [
            {
                "rank": i + 1,
                "event_id": n["event_id"],
                "company_name": n["company_name"],
                "ticker": n["ticker"],
                "filed_at": n["filed_at"],
                "form_type": n["form_type"],
                "outcome_label": n["outcome_label"],
                "return_pct": n["return_pct"],
                "distance": n["distance"],
                "similarity_score": n["similarity_score"],
                "sidecar_present": n["sidecar_present"],
                "primary_source_url": n["primary_source_url"],
                "delta_features": n["delta_features"],
            }
            for i, n in enumerate(neighbors)
        ],
        "aggregates": aggregates,
        "warnings": density["warnings"],
        "confidence": confidence,
        "harvested_at": harvested_at,
        "harvester": "compare-to-historical-precedents.v1",
    }

    # Write outputs
    out_dir = output_root / "skills" / "compare-to-historical-precedents" / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_out = out_dir / f"{args.candidate_id}_{profile}_knn.json"
    md_out = out_dir / f"{args.candidate_id}_{profile}_precedents.md"

    md_text = render_markdown(
        candidate_id=args.candidate_id,
        profile=profile,
        candidate_features=candidate_features,
        candidate_features_source=candidate_features_source,
        candidate_features_imputed=candidate_features_imputed,
        neighbors=out_json["neighbors"],
        aggregates=aggregates,
        density=density,
        universe_meta=universe_meta,
        feature_weights=out_json["feature_weights"],
        confidence=confidence,
        harvested_at=harvested_at,
    )

    try:
        atomic_write_json(json_out, out_json)
        atomic_write_text(md_out, md_text)
    except OSError as e:
        print(json.dumps({"status": "error", "skill": "compare-to-historical-precedents",
                          "error_class": "write_failure", "error_msg": str(e),
                          "recoverable": True}))
        return 2

    summary = {
        "status": "ok",
        "skill": "compare-to-historical-precedents",
        "candidate_id": args.candidate_id,
        "profile": profile,
        "k_returned": len(neighbors),
        "hit_rate": aggregates.get("hit_rate"),
        "confidence": confidence,
        "outputs": [str(md_out), str(json_out)],
        "harvested_at": harvested_at,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
