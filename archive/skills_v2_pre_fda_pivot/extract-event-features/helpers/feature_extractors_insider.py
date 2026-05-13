"""insider feature extractor. Mirrors iter-4 schema (24 numeric + role_token + ticker)."""
from __future__ import annotations
from typing import Optional

NUMERIC_KEYS = [
    "no_price_data",
    "is_purchase",
    "is_sale",
    "is_award",
    "is_exercise",
    "is_director",
    "is_officer",
    "is_ten_pct_owner",
    "is_role_multi",
    "is_role_director",
    "is_role_officer",
    "is_role_tenpct",
    "n_total_txn",
    "total_shares",
    "total_value_usd",
    "value_usd_log10",
    "trade_pct_of_outstanding",
    "trade_pct_log10",
    "is_large_trade",
    "price_30d_pre_event",
    "has_30d_ret",
    "price_5d_pre_event",
    "has_5d_ret",
    "is_buy_after_dip",
    "avg_volume_30d",
    "avg_volume_log10",
    "market_cap_usd",
    "market_cap_log10",
    "has_market_cap",
]
TOKEN_KEYS = ["role_token", "ticker"]
ALL_KEYS = NUMERIC_KEYS + TOKEN_KEYS


def extract(event: dict, sidecar_row: Optional[dict]) -> tuple[dict, list[str], str]:
    feats: dict = {}
    imputed: list[str] = []
    if sidecar_row and isinstance(sidecar_row.get("rich_features"), dict):
        rf = sidecar_row["rich_features"]
        for k in ALL_KEYS:
            if k in rf:
                feats[k] = rf[k]
            else:
                feats[k] = "" if k in TOKEN_KEYS else 0
                imputed.append(k)
        source = "iter4_sidecar"
    else:
        feats["ticker"] = event.get("ticker") or ""
        feats["role_token"] = ""
        for k in NUMERIC_KEYS:
            feats[k] = 0
            imputed.append(k)
        source = "m2_inline"
    return feats, imputed, source
