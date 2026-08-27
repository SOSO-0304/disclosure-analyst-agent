"""XML/HTML loading with explicit recovery and diagnostic reporting."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from lxml import etree, html

from disclosure_agent.domain.models import IssueSeverity, ParseIssue
from disclosure_agent.parsing.markup_repair import repair_dart_xml

_ENCODING = re.compile(rb"<\?xml[^>]+encoding=[\"']([^\"']+)", re.IGNORECASE)
_PROTECTED_REGIONS = re.compile(
    rb"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|<!DOCTYPE(?:[^>\[]|\[[\s\S]*?\])*>",
    re.DOTALL | re.IGNORECASE,
)
_TEXT_REFERENCE = re.compile(rb"&(?:amp|lt|gt|apos|quot|#[0-9]+|#x[0-9a-fA-F]+);")


def _shield_references(raw: bytes) -> tuple[bytes, dict[str, str]]:
    """libxml2 recovery can erase even valid references after a syntax error.

    Replace references with collision-free ASCII tokens for recovery only. CDATA,
    declarations and comments are opaque; they must not be unescaped afterwards.
    """
    prefix = "DARTREF" + uuid.uuid4().hex + "X"
    while prefix.encode() in raw:
        prefix = "DARTREF" + uuid.uuid4().hex + "X"
    restored: dict[str, str] = {}
    tokens: dict[bytes, bytes] = {}

    def replace(match: re.Match[bytes]) -> bytes:
        reference = match.group()
        if reference not in tokens:
            value = unescape(reference.decode("ascii"))
            if reference.startswith(b"&#"):
                # Invalid XML codepoints stay subject to ordinary diagnostics.
                number = reference[2:-1]
                try:
                    codepoint = int(number[1:], 16) if number.startswith(b"x") else int(number)
                except ValueError:
                    return reference
                if not (
                    codepoint in (9, 10, 13)
                    or 32 <= codepoint <= 0xD7FF
                    or 0xE000 <= codepoint <= 0xFFFD
                    or 0x10000 <= codepoint <= 0x10FFFF
                ):
                    return reference
                value = chr(codepoint)
            token = f"{prefix}{len(tokens)}Z"
            tokens[reference] = token.encode()
            restored[token] = value
        return tokens[reference]

    parts: list[bytes] = []
    position = 0
    for match in _PROTECTED_REGIONS.finditer(raw):
        parts.append(_TEXT_REFERENCE.sub(replace, raw[position : match.start()]))
        parts.append(match.group())
        position = match.end()
    parts.append(_TEXT_REFERENCE.sub(replace, raw[position:]))
    return b"".join(parts), restored


def _restore_references(root: etree._Element, replacements: dict[str, str]) -> None:
    if not replacements:
        return
    pattern = re.compile("|".join(map(re.escape, replacements)))

    def restore(value: str | None) -> str | None:
        return pattern.sub(lambda m: replacements[m.group()], value) if value else value

    for element in root.iter():
        if isinstance(element.tag, str):
            element.text = restore(element.text)
            for key, value in list(element.attrib.items()):
                element.set(key, restore(value))
        element.tail = restore(element.tail)


@dataclass(frozen=True, slots=True)
class LoadedMarkup:
    """Parsed markup together with diagnostics that must not be discarded."""

    root: etree._Element
    detected_encoding: str | None
    recovered: bool
    structural_recovery: bool
    issues: list[ParseIssue]


def _declared_encoding(raw: bytes) -> str | None:
    match = _ENCODING.search(raw[:512])
    return match.group(1).decode("ascii", errors="replace") if match else None


def decode_source(path: str | Path) -> tuple[str, str]:
    """Decode HTML whose declaration does not always match its actual bytes."""

    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _issues_from_error_log(
    error_log: etree._ListErrorLog,
    source_file_id: str,
) -> list[ParseIssue]:
    grouped: dict[
        tuple[IssueSeverity, str, str, str],
        dict[str, object],
    ] = {}
    for error in error_log:
        severity = (
            IssueSeverity.ERROR if error.level_name in {"ERROR", "FATAL"} else IssueSeverity.WARNING
        )
        key = (severity, error.message, error.domain_name, error.type_name)
        group = grouped.setdefault(key, {"count": 0, "examples": []})
        group["count"] = int(group["count"]) + 1
        examples = group["examples"]
        if isinstance(examples, list) and len(examples) < 5:
            examples.append({"line": error.line, "column": error.column})

    issues: list[ParseIssue] = []
    for (severity, message, domain, error_type), group in grouped.items():
        issues.append(
            ParseIssue(
                issue_code="markup_recovery",
                severity=severity,
                message=message,
                occurrence_count=int(group["count"]),
                source_file_id=source_file_id,
                details={
                    "domain": domain,
                    "type": error_type,
                    "examples": group["examples"],
                },
            )
        )
    return issues


def _xml_parser(*, recover: bool) -> etree.XMLParser:
    return etree.XMLParser(
        recover=recover,
        huge_tree=True,
        resolve_entities=False,
        no_network=True,
    )


def load_dart_xml(path: str | Path, source_file_id: str) -> LoadedMarkup:
    """Repair lexical source defects, then use structural recovery only if needed."""

    raw = Path(path).read_bytes()
    parser = _xml_parser(recover=False)
    try:
        root = etree.fromstring(raw, parser=parser)
        return LoadedMarkup(
            root=root,
            detected_encoding=_declared_encoding(raw),
            recovered=False,
            structural_recovery=False,
            issues=[],
        )
    except etree.XMLSyntaxError:
        pass

    repaired = repair_dart_xml(raw, source_file_id)
    structural_recovery = False
    parser = _xml_parser(recover=False)
    try:
        root = etree.fromstring(repaired.content, parser=parser)
        parser_issues = _issues_from_error_log(parser.error_log, source_file_id)
    except etree.XMLSyntaxError:
        structural_recovery = True
        parser = _xml_parser(recover=True)
        protected, replacements = _shield_references(repaired.content)
        root = etree.fromstring(protected, parser=parser)
        parser_issues = _issues_from_error_log(parser.error_log, source_file_id)
        if root is not None:
            _restore_references(root, replacements)
        if replacements:
            for issue in parser_issues:
                issue.details["columns_refer_to"] = "protected_parse_buffer"
            parser_issues.append(
                ParseIssue(
                    issue_code="recovery_references_protected",
                    severity=IssueSeverity.WARNING,
                    message="Text references were protected during structural XML recovery.",
                    source_file_id=source_file_id,
                    details={
                        "distinct_reference_count": len(replacements),
                        "recovery_columns_refer_to": "protected_parse_buffer",
                    },
                )
            )
    issues = [*repaired.issues, *parser_issues]
    return LoadedMarkup(
        root=root,
        detected_encoding=_declared_encoding(raw),
        recovered=bool(issues),
        structural_recovery=structural_recovery,
        issues=issues,
    )


def load_html(path: str | Path, source_file_id: str) -> LoadedMarkup:
    """Load HTML-form disclosures and retain parser diagnostics."""

    text, encoding = decode_source(path)
    parser = html.HTMLParser(recover=True, no_network=True)
    root = html.fromstring(text, parser=parser)
    issues = _issues_from_error_log(parser.error_log, source_file_id)
    return LoadedMarkup(
        root=root,
        detected_encoding=encoding,
        recovered=bool(issues),
        structural_recovery=bool(issues),
        issues=issues,
    )
