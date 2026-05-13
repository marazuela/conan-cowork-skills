"""Sparse-density evaluation for K-NN reference classes.

Maps the count of usable neighbors to (use, confidence, warnings).
Centralizes the §8 decision tree from SKILL.md so the orchestrator
just calls evaluate_density(neighbors_kept, neighbors_resolved).
"""
from __future__ import annotations

from typing import Any


def evaluate_density(
    n_neighbors_kept: int,
    n_neighbors_resolved: int,
    n_with_sidecar: int,
    k_requested: int = 5,
) -> dict[str, Any]:
    """Returns:
        {
          "use": bool,
          "confidence": float,
          "warnings": [str],
          "error_class": str | None,
        }

    n_neighbors_kept: how many we actually have for distance ranking
    n_neighbors_resolved: how many bucket events have non-PENDING outcome
    n_with_sidecar: how many of those have iter-4 sidecar overlay
    """
    warnings: list[str] = []
    error_class: str | None = None
    use = True
    confidence = 0.85

    if n_neighbors_kept == 0:
        use = False
        confidence = 0.0
        error_class = "no_neighbors"
        warnings.append(
            "No neighbors found in reference universe. Consider broadening filters."
        )
        return {
            "use": use,
            "confidence": confidence,
            "warnings": warnings,
            "error_class": error_class,
        }

    if n_neighbors_kept >= k_requested:
        confidence = 0.85
    elif n_neighbors_kept >= 3:
        confidence = 0.65
        warnings.append(
            f"low_density_reference_class=true (k_returned={n_neighbors_kept}<k_requested={k_requested})"
        )
    elif n_neighbors_kept == 2:
        confidence = 0.45
        warnings.append("base_rate_unstable=true (only 2 neighbors)")
    elif n_neighbors_kept == 1:
        confidence = 0.25
        warnings.append(
            "single_precedent=true — useful as anecdote, not as base rate"
        )

    # Downgrade if sidecar coverage is poor
    if n_with_sidecar < 0.5 * n_neighbors_resolved and n_neighbors_resolved > 0:
        confidence = max(0.45, confidence - 0.15)
        warnings.append(
            f"sidecar_coverage_low (n_with_sidecar={n_with_sidecar}, "
            f"n_resolved={n_neighbors_resolved}) — distance computed mostly on base features"
        )

    return {
        "use": use,
        "confidence": round(confidence, 4),
        "warnings": warnings,
        "error_class": error_class,
    }


if __name__ == "__main__":
    import json
    for k in [0, 1, 2, 3, 5, 7]:
        print(k, json.dumps(evaluate_density(k, max(k, 5), max(k, 5))))
