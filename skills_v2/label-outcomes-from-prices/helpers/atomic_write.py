"""Atomic write helpers for label-outcomes-from-prices.

Mirrors harvest-historical-events/helpers/atomic_write.py. Writes content to
a temp file in the same directory, fsyncs, then os.replace's into place.
Per D-052 and CLAUDE.md §3.4.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile


def atomic_write_text(final_path: str, content: str) -> None:
    final_dir = os.path.dirname(os.path.abspath(final_path)) or "."
    os.makedirs(final_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp.", suffix=os.path.basename(final_path), dir=final_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(final_path: str, data, indent: int = 2) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=False)
    atomic_write_text(final_path, text + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: atomic_write.py <path>", file=sys.stderr)
        sys.exit(2)
    atomic_write_text(sys.argv[1], sys.stdin.read())
