"""Conservative lexical repair for malformed DART XML.

The archive source is never changed.  This module creates an in-memory parse
buffer that escapes only constructs which cannot be valid XML markup while
recording compact, auditable diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.entities import html5

from disclosure_agent.domain.models import IssueSeverity, ParseIssue

_NAME_PART = rb"[A-Za-z_][A-Za-z0-9_.-]*"
_QNAME = re.compile(_NAME_PART + rb"(?::" + _NAME_PART + rb")?")
_ENTITY = re.compile(rb"&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_.:-]*);")
_PREDEFINED_ENTITIES = {b"amp", b"lt", b"gt", b"apos", b"quot"}
_ENG_ASSIGNMENT = re.compile(rb"\bENG\s*=\s*([\"'])", re.IGNORECASE)
_NEXT_ATTRIBUTE = re.compile(rb"\s+" + _NAME_PART + rb"(?::" + _NAME_PART + rb")?\s*=")
_MAX_EXAMPLES = 5
# Reviewed labels observed in the corpus. Unknown extension tags are not guessed.
_LITERAL_LABELS = {
    b"BGMI",
    b"STS",
    b"CG",
    b"MANIFESTO",
    b"GranData",
    b"DataBada",
    b"DREAM",
    b"SIT",
    b"IIT",
    b"SHEESH",
}
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
        self.declared_entities = set(re.findall(rb"<!ENTITY\s+([\w:.-]+)\s", raw))
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
            # Apostrophes in <신설 '23. 3.16.> are prose, not attributes.
            previous = position - 1
            while previous > start and raw[previous : previous + 1].isspace():
                previous -= 1
            if raw[previous : previous + 1] == b"=":
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
        elif body in collector.declared_entities:
            output.extend(entity)
        elif (value := html5.get(body.decode("ascii") + ";")) is not None:
            output.extend("".join(f"&#{ord(char)};" for char in value).encode("ascii"))
            collector.record(
                "named_entity_normalized",
                "An HTML named entity was converted to numeric XML references.",
                base_offset + position,
                entity,
            )
        else:
            output.extend(b"&amp;" + entity[1:])
            collector.record(
                "undefined_entity_preserved",
                "An undefined entity was retained as literal text, not expanded.",
                base_offset + position,
                entity,
            )
        position = entity_match.end()
    return bytes(output)


def _repair_eng_attribute_quotes(
    token: bytes,
    *,
    base_offset: int,
    collector: _IssueCollector,
) -> bytes:
    """Preserve stray raw quotes inside malformed DART ``ENG`` attributes.

    Several real filings contain values such as ``ENG=""Snow Corporation"`` or
    ``ENG="Accrued Expenses""``.  libxml recovery can then truncate the enclosing
    table.  For the reviewed ENG attribute only, use the outermost quote as the
    XML delimiter and encode any interior raw quote as an XML entity.  The source
    bytes remain untouched and the malformed quote itself is not discarded.
    """

    assignment = _ENG_ASSIGNMENT.search(token)
    if assignment is None:
        return token

    quote = assignment.group(1)
    value_start = assignment.end()
    boundary = len(token)
    for candidate in _NEXT_ATTRIBUTE.finditer(token, value_start):
        before = token[value_start : candidate.start()].rstrip()
        if before.endswith(quote):
            boundary = candidate.start()
            break

    segment = token[value_start:boundary]
    closing_quote = segment.rfind(quote)
    if closing_quote < 0:
        return token
    value = segment[:closing_quote]
    if quote not in value:
        return token

    entity = b"&quot;" if quote == b'"' else b"&apos;"
    repaired_value = value.replace(quote, entity)
    repaired = (
        token[:value_start]
        + repaired_value
        + quote
        + segment[closing_quote + 1 :]
        + token[boundary:]
    )
    collector.record(
        "malformed_eng_attribute_quote_preserved",
        (
            "Raw quote characters inside a malformed ENG attribute were encoded "
            "in the parse buffer."
        ),
        base_offset + assignment.start(),
        token[assignment.start() : boundary],
    )
    return repaired


def repair_dart_xml(raw: bytes, source_file_id: str) -> XmlRepairResult:
    """Create a conservative parse buffer without mutating the source bytes."""

    collector = _IssueCollector(raw, source_file_id)
    output = bytearray()
    position = 0
    unpaired_labels = {
        name
        for name in _LITERAL_LABELS
        if re.search(rb"</" + re.escape(name) + rb"\s*>", raw) is None
    }

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
        if _is_known_dart_tag(token):
            token = _repair_eng_attribute_quotes(
                token,
                base_offset=position + 1,
                collector=collector,
            )
            # Repair only an extra terminal quote after a complete non-ENG attribute.
            # Do not reinterpret arbitrary malformed structural tags as prose.
            fixed = re.sub(rb"(=\s*\"[^\"<>]*\")\"(\s*/?\s*)$", rb"\1\2", token)
            fixed = re.sub(rb"(=\s*'[^'<>]*')'(\s*/?\s*)$", rb"\1\2", fixed)
            if fixed != token:
                collector.record(
                    "duplicate_attribute_quote_repaired",
                    "An extra terminal attribute quote was removed in the parse buffer.",
                    position,
                    raw[position : tag_end + 1],
                )
                token = fixed
        literal_label = token.strip() in unpaired_labels
        if token.lstrip().startswith(b"!"):
            output.extend(raw[position : tag_end + 1])
        elif not literal_label and (_is_valid_tag(token) or _is_known_dart_tag(token)):
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
