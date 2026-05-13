"""courtlistener_client.py — auth-required CourtListener wrapper with graceful degradation.

CourtListener requires a free API token (Q-017 in OPEN_QUESTIONS). The token
is expected at `02_System/engine/config/secrets.env` as
`COURTLISTENER_API_TOKEN=...` per CLAUDE.md §6.

Per the build-plan known_blockers, this skill MUST NOT crash when the token
is missing. This module returns:

    {"auth_required": True, "recoverable": True, "next_steps": "..."}

…and the orchestrator continues with priors-only enrichment.

When the token IS available, this module provides:
    fetch_docket(court, docket_number)   -> docket metadata
    fetch_search(query, **filters)       -> full-text search results
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Optional


SECRETS_CANDIDATES = [
    # Working folder mirror first (writes go here)
    os.path.join(
        "C:\\",
        "Users",
        "javie",
        "OneDrive",
        "Desktop",
        "Claude Cowork",
        "Investment tool backup skills",
        "02_System",
        "engine",
        "config",
        "secrets.env",
    ),
    # Reference folder fallback (read-only)
    os.path.join(
        "C:\\",
        "Users",
        "javie",
        "OneDrive",
        "Desktop",
        "Claude Cowork",
        "Investment tool backup",
        "02_System",
        "engine",
        "config",
        "secrets.env",
    ),
]


def _read_token_from_env_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("COURTLISTENER_API_TOKEN"):
                    if "=" in line:
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def get_token() -> Optional[str]:
    # Prefer real env var first
    tok = os.environ.get("COURTLISTENER_API_TOKEN")
    if tok:
        return tok.strip() or None
    for p in SECRETS_CANDIDATES:
        tok = _read_token_from_env_file(p)
        if tok:
            return tok
    return None


def auth_status() -> Dict:
    tok = get_token()
    if tok:
        return {"auth_required": False, "token_present": True}
    return {
        "auth_required": True,
        "recoverable": True,
        "token_present": False,
        "next_steps": (
            "Add COURTLISTENER_API_TOKEN=... to "
            "Investment tool backup skills/02_System/engine/config/secrets.env "
            "(register free at courtlistener.com/sign-in/register/) per Q-017. "
            "Skill continues with priors-only enrichment in the meantime."
        ),
        "registration_url": "https://www.courtlistener.com/sign-in/register/",
    }


def fetch_docket(court: str, docket_number: str, timeout_s: float = 8.0) -> Dict:
    """Fetch docket metadata via CourtListener REST v4.

    Returns a dict with `auth_required: True` if no token is available; the
    caller is expected to fall back to priors-only.
    """
    tok = get_token()
    if not tok:
        return auth_status()
    try:
        # Lazy import — keeps the module importable in environments without
        # network access. py_compile clean either way.
        import urllib.parse
        import urllib.request
        import json as _json

        params = urllib.parse.urlencode({"court": court, "docket_number": docket_number})
        url = f"https://www.courtlistener.com/api/rest/v4/dockets/?{params}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {tok}",
            "User-Agent": "investment-tool/skill-p5 (Pedro javiergorordo13@hotmail.com)",
        })
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        return {"auth_required": False, "ok": True, "data": payload, "source": url}
    except Exception as e:
        return {
            "auth_required": False,
            "ok": False,
            "error_class": e.__class__.__name__,
            "error_msg": str(e),
            "recoverable": True,
        }


def fetch_search(query: str, **filters) -> Dict:
    """Full-text search via /search/ endpoint."""
    tok = get_token()
    if not tok:
        return auth_status()
    try:
        import urllib.parse
        import urllib.request
        import json as _json

        q = {"q": query}
        q.update(filters)
        params = urllib.parse.urlencode(q)
        url = f"https://www.courtlistener.com/api/rest/v4/search/?{params}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {tok}",
            "User-Agent": "investment-tool/skill-p5 (Pedro javiergorordo13@hotmail.com)",
        })
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        return {"auth_required": False, "ok": True, "data": payload, "source": url}
    except Exception as e:
        return {
            "auth_required": False,
            "ok": False,
            "error_class": e.__class__.__name__,
            "error_msg": str(e),
            "recoverable": True,
        }


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(auth_status(), indent=2))
