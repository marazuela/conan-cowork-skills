"""Atomic write utility per D-052.

Write to <final>.tmp.<pid>, then os.replace. Avoids partial-write corruption.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Union


def atomic_write_text(path: Union[str, Path], content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_json(path: Union[str, Path], obj: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False))
