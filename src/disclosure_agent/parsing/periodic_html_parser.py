"""Extract metadata from DART viewer shells without mistaking it for report body."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from disclosure_agent.parsing.xml_loader import decode_source

_NODE_BLOCK = re.compile(
    r"var\s+node\d+\s*=\s*\{\};(?P<body>.*?)(?=var\s+node\d+\s*=\s*\{\};|$)",
    re.DOTALL,
)
_PROPERTY = re.compile(
    r"node\d+\[['\"](?P<key>\w+)['\"]\]\s*=\s*['\"](?P<value>.*?)['\"]\s*;",
    re.DOTALL,
)


def parse_viewer_metadata(path: str | Path) -> dict[str, Any]:
    """Return DART TOC/offset metadata; do not emit it as filing body text."""

    text, encoding = decode_source(path)
    toc: list[dict[str, Any]] = []
    for match in _NODE_BLOCK.finditer(text):
        values = {
            item.group("key"): item.group("value").strip()
            for item in _PROPERTY.finditer(match.group("body"))
        }
        if "text" not in values or "rcpNo" not in values:
            continue
        for numeric_key in ("id", "eleId", "offset", "length", "tocNo"):
            if values.get(numeric_key, "").isdigit():
                values[numeric_key] = int(values[numeric_key])
        toc.append(values)
    return {
        "viewer_encoding": encoding,
        "viewer_toc": toc,
        "viewer_is_companion_only": True,
    }
