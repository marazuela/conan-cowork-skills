"""Dossier parser.

Extracts:
  - YAML-style frontmatter at the head of a dossier.md (between ``---`` lines)
  - The ``## Kill Conditions`` section as a list of parsed condition rows
  - Headline state markers (score, status, primary_catalyst_date, last_updated)

The parser is deliberately tolerant: dossiers in this repo are written by
multiple workflows over time and have minor stylistic variation. The parser
prefers to extract what is unambiguous and to leave the rest for manual review.

Usage:
    from dossier_parser import parse_dossier
    state = parse_dossier("/abs/path/dossier.md")
    # state -> {"frontmatter": {...}, "kill_conditions": [{...}], "raw_text": "..."}

CLI:
    python dossier_parser.py <path>  -> prints JSON to stdout

The parser uses no third-party dependencies (pure stdlib). YAML parsing is
done with a minimal hand-written reader limited to flat key:value lines plus
quoted-string and bracket-list values, which is the shape every dossier in
this repo currently uses.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List


_FRONTMATTER_DELIM = "---"
# Regex for kill-conditions section header. Tolerates: "## Kill Conditions",
# "## 6. Kill Conditions", "## Kill Conditions (explicit, monitor daily)".
_KILL_HEADER = re.compile(
    r"^(?:#{1,3})\s*(?:\d+\.\s*)?Kill\s*Conditions?\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADER = re.compile(r"^#{1,3}\s+\S", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.|\|)\s*(.+?)$", re.MULTILINE)
_PIPE_ROW = re.compile(r"^\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", re.MULTILINE)


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse a leading YAML-ish frontmatter block. Tolerant, stdlib-only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            end = i
            break
    if end is None:
        return {}
    fm: Dict[str, Any] = {}
    for raw in lines[1:end]:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # Strip wrapping quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        # Cast numeric and bool
        if val.lower() in ("true", "false"):
            fm[key] = val.lower() == "true"
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items: List[str] = []
            for piece in inner.split(","):
                p = piece.strip().strip('"').strip("'")
                if p:
                    items.append(p)
            fm[key] = items
            continue
        try:
            if "." in val:
                fm[key] = float(val)
            else:
                fm[key] = int(val)
            continue
        except (TypeError, ValueError):
            pass
        fm[key] = val
    return fm


def _classify_kind(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("price <", "price below", "break <", "price drop", "drop >")):
        return "price_break"
    if "adcom" in t or "advisory committee" in t:
        return "regulatory_decision_issued"
    if "crl" in t or "complete response letter" in t:
        return "regulatory_decision_issued"
    if "pdufa" in t or "fda" in t and ("issued" in t or "decision" in t or "approval" in t):
        return "regulatory_decision_issued"
    if "8-k" in t or "8k" in t:
        return "filing_filed"
    if "13d/a" in t or "13d filing" in t or "schedule 13d" in t:
        return "filing_filed"
    if "withdraw" in t or "termin" in t:
        return "filing_filed"
    if "downgrade" in t or "price target" in t or "pt drop" in t:
        return "analyst_action"
    if "safety signal" in t or "openfda" in t or "adverse event" in t or "ae spike" in t:
        return "safety_signal_appeared"
    if "annual meeting" in t and ("nominat" in t or "passes" in t):
        return "catalyst_date_passed"
    if "issuance" in t or "dilut" in t or "equity raise" in t:
        return "corporate_action"
    if "short interest" in t or "put buildup" in t:
        return "position_change"
    if "sector" in t and ("decline" in t or "selloff" in t or "drawdown" in t):
        return "macro_drawdown"
    if "judgment" in t or "dismissal" in t or "settlement" in t:
        return "regulatory_decision_issued"
    return "manual_review_required"


def _parse_kill_conditions(body: str) -> List[Dict[str, Any]]:
    """Pull list items from the kill-conditions section body.

    Supports three common shapes seen in this repo:
        1. Numbered Markdown list ("1. ...")
        2. Bulleted list ("- ..." / "* ...")
        3. Pipe-table rows (| # | condition | source | action |)
    """
    rows: List[Dict[str, Any]] = []
    # First try pipe-table rows.
    for m in _PIPE_ROW.finditer(body):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        cond = m.group(2).strip()
        source = m.group(3).strip()
        if cond.lower() in ("condition", "---"):
            continue
        rows.append(
            {
                "index": idx,
                "raw_text": cond,
                "parsed_source": source,
                "kind": _classify_kind(cond + " " + source),
            }
        )
    if rows:
        return rows
    # Fallback to list items.
    seen = 0
    for m in _LIST_ITEM.finditer(body):
        item = m.group(1).strip()
        # Strip leading "**...**" emphasis
        item = re.sub(r"^\*+", "", item).strip("* ")
        if not item or len(item) < 4:
            continue
        seen += 1
        rows.append(
            {
                "index": seen,
                "raw_text": item,
                "parsed_source": None,
                "kind": _classify_kind(item),
            }
        )
    return rows


def parse_dossier(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {
            "ok": False,
            "error": "path_missing",
            "path": path,
            "frontmatter": {},
            "kill_conditions": [],
        }
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    fm = _parse_frontmatter(text)
    # Locate the kill-conditions section
    m = _KILL_HEADER.search(text)
    kc_body = ""
    if m:
        start = m.end()
        # find next markdown header at level 1-3 starting at a new line
        rest = text[start:]
        nm = _NEXT_HEADER.search(rest)
        kc_body = rest[: nm.start()] if nm else rest
    kc = _parse_kill_conditions(kc_body) if kc_body else []
    return {
        "ok": True,
        "path": path,
        "frontmatter": fm,
        "kill_conditions": kc,
        "kill_conditions_section_present": bool(m),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: dossier_parser.py <dossier.md>", file=sys.stderr)
        sys.exit(2)
    out = parse_dossier(sys.argv[1])
    print(json.dumps(out, indent=2, default=str))
