"""Conservative lexical repair for malformed DART XML.

The archive source is never changed.  This module creates an in-memory parse
buffer that escapes only constructs which cannot be valid XML markup while
recording compact, auditable diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from disclosure_agent.domain.models import IssueSeverity, ParseIssue

_NAME_PART = rb"[A-Za-z_][A-Za-z0-9_.-]*"
_QNAME = re.compile(_NAME_PART + rb"(?::" + _NAME_PART + rb")?")
_ENTITY = re.compile(rb"&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_.:-]*);")
_PREDEFINED_ENTITIES = {b"amp", b"lt", b"gt", b"apos", b"quot"}
_MAX_EXAMPLES = 5
_KNOWN_DART_TAGS = {
    b"A",
    b"B",
    b"BODY",
    b"BR",
    b"CAPTION",
    b"COL",
    b"COLGROUP",
    b"DIV",
    b"DOCUMENT",
    b"FONT",
    b"I",
    b"IMG",
    b"LI",
    b"LIST",
    b"NOTE",
    b"P",
    b"PARAGRAPH",
    b"PGBRK",
    b"SPAN",
    b"SUB",
    b"SUP",
    b"TABLE",
    b"TABLE-GROUP",
    b"TBODY",
    b"TD",
    b"TE",
    b"TFOOT",
    b"TH",
    b"THEAD",
    b"TITLE",
    b"TR",
    b"TU",
    b"U",
    b"WARNING",
}


@dataclass(frozen=True, slots=True)
class XmlRepairResult:
    """Repaired parse bytes and aggregated repair diagnostics."""

    content: bytes
    issues: list[ParseIssue]

    @property
    def changed(self) -> bool:
        return bool(self.issues)


class _IssueCollector:
    def __init__(self, raw: bytes, source_file_id: str) -> None:
        self.raw = raw
        self.source_file_id = source_file_id
        self._counts: dict[tuple[str, str], int] = {}
        self._examples: dict[tuple[str, str], list[dict[str, object]]] = {}

    def record(
        self,
        issue_code: str,
        message: str,
        offset: int,
        original: bytes,
    ) -> None:
        key = (issue_code, message)
        self._counts[key] = self._counts.get(key, 0) + 1
        examples = self._examples.setdefault(key, [])
        if len(examples) >= _MAX_EXAMPLES:
            return

        previous_newline = self.raw.rfind(b"\n", 0, offset)
        examples.append(
            {
                "line": self.raw.count(b"\n", 0, offset) + 1,
                "byte_column": offset - previous_newline,
                "original": _decode_preview(original),
            }
        )

    def build(self) -> list[ParseIssue]:
        issues: list[ParseIssue] = []
        for (issue_code, message), count in sorted(self._counts.items()):
            issues.append(
                ParseIssue(
                    issue_code=issue_code,
                    severity=IssueSeverity.WARNING,
                    message=message,
                    source_file_id=self.source_file_id,
                    occurrence_count=count,
                    details={"examples": self._examples[(issue_code, message)]},
                )
            )
        return issues


def _decode_preview(value: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _valid_qname(value: bytes) -> bool:
    return _QNAME.fullmatch(value) is not None


def _is_valid_tag(token: bytes) -> bool:
    """Return whether bytes between angle brackets form valid XML tag syntax."""

    token = token.strip()
    if not token:
        return False

    if token.startswith(b"/"):
        closing_name = token[1:].strip()
        return _valid_qname(closing_name)

    if token.endswith(b"/"):
        token = token[:-1].rstrip()

    name_match = _QNAME.match(token)
    if name_match is None:
        return False
    name = name_match.group(0)
    if not _valid_qname(name):
        return False

    position = name_match.end()
    length = len(token)
    while position < length:
        whitespace_start = position
        while position < length and token[position : position + 1].isspace():
            position += 1
        if position == length:
            return True
        if position == whitespace_start:
            return False

        attribute_match = _QNAME.match(token, position)
        if attribute_match is None or not _valid_qname(attribute_match.group(0)):
            return False
        position = attribute_match.end()
        while position < length and token[position : position + 1].isspace():
            position += 1
        if position >= length or token[position] != ord("="):
            return False
        position += 1
        while position < length and token[position : position + 1].isspace():
            position += 1
        if position >= length or token[position] not in {ord('"'), ord("'")}:
            return False
        quote = token[position]
        position += 1
        closing_quote = token.find(bytes((quote,)), position)
        if closing_quote < 0:
            return False
        position = closing_quote + 1
    return True


def _is_known_dart_tag(token: bytes) -> bool:
    token = token.strip().lstrip(b"/").lstrip()
    name_match = _QNAME.match(token)
    if name_match is None:
        return False
    name = name_match.group(0).upper()
    return name in _KNOWN_DART_TAGS or re.fullmatch(rb"SECTION-[0-9]+", name) is not None


def _find_tag_end(raw: bytes, start: int) -> int | None:
    quote: int | None = None
    for position in range(start + 1, len(raw)):
        current = raw[position]
        if quote is not None:
            if current == quote:
                quote = None
            continue
        if current in {ord('"'), ord("'")}:
            quote = current
        elif current == ord(">"):
            return position
        elif current == ord("<"):
            # A second opener means the first one was literal text such as
            # ``value < 10`` immediately before a real closing tag.
            return None
        elif current in {ord("\n"), ord("\r")}:
            # DART tags may span lines, so a newline alone does not terminate a tag.
            continue
    return None


def _find_doctype_end(raw: bytes, start: int) -> int | None:
    quote: int | None = None
    subset_depth = 0
    for position in range(start + 2, len(raw)):
        current = raw[position]
        if quote is not None:
            if current == quote:
                quote = None
            continue
        if current in {ord('"'), ord("'")}:
            quote = current
        elif current == ord("["):
            subset_depth += 1
        elif current == ord("]") and subset_depth:
            subset_depth -= 1
        elif current == ord(">") and subset_depth == 0:
            return position
    return None


def _repair_entities(
    segment: bytes,
    *,
    base_offset: int,
    collector: _IssueCollector,
) -> bytes:
    output = bytearray()
    position = 0
    while position < len(segment):
        ampersand = segment.find(b"&", position)
        if ampersand < 0:
            output.extend(segment[position:])
            break
        if ampersand > position:
            output.extend(segment[position:ampersand])
            position = ampersand
            continue

        entity_match = _ENTITY.match(segment, position)
        if entity_match is None:
            output.extend(b"&amp;")
            collector.record(
                "bare_ampersand_repaired",
                "A bare ampersand was escaped in the parse buffer.",
                base_offset + position,
                segment[position : position + 40],
            )
            position += 1
            continue

        entity = entity_match.group(0)
        body = entity[1:-1]
        if body.startswith(b"#") or body in _PREDEFINED_ENTITIES:
            output.extend(entity)
        elif body.lower() == b"nbsp":
            output.extend(b"&#160;")
            collector.record(
                "named_entity_normalized",
                "The HTML nbsp entity was converted to a numeric XML entity.",
                base_offset + position,
                entity,
            )
        else:
            # A DART DTD may define additional named entities.  Their semantic
            # value cannot be inferred safely here, so valid syntax is retained.
            output.extend(entity)
        position = entity_match.end()
    return bytes(output)


def repair_dart_xml(raw: bytes, source_file_id: str) -> XmlRepairResult:
    """Create a conservative parse buffer without mutating the source bytes."""

    collector = _IssueCollector(raw, source_file_id)
    output = bytearray()
    position = 0

    while position < len(raw):
        next_ampersand = raw.find(b"&", position)
        next_angle = raw.find(b"<", position)
        candidates = [index for index in (next_ampersand, next_angle) if index >= 0]
        next_special = min(candidates) if candidates else len(raw)
        if next_special > position:
            output.extend(raw[position:next_special])
            position = next_special
            continue

        if raw[position] == ord("&"):
            next_markup = raw.find(b"<", position)
            segment_end = len(raw) if next_markup < 0 else next_markup
            output.extend(
                _repair_entities(
                    raw[position:segment_end],
                    base_offset=position,
                    collector=collector,
                )
            )
            position = segment_end
            continue

        if raw[position] != ord("<"):
            output.append(raw[position])
            position += 1
            continue

        if raw.startswith(b"<!--", position):
            end = raw.find(b"-->", position + 4)
            end = len(raw) if end < 0 else end + 3
            output.extend(raw[position:end])
            position = end
            continue

        if raw.startswith(b"<![CDATA[", position):
            end = raw.find(b"]]>", position + 9)
            end = len(raw) if end < 0 else end + 3
            output.extend(raw[position:end])
            position = end
            continue

        if raw.startswith(b"<?", position):
            end = raw.find(b"?>", position + 2)
            end = len(raw) if end < 0 else end + 2
            output.extend(raw[position:end])
            position = end
            continue

        if raw[position : position + 9].upper() == b"<!DOCTYPE":
            end = _find_doctype_end(raw, position)
            end = len(raw) if end is None else end + 1
            output.extend(raw[position:end])
            position = end
            continue

        tag_end = _find_tag_end(raw, position)
        if tag_end is None:
            output.extend(b"&lt;")
            collector.record(
                "pseudo_tag_repaired",
                "An unterminated angle bracket was preserved as literal text.",
                position,
                raw[position : position + 80],
            )
            position += 1
            continue

        token = raw[position + 1 : tag_end]
        if token.lstrip().startswith(b"!"):
            output.extend(raw[position : tag_end + 1])
        elif _is_valid_tag(token) or _is_known_dart_tag(token):
            output.append(ord("<"))
            output.extend(
                _repair_entities(
                    token,
                    base_offset=position + 1,
                    collector=collector,
                )
            )
            output.append(ord(">"))
        else:
            output.extend(b"&lt;")
            output.extend(
                _repair_entities(
                    token,
                    base_offset=position + 1,
                    collector=collector,
                )
            )
            output.extend(b"&gt;")
            collector.record(
                "pseudo_tag_repaired",
                "A non-XML angle-bracket expression was preserved as literal text.",
                position,
                raw[position : tag_end + 1],
            )
        position = tag_end + 1

    return XmlRepairResult(content=bytes(output), issues=collector.build())
