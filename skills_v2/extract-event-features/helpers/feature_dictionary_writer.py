"""Feature dictionary markdown writer.

Generates a profile-scoped markdown documenting every feature column with name, type, observed range,
description, source, and resolver-leakage flag.
"""
from __future__ import annotations
from typing import Any

DESCRIPTIONS = {
    # merger_arb
    "ticker": "Yfinance trading symbol (preserved for downstream rendering with company_name).",
    "no_price_data": "1 if yfinance lookup failed for this ticker; 0 otherwise.",
    "target_market_cap_usd": "Target equity market capitalization at filing time, USD.",
    "target_market_cap_log10": "log10 of market cap; preferred over raw cap as design feature.",
    "has_market_cap": "1 if market cap was successfully looked up; 0 otherwise.",
    "price_runup_30d_to_5d": "Stock return from t-30 to t-5 trading days before filing (decimal).",
    "has_runup": "1 if runup was successfully computed.",
    "price_5d_post_filed": "Stock return from t to t+5 trading days after filing (decimal).",
    "has_post5": "1 if post-filing return was successfully computed.",
    "is_definitive_form": "1 if form is definitive (DEFM14A, S-4, SC TO-T, SC 14D9); 0 otherwise.",
    "is_amendment_form": "1 if form is an amendment (S-4/A, SC 13D/A, etc); 0 otherwise.",
    "target_sector_token": "Yahoo Finance sector token; empty when unavailable.",
    "sector_is_tech": "One-hot: target sector is Technology or Communication Services.",
    "sector_is_healthcare": "One-hot: target sector is Healthcare.",
    "sector_is_financial": "One-hot: target sector is Financial Services.",
    "sector_is_industrial": "One-hot: target sector is Industrials.",
    "sector_is_consumer": "One-hot: target sector is Consumer Cyclical or Consumer Defensive (activist only).",
    # activist_governance
    "price_60d_pre_event": "Stock return over 60 trading days before SC 13D filing (decimal).",
    "has_60d_ret": "1 if 60d return was computed.",
    "price_252d_pre_event": "Stock return over 252 trading days (~1y) before SC 13D filing (decimal).",
    "has_252d_ret": "1 if 252d return was computed.",
    "is_underperformer": "1 if stock underperformed sector ETF over 252d by >10pp; 0 otherwise.",
    "form_is_initial_13d": "1 if SC 13D (not amendment).",
    "form_is_13d_amendment": "1 if SC 13D/A.",
    # insider
    "is_purchase": "Form-4 transaction code P (open-market purchase).",
    "is_sale": "Form-4 transaction code S (open-market sale).",
    "is_award": "Form-4 transaction code A (grant/award).",
    "is_exercise": "Form-4 transaction code M (option exercise).",
    "is_director": "Filer's relationship includes Director.",
    "is_officer": "Filer's relationship includes Officer.",
    "is_ten_pct_owner": "Filer's relationship is 10% beneficial owner.",
    "is_role_multi": "Filer holds multiple roles (e.g., Director + Officer).",
    "is_role_director": "Filer is solely Director.",
    "is_role_officer": "Filer is solely Officer.",
    "is_role_tenpct": "Filer is solely 10% owner.",
    "n_total_txn": "Number of transaction lines in this Form-4.",
    "total_shares": "Total shares transacted across all lines.",
    "total_value_usd": "Total transaction USD value across all lines.",
    "value_usd_log10": "log10 of total transaction USD value.",
    "trade_pct_of_outstanding": "Total shares as fraction of shares outstanding.",
    "trade_pct_log10": "log10 of trade_pct_of_outstanding.",
    "is_large_trade": "1 if trade_pct_of_outstanding >= 0.001 (10bps); 0 otherwise.",
    "price_30d_pre_event": "Stock return over 30 trading days before filing.",
    "has_30d_ret": "1 if 30d return was computed.",
    "price_5d_pre_event": "Stock return over 5 trading days before filing.",
    "has_5d_ret": "1 if 5d return was computed.",
    "is_buy_after_dip": "1 if is_purchase=1 and price_5d_pre_event < -2%; 0 otherwise.",
    "avg_volume_30d": "Average daily volume over prior 30 trading days.",
    "avg_volume_log10": "log10 of avg_volume_30d.",
    "market_cap_usd": "Issuer market cap at filing time.",
    "market_cap_log10": "log10 of market_cap_usd.",
    "role_token": "Filer role string ('director', 'officer', 'tenpct', 'multi').",
    # binary_catalyst (Option-B prospective)
    "sponsor_p3_track_record": "Sponsor's prior P3 success rate (decimal).",
    "sponsor_p3_prior_count_log": "log10 of sponsor's prior P3 trial count + 1.",
    "indication_p3_success_rate": "Indication-bucket historical P3 success rate (BIO/BPI 2024 data).",
    "indication_p3_pool_size_log": "log10 of indication-bucket prior trial count.",
    "enrollment_zscore_vs_indication": "Trial enrollment z-score vs indication-bucket median.",
    "phase2_readout_strength": "Phase-2 readout signal strength score (0..1, sponsor self-reported).",
    "phase2_prior_count": "Number of prior P2 trials in this program.",
    "sponsor_biorxiv_volume_log": "log10 of sponsor's prior biorxiv preprint count.",
    # litigation
    "claim_amount_usd": "Claimed damages amount in USD (UPPER bound; actuals typically lower).",
    "claim_amount_log10": "log10 of claim_amount_usd.",
    "has_claim": "1 if claim amount was extracted.",
    "ev_pct_claim": "claim_amount_usd / enterprise_value (financial materiality ratio).",
    "motion_stage_ord": "Ordinal: 0=complaint, 1=mtd_pending, 2=mtd_denied/granted, 3=discovery, 4=summary_judgment, 5=trial, 6=settled, 7=appeal.",
    "is_party_publicly_traded": "1 if defendant/respondent is publicly traded; 0 otherwise.",
    "party_resolution_confidence": "0..1 confidence in entity match (CIK exact = 1.0, fuzzy < 0.92 = signal dropped).",
    "days_since_complaint": "Days between complaint filing and current observation.",
    "is_class_action": "1 if class action; 0 otherwise.",
    "is_sec_enforcement": "1 if SEC enforcement action (LR, AAER); 0 otherwise.",
    "case_type_token": "Case type label ('securities_fraud', 'patent', 'antitrust', etc).",
    "jurisdiction_token": "Court jurisdiction ('SDNY', 'EDTX', 'PTAB', 'USITC', etc).",
}


def write(profile: str, feature_keys: list[str], events: list[dict], leakage_check: dict) -> str:
    """Return markdown text. Caller atomic-writes."""
    leakage_set = set(leakage_check.get("resolver_leakage_set") or [])
    lines = [
        f"# Feature Dictionary — `{profile}`",
        "",
        f"_Generated by extract-event-features.v1. Observed ranges reflect the current run sample (n={len(events)})._",
        "",
        "| Feature | Type | Observed range | Description | Resolver-leakage |",
        "|---------|------|----------------|-------------|------------------|",
    ]
    for k in feature_keys:
        vals = []
        for ev in events:
            rf = ev.get("rich_features") or {}
            v = rf.get(k)
            if isinstance(v, (int, float)):
                vals.append(v)
        if vals:
            obs = f"[{min(vals):g}, {max(vals):g}]"
            ftype = "binary" if all(v in (0, 1) for v in vals) else (
                "log10" if "log10" in k else (
                    "ratio" if any(x in k for x in ("price_", "ret_", "pct", "rate", "score", "zscore")) else "count"
                )
            )
        else:
            obs = "(no numeric values)"
            ftype = "token" if k in {"target_sector_token", "ticker", "role_token", "case_type_token", "jurisdiction_token"} else "binary"
        desc = DESCRIPTIONS.get(k, "(undocumented; add to feature_dictionary_writer.DESCRIPTIONS)")
        leakage_flag = "**YES — D-097**" if k in leakage_set else "no"
        lines.append(f"| `{k}` | {ftype} | {obs} | {desc} | {leakage_flag} |")
    lines.append("")
    lines.append(f"**Leakage check verdict**: `{leakage_check.get('verdict')}` (confidence floor {leakage_check.get('confidence_floor')}).")
    if leakage_check.get("verdict") == "leakage_features_present":
        lines.append("")
        lines.append(f"⚠️ Leakage columns in schema: {leakage_check.get('leakage_columns_in_schema')}.")
        lines.append(f"⚠️ Events with nonzero leakage: {leakage_check.get('events_with_nonzero_leakage')}.")
    return "\n".join(lines) + "\n"
