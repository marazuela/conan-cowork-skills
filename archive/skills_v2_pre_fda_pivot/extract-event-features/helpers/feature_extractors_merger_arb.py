"""merger_arb feature extractor.

Mirrors iter-4 schema (15 numeric + target_sector_token).
"""
from __future__ import annotations
from typing import Optional

NUMERIC_KEYS = [
    "no_price_data",
    "target_market_cap_usd",
    "target_market_cap_log10",
    "has_market_cap",
    "price_runup_30d_to_5d",
    "has_runup",
    "price_5d_post_filed",
    "has_post5",
    "is_definitive_form",
    "is_amendment_form",
    "sector_is_tech",
    "sector_is_healthcare",
    "sector_is_financial",
    "sector_is_industrial",
]
TOKEN_KEYS = ["target_sector_token", "ticker"]
ALL_KEYS = NUMERIC_KEYS + TOKEN_KEYS

DEFINITIVE_FORMS = {"DEFM14A", "S-4", "SC TO-T", "SC 14D9", "SC 14D-9"}
AMENDMENT_FORMS = {"S-4/A", "SC 13D/A", "SC TO-T/A", "DEFM14A/A", "PREM14A"}


def extract(event: dict, sidecar_row: Optional[dict]) -> tuple[dict, list[str], str]:
    """Return (rich_features, imputed_keys, enrichment_source_token)."""
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
        for k in ("price_runup_30d_to_5d", "has_runup", "price_5d_post_filed", "has_post5"):
            feats[k] = 0
            imputed.append(k)
        feats["is_definitive_form"] = 1 if form in DEFINITIVE_FORMS else int(m2_feats.get("is_definitive_form") or 0)
        feats["is_amendment_form"] = 1 if form in AMENDMENT_FORMS else int(m2_feats.get("is_amendment_form") or 0)
        feats["target_sector_token"] = ""
        for k in ("sector_is_tech", "sector_is_healthcare", "sector_is_financial", "sector_is_industrial"):
            feats[k] = 0
            imputed.append(k)
        source = "m2_inline"
    return feats, imputed, source


if __name__ == "__main__":
    import json, sys
    e = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {"form_type": "S-4", "features": {"target_market_cap_usd": 1e9}}
    print(json.dumps(extract(e, None), indent=2))
