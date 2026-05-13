"""Fetch trial data from ClinicalTrials.gov v2 API.

Inputs:
    - nct_ids (optional list)
    - search criteria: drug, indication, sponsor (optional fallback)

Outputs:
    JSON dict {
        "ok": bool,
        "trials": [...],
        "discovery": "provided" | "inferred",
        "dropped_off_topic": [ {nct_id, title, sponsor, reason} ],
        "query": {...},
        "error_class": str
    }

Drug+sponsor post-filter (added 2026-04-29 fix): when fetch_by_nct or
search_trials is called with drug and/or sponsor set, returned trials are
post-filtered so off-topic trials are dropped to dropped_off_topic instead of
corrupting downstream forensics. (Bug: P1 verification on 2026-04-29 fed
illustrative placeholder NCT IDs into the live --nct path; CT.gov returned
real but unrelated trials, breaking the trial forensics.)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


_BASE = "https://clinicaltrials.gov/api/v2"
_USER_AGENT = "InvestmentTool-FDA-Skill/1.0 (contact: javiergorordo13@hotmail.com)"
_TIMEOUT = 12.0


# --------------------------------------------------------------------------- #
# Drug/sponsor post-filter (off-topic NCT-ID guard)
# --------------------------------------------------------------------------- #


def drug_aliases(drug_name: Optional[str]) -> List[str]:
    """Extract searchable aliases. 'AXS-05 (Auvelity)' -> ['Auvelity', 'AXS-05']."""
    if not drug_name:
        return []
    aliases: List[str] = []
    s = drug_name.strip()
    for paren in re.findall(r"\(([^)]+)\)", s):
        for piece in re.split(r"[/,;]", paren):
            piece = piece.strip()
            if piece:
                aliases.append(piece)
    s = re.sub(r"\([^)]+\)", "", s).strip()
    for piece in re.split(r"[/,;]", s):
        piece = piece.strip()
        if piece:
            aliases.append(piece)
    seen, out = set(), []
    for a in aliases:
        key = a.lower()
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def trial_matches_drug_or_sponsor(
    trial: Dict[str, Any],
    aliases: List[str],
    sponsor_substr: Optional[str] = None,
) -> Tuple[bool, str]:
    """True if any drug alias hits intervention/title OR sponsor substring matches."""
    interventions = trial.get("interventions") or []
    intervention_blob = " | ".join(
        str((i or {}).get("name", "")) for i in interventions
    ).lower()
    title = (trial.get("title") or "").lower()
    for alias in aliases:
        key = alias.lower()
        if key and (key in intervention_blob or key in title):
            return True, f"matched_alias:{alias}"
    if sponsor_substr:
        trial_sponsor = (trial.get("sponsor") or "").lower()
        if sponsor_substr.lower() in trial_sponsor:
            return True, f"matched_sponsor:{sponsor_substr}"
    return False, "no_drug_or_sponsor_match"


def filter_trials_by_drug_or_sponsor(
    trials: List[Dict[str, Any]],
    drug: Optional[str] = None,
    sponsor: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (matched, dropped). Pass-through if no filter criteria set."""
    if not drug and not sponsor:
        return list(trials), []
    aliases = drug_aliases(drug) if drug else []
    matched: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for t in trials:
        is_match, reason = trial_matches_drug_or_sponsor(t, aliases, sponsor)
        if is_match:
            matched.append(t)
        else:
            dropped.append(
                {
                    "nct_id": t.get("nct_id"),
                    "title": t.get("title"),
                    "sponsor": t.get("sponsor"),
                    "reason": reason,
                }
            )
    return matched, dropped


# --------------------------------------------------------------------------- #
# HTTP + flatten
# --------------------------------------------------------------------------- #


def _http_get(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return {
                "ok": True,
                "body": resp.read().decode("utf-8", errors="replace"),
                "status": resp.status,
                "url": url,
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "url": url, "error": "http_error"}
    except urllib.error.URLError as e:
        return {"ok": False, "status": 0, "url": url, "error": f"url_error:{e.reason}"}
    except Exception as e:
        return {"ok": False, "status": 0, "url": url, "error": f"exception:{type(e).__name__}"}


def _flatten_trial(study: Dict[str, Any]) -> Dict[str, Any]:
    proto = study.get("protocolSection", {}) or {}
    ident = proto.get("identificationModule", {}) or {}
    status = proto.get("statusModule", {}) or {}
    design = proto.get("designModule", {}) or {}
    sponsors = proto.get("sponsorCollaboratorsModule", {}) or {}
    cond = proto.get("conditionsModule", {}) or {}
    arms = proto.get("armsInterventionsModule", {}) or {}
    outcomes = proto.get("outcomesModule", {}) or {}
    nct_id = ident.get("nctId")
    return {
        "nct_id": nct_id,
        "title": ident.get("officialTitle") or ident.get("briefTitle"),
        "phase": ", ".join(design.get("phases", [])) or design.get("phase"),
        "status": status.get("overallStatus"),
        "sponsor": (sponsors.get("leadSponsor") or {}).get("name"),
        "conditions": cond.get("conditions"),
        "interventions": [
            {"name": i.get("name"), "type": i.get("type")}
            for i in (arms.get("interventions") or [])
        ],
        "primary_outcomes": [
            {"measure": o.get("measure"), "time_frame": o.get("timeFrame")}
            for o in (outcomes.get("primaryOutcomes") or [])
        ],
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "completion_date": (status.get("completionDateStruct") or {}).get("date"),
        "results_first_posted": (status.get("resultsFirstPostDateStruct") or {}).get("date"),
        "has_results": status.get("hasResults"),
        "source_url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
    }


# --------------------------------------------------------------------------- #
# Public fetchers
# --------------------------------------------------------------------------- #


def fetch_by_nct(
    nct_ids: List[str],
    drug: Optional[str] = None,
    sponsor: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch trials by NCT IDs with optional post-filter by drug/sponsor."""
    studies: List[Dict[str, Any]] = []
    failed: List[str] = []
    for nct in nct_ids:
        url = f"{_BASE}/studies/{nct}?format=json"
        r = _http_get(url)
        if not r["ok"]:
            failed.append(nct)
            continue
        try:
            data = json.loads(r["body"])
            studies.append(_flatten_trial(data))
        except (TypeError, ValueError):
            failed.append(nct)
    studies, dropped = filter_trials_by_drug_or_sponsor(studies, drug, sponsor)
    if not studies and not dropped:
        ec = "all_failed"
    elif not studies and dropped:
        ec = "all_off_topic"
    else:
        ec = "ok"
    return {
        "ok": bool(studies),
        "trials": studies,
        "discovery": "provided",
        "failed": failed,
        "dropped_off_topic": dropped,
        "error_class": ec,
    }


def search_trials(
    drug: Optional[str] = None,
    indication: Optional[str] = None,
    sponsor: Optional[str] = None,
    page_size: int = 25,
) -> Dict[str, Any]:
    """Search CT.gov by drug + indication + sponsor with post-filter."""
    params = {
        "format": "json",
        "pageSize": str(page_size),
        "filter.advanced": " AND ".join(
            filter(
                None,
                [
                    f"AREA[InterventionName]{drug}" if drug else None,
                    f"AREA[ConditionSearch]{indication}" if indication else None,
                    f"AREA[LeadSponsorName]{sponsor}" if sponsor else None,
                ],
            )
        )
        or "AREA[StudyType]Interventional",
    }
    url = f"{_BASE}/studies?" + urllib.parse.urlencode(params)
    r = _http_get(url)
    if not r["ok"]:
        return {
            "ok": False,
            "trials": [],
            "discovery": "inferred",
            "error_class": "unavailable",
            "query": params,
            "url": url,
            "dropped_off_topic": [],
        }
    try:
        data = json.loads(r["body"])
    except (TypeError, ValueError):
        return {
            "ok": False,
            "trials": [],
            "discovery": "inferred",
            "error_class": "parse_error",
            "query": params,
            "url": url,
            "dropped_off_topic": [],
        }
    studies = [_flatten_trial(s) for s in (data.get("studies") or [])]
    studies, dropped = filter_trials_by_drug_or_sponsor(studies, drug, sponsor)
    return {
        "ok": bool(studies),
        "trials": studies,
        "discovery": "inferred",
        "query": params,
        "url": url,
        "dropped_off_topic": dropped,
        "error_class": "ok" if studies else "all_off_topic",
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Fetch ClinicalTrials.gov trial data")
    p.add_argument("--nct", action="append", help="Specific NCT id (repeatable)")
    p.add_argument("--drug", help="Drug intervention name (post-filter)")
    p.add_argument("--indication", help="Indication / condition")
    p.add_argument("--sponsor", help="Lead sponsor name (post-filter)")
    p.add_argument("--out", default=None, help="Output JSON path (defaults to stdout)")
    args = p.parse_args(argv)
    if args.nct:
        out = fetch_by_nct(args.nct, drug=args.drug, sponsor=args.sponsor)
    else:
        out = search_trials(args.drug, args.indication, args.sponsor)
    text = json.dumps(out, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
