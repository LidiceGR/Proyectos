"""Shared file I/O helpers for Capstone artifact processors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_sql_text(path: str | Path) -> str:
    """Read SQL scripts without changing their original encoding on disk.

    UTF-8, UTF-8 with BOM, UTF-16 with BOM, and Windows-1252 are supported.
    The fallback to latin-1 is used only when all preferred codecs fail.
    """
    payload = Path(path).read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    for encoding in ("utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")


def write_json(path: str | Path, document: Any) -> None:
    """Write indented UTF-8 JSON, creating parent directories if necessary."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
