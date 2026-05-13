"""activist_governance feature extractor. Mirrors iter-4 schema (16 numeric + sector token + ticker)."""
from __future__ import annotations
from typing import Optional

NUMERIC_KEYS = [
    "no_price_data",
    "target_market_cap_usd",
    "target_market_cap_log10",
    "has_market_cap",
    "price_60d_pre_event",
    "has_60d_ret",
    "price_252d_pre_event",
    "has_252d_ret",
    "is_underperformer",
    "form_is_initial_13d",
    "form_is_13d_amendment",
    "sector_is_tech",
    "sector_is_healthcare",
    "sector_is_financial",
    "sector_is_industrial",
    "sector_is_consumer",
]
TOKEN_KEYS = ["target_sector_token", "ticker"]
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
        m2_feats = (event.get("features") or {})
        form = m2_feats.get("form") or event.get("form_type") or ""
        feats["ticker"] = event.get("ticker") or ""
        feats["no_price_data"] = 0
        mcap = m2_feats.get("target_market_cap_usd")
        if isinstance(mcap, (int, float)) and mcap and mcap > 0:
            import math
            feats["target_market_cap_usd"] = float(mcap)
            feats["target_market_cap_log10"] = round(math.log10(mcap), 3)
            feats["has_market_cap"] = 1
        else:
            feats["target_market_cap_usd"] = 0.0
            feats["target_market_cap_log10"] = 0.0
            feats["has_market_cap"] = 0
            imputed.append("target_market_cap_usd")
        for k in ("price_60d_pre_event", "has_60d_ret", "price_252d_pre_event", "has_252d_ret", "is_underperformer"):
            feats[k] = 0
            imputed.append(k)
        feats["form_is_initial_13d"] = 1 if form == "SC 13D" else 0
        feats["form_is_13d_amendment"] = 1 if form == "SC 13D/A" else 0
        feats["target_sector_token"] = ""
        for k in ("sector_is_tech", "sector_is_healthcare", "sector_is_financial", "sector_is_industrial", "sector_is_consumer"):
            feats[k] = 0
            imputed.append(k)
        source = "m2_inline"
    return feats, imputed, source
