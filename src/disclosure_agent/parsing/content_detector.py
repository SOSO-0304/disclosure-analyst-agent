"""Detect source content independently from potentially misleading extensions."""

from __future__ import annotations

import re
from pathlib import Path

from disclosure_agent.domain.models import ContentFormat, ParserProfile, SourceRole

_ROOT_TAG = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)\b")


def decode_prefix(raw: bytes) -> str:
    """Decode enough source text for format detection."""

    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_content_format(path: str | Path) -> ContentFormat:
    """Inspect source bytes and return the actual content family."""

    source = Path(path)
    with source.open("rb") as stream:
        prefix = stream.read(32_768)

    if prefix.startswith(b"%PDF-"):
        return ContentFormat.PDF

    text = decode_prefix(prefix).lstrip()
    lowered = text.lower()
    if lowered.startswith("<!doctype html") or "<html" in lowered[:4096]:
        return ContentFormat.HTML

    without_declaration = re.sub(r"^<\?xml[^>]*>\s*", "", text, count=1, flags=re.IGNORECASE)
    without_doctype = re.sub(
        r"^<!DOCTYPE[^>]*>\s*",
        "",
        without_declaration,
        count=1,
        flags=re.IGNORECASE,
    )
    root_match = _ROOT_TAG.search(without_doctype)
    if root_match:
        root_name = root_match.group(1).lower()
        if root_name == "html":
            return ContentFormat.HTML
        if root_name == "document":
            return ContentFormat.DART_XML

    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return ContentFormat.PDF
    if suffix in {".html", ".htm"}:
        return ContentFormat.HTML
    return ContentFormat.UNKNOWN


def choose_parser_profile(
    content_format: ContentFormat,
    source_role: SourceRole,
) -> ParserProfile:
    """Choose a parser only after content detection and role assignment."""

    if content_format is ContentFormat.DART_XML:
        return ParserProfile.DART_DOCUMENT_XML
    if content_format is ContentFormat.PDF:
        return ParserProfile.PDF_TEXT
    if content_format is ContentFormat.HTML:
        if source_role is SourceRole.COMPANION_VIEWER:
            return ParserProfile.COMPANION_HTML
        return ParserProfile.XFORMS_HTML
    return ParserProfile.UNKNOWN
