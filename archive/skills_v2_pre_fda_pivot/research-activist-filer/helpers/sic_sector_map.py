"""sic_sector_map.py — SIC 4-digit code → high-level sector mapping.

High-level sectors:
  Technology, Healthcare, Financials, Consumer, Industrials,
  Energy, Materials, Utilities, Real Estate, Communication, Other

The mapping is intentionally coarse — used only for sector concentration
metrics in P3, not for fundamental analysis.
"""

from __future__ import annotations

from typing import Dict


def sic_to_sector(sic: str) -> str:
    """Map SEC 4-digit SIC code (string or int) to high-level sector.

    Reference: SEC SIC code table at https://www.sec.gov/info/edgar/siccodes.htm
    """
    try:
        code = int(str(sic).strip())
    except (ValueError, TypeError):
        return "Other"

    # Coarse buckets by SIC division & key 4-digit ranges
    if 100 <= code <= 999:
        return "Materials"  # Agriculture, fishing, forestry — bucketed with Materials for activist purposes
    if 1000 <= code <= 1499:
        return "Materials"  # Mining
    if code == 1311 or 1300 <= code <= 1399:
        return "Energy"  # Oil & gas extraction
    if 1500 <= code <= 1799:
        return "Industrials"  # Construction
    if 2000 <= code <= 2099:
        return "Consumer"  # Food
    if 2100 <= code <= 2199:
        return "Consumer"  # Tobacco
    if 2200 <= code <= 2399:
        return "Consumer"  # Textile, apparel
    if 2400 <= code <= 2499:
        return "Materials"  # Lumber, wood
    if 2500 <= code <= 2599:
        return "Consumer"  # Furniture
    if 2600 <= code <= 2699:
        return "Materials"  # Paper
    if 2700 <= code <= 2799:
        return "Communication"  # Printing & publishing
    if 2800 <= code <= 2899:
        return "Materials"  # Chemicals
    if code in (2833, 2834, 2835, 2836):
        return "Healthcare"  # Pharmaceuticals
    if 2900 <= code <= 2999:
        return "Energy"  # Petroleum refining
    if 3000 <= code <= 3399:
        return "Materials"  # Rubber, leather, primary metals
    if 3400 <= code <= 3499:
        return "Industrials"  # Fabricated metal
    if 3500 <= code <= 3599:
        return "Industrials"  # Industrial machinery
    if 3600 <= code <= 3699:
        return "Technology"  # Electronic equipment
    if code in (3674,):
        return "Technology"  # Semiconductors
    if 3700 <= code <= 3799:
        return "Industrials"  # Transportation equipment
    if 3800 <= code <= 3899:
        return "Healthcare"  # Instruments — many medical devices
    if code in (3841, 3842, 3843, 3844, 3845, 3851):
        return "Healthcare"  # Medical devices, ophthalmics
    if 3900 <= code <= 3999:
        return "Consumer"  # Misc manufacturing
    if 4000 <= code <= 4799:
        return "Industrials"  # Transportation services
    if 4800 <= code <= 4899:
        return "Communication"  # Telecom
    if 4900 <= code <= 4999:
        return "Utilities"
    if 5000 <= code <= 5199:
        return "Industrials"  # Wholesale
    if 5200 <= code <= 5999:
        return "Consumer"  # Retail
    if 6000 <= code <= 6199:
        return "Financials"  # Depository institutions
    if 6200 <= code <= 6299:
        return "Financials"  # Securities & commodities
    if 6300 <= code <= 6499:
        return "Financials"  # Insurance
    if 6500 <= code <= 6599:
        return "Real Estate"
    if 6700 <= code <= 6799:
        return "Financials"  # Holding companies / investment trusts
    if 7000 <= code <= 7099:
        return "Consumer"  # Hotels, lodging
    if 7200 <= code <= 7299:
        return "Consumer"  # Personal services
    if 7300 <= code <= 7399:
        return "Technology"  # Business services — includes 7372 prepackaged software
    if code in (7370, 7371, 7372, 7373, 7374, 7375, 7376, 7377, 7378, 7379):
        return "Technology"  # Computer services / software
    if 7400 <= code <= 7999:
        return "Consumer"  # Misc services / amusement
    if 8000 <= code <= 8099:
        return "Healthcare"  # Health services
    if 8200 <= code <= 8299:
        return "Consumer"  # Educational services
    if 8700 <= code <= 8799:
        return "Industrials"  # Engineering / accounting / R&D
    if 9100 <= code <= 9999:
        return "Other"  # Public administration
    return "Other"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--sic", required=True)
    args = p.parse_args()
    print(sic_to_sector(args.sic))
