"""Event deduplication for harvest-historical-events.

`event_id` derivation rules per profile/source — see SKILL.md §3.

  - SEC EDGAR:   sha1(accession + cik_padded + form_type)[:24]
  - FDA:         sha1(application_number + submission_type + submission_status_date)[:24]
  - Insider:     sha1(issuer_cik + cluster_window_start + cluster_window_end + cluster_kind)[:24]
  - Litigation:  sha1(docket_id + court_id)[:24]

Across-source ID collision is structurally impossible because the input
keyspaces are disjoint. We still hash with a per-source prefix as defense in
depth.

CLI:
    python event_dedupe.py --in events.json [--in events2.json] --out merged.json
"""

from __future__ import annotations

import hashlib
import json
import sys


def event_id_sec(accession: str, cik: str, form_type: str) -> str:
    cik_padded = str(cik).zfill(10)
    return hashlib.sha1(f"sec|{accession}|{cik_padded}|{form_type}".encode()).hexdigest()[:24]


def event_id_fda(application_number: str, submission_type: str, submission_status_date: str) -> str:
    return hashlib.sha1(
        f"fda|{application_number}|{submission_type}|{submission_status_date}".encode()
    ).hexdigest()[:24]


def event_id_insider_cluster(issuer_cik: str, window_start: str, window_end: str, kind: str) -> str:
    cik_padded = str(issuer_cik).zfill(10)
    return hashlib.sha1(
        f"ins|{cik_padded}|{window_start}|{window_end}|{kind}".encode()
    ).hexdigest()[:24]


def event_id_litigation(docket_id: str, court_id: str) -> str:
    return hashlib.sha1(f"lit|{docket_id}|{court_id}".encode()).hexdigest()[:24]


def dedupe_events(*event_lists) -> list:
    """Merge multiple event lists, dropping duplicates by event_id.

    First occurrence wins. Returns a new list, original lists untouched.
    """
    seen = set()
    out = []
    for events in event_lists:
        for ev in events or []:
            eid = ev.get("event_id")
            if not eid:
                continue
            if eid in seen:
                continue
            seen.add(eid)
            out.append(ev)
    return out


def dedupe_against_existing(new_events: list, existing_events: list) -> list:
    """Return only new_events whose event_id is not in existing_events."""
    seen = {ev.get("event_id") for ev in (existing_events or []) if ev.get("event_id")}
    return [ev for ev in new_events if ev.get("event_id") and ev["event_id"] not in seen]


def load_events_file(path: str) -> list:
    """Load events from either a top-level list or {"events": [...]} shape."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "events" in data:
        return data["events"]
    return []


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Merge + dedupe event JSON files")
    p.add_argument("--in", dest="inputs", action="append", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    lists = [load_events_file(p) for p in args.inputs]
    merged = dedupe_events(*lists)

    payload = {"events": merged, "_inputs": args.inputs, "_count": len(merged)}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(json.dumps({"merged_count": len(merged), "inputs": args.inputs}, indent=2))
    sys.exit(0)
