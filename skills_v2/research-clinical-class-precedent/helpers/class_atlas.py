"""class_atlas.py — fallback class → drug lookup table for binary_catalyst skill P2.

Used by analyze.py when ChEMBL MCP is unavailable and class_drugs not provided.
Curated for the active book's binary_catalyst exposure (Axsome / Vera / Viridian and
the watchlist of upcoming PDUFAs as of 2026-04-29). Augment over time as new
classes enter the book.

Each entry maps a normalized class label to a list of (drug, brand, sponsor) tuples
of historical members of that class. The map is *not* exhaustive — it is a
defensive fallback only.

Source: openFDA cross-reference + FDA approvals database scan, manually curated.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# Class label is intentionally indication-aware: NMDA antagonists for pain are
# a different reference class than NMDA antagonists for depression.
CLASS_ATLAS: Dict[str, List[Tuple[str, str, str]]] = {
    "NMDA antagonist (depression / mood)": [
        ("dextromethorphan", "AXS-05 component", "Axsome"),
        ("esketamine", "Spravato", "Janssen"),
        ("ketamine", "Ketalar (off-label use)", "Par Pharma"),
    ],
    "NMDA antagonist (cognition / Alzheimer)": [
        ("memantine", "Namenda", "Forest"),
    ],
    "JAK inhibitor": [
        ("tofacitinib", "Xeljanz", "Pfizer"),
        ("baricitinib", "Olumiant", "Lilly"),
        ("upadacitinib", "Rinvoq", "AbbVie"),
        ("abrocitinib", "Cibinqo", "Pfizer"),
        ("ruxolitinib", "Jakafi / Opzelura", "Incyte"),
        ("ritlecitinib", "Litfulo", "Pfizer"),
        ("deucravacitinib", "Sotyktu (TYK2)", "Bristol Myers Squibb"),
    ],
    "GLP-1 receptor agonist": [
        ("liraglutide", "Victoza / Saxenda", "Novo Nordisk"),
        ("dulaglutide", "Trulicity", "Lilly"),
        ("exenatide", "Byetta / Bydureon", "AstraZeneca"),
        ("semaglutide", "Ozempic / Wegovy / Rybelsus", "Novo Nordisk"),
        ("tirzepatide", "Mounjaro / Zepbound", "Lilly"),
    ],
    "anti-amyloid monoclonal antibody": [
        ("aducanumab", "Aduhelm", "Biogen"),
        ("lecanemab", "Leqembi", "Eisai / Biogen"),
        ("donanemab", "Kisunla", "Lilly"),
    ],
    "anti-FXIa anticoagulant": [
        ("milvexian", "investigational", "BMS / Janssen"),
        ("asundexian", "investigational", "Bayer"),
        ("abelacimab", "investigational", "Anthos"),
    ],
    "anti-IL-23 (p19) monoclonal antibody": [
        ("guselkumab", "Tremfya", "Janssen"),
        ("risankizumab", "Skyrizi", "AbbVie"),
        ("tildrakizumab", "Ilumya", "Sun Pharma"),
        ("mirikizumab", "Omvoh", "Lilly"),
    ],
    "anti-VEGF (intravitreal, AMD/DME/RVO)": [
        ("ranibizumab", "Lucentis", "Genentech"),
        ("aflibercept", "Eylea", "Regeneron"),
        ("brolucizumab", "Beovu", "Novartis"),
        ("faricimab", "Vabysmo", "Genentech"),
    ],
    "IGF-1R inhibitor (thyroid eye disease)": [
        ("teprotumumab", "Tepezza", "Horizon / Amgen"),
        ("VRDN-001", "investigational", "Viridian"),
    ],
    "complement C5 inhibitor": [
        ("eculizumab", "Soliris", "Alexion / AstraZeneca"),
        ("ravulizumab", "Ultomiris", "Alexion / AstraZeneca"),
        ("crovalimab", "Piasky", "Roche"),
        ("pegcetacoplan", "Empaveli (C3) / Syfovre", "Apellis"),
    ],
    "anti-21-hydroxylase / CAH (CRF1 antagonist)": [
        ("crinecerfont", "investigational", "Neurocrine"),
        ("tildacerfont", "investigational", "Spruce Biosciences"),
        ("VERA-002 (atrasentan-like)", "investigational", "Vera Therapeutics"),
    ],
    "anti-APRIL / IgA nephropathy": [
        ("sibeprenlimab", "investigational", "Otsuka"),
        ("zigakibart", "investigational", "Novartis"),
        ("atacicept", "investigational", "Vera Therapeutics"),
    ],
}


def lookup_by_moa(moa: str) -> Tuple[str, List[Tuple[str, str, str]]]:
    """Best-effort match an MoA fragment to a class atlas entry.

    Returns (class_label, drugs). If no match, returns ("", []).
    """
    if not moa:
        return ("", [])
    needle = moa.lower()
    # Direct keyword matches
    keyword_map = [
        ("nmda", "NMDA antagonist (depression / mood)"),
        ("jak ", "JAK inhibitor"),
        ("janus kinase", "JAK inhibitor"),
        ("tyk2", "JAK inhibitor"),
        ("glp-1", "GLP-1 receptor agonist"),
        ("amyloid", "anti-amyloid monoclonal antibody"),
        ("factor xia", "anti-FXIa anticoagulant"),
        ("fxia", "anti-FXIa anticoagulant"),
        ("il-23", "anti-IL-23 (p19) monoclonal antibody"),
        ("p19", "anti-IL-23 (p19) monoclonal antibody"),
        ("vegf", "anti-VEGF (intravitreal, AMD/DME/RVO)"),
        ("igf-1r", "IGF-1R inhibitor (thyroid eye disease)"),
        ("complement c5", "complement C5 inhibitor"),
        ("c5 inhibit", "complement C5 inhibitor"),
        ("crf1", "anti-21-hydroxylase / CAH (CRF1 antagonist)"),
        ("21-hydroxylase", "anti-21-hydroxylase / CAH (CRF1 antagonist)"),
        ("april", "anti-APRIL / IgA nephropathy"),
    ]
    for kw, label in keyword_map:
        if kw in needle:
            return (label, CLASS_ATLAS.get(label, []))
    return ("", [])


def lookup_by_label(label: str) -> List[Tuple[str, str, str]]:
    """Lookup by exact (case-insensitive) label."""
    if not label:
        return []
    for k, v in CLASS_ATLAS.items():
        if k.lower() == label.lower():
            return v
    return []


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("usage: class_atlas.py <moa-or-label>", file=sys.stderr)
        sys.exit(2)
    q = " ".join(sys.argv[1:])
    label, drugs = lookup_by_moa(q)
    if not label:
        drugs = lookup_by_label(q)
        label = q if drugs else ""
    print(json.dumps({"query": q, "class_label": label, "drugs": drugs}, indent=2))
