"""Robust XML/HTML loading utilities for the supplied corpus."""
from __future__ import annotations

from pathlib import Path
from lxml import etree, html


def decode_source(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    # Exchange files can declare euc-kr while the actual bytes are UTF-8.
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_exchange_html(path: str | Path) -> etree._Element:
    return html.fromstring(decode_source(path))


def load_dart_xml(path: str | Path) -> etree._Element:
    text = decode_source(path)
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    return etree.fromstring(text.encode("utf-8"), parser=parser)
