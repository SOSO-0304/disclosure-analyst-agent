"""XML/HTML loading with explicit recovery and diagnostic reporting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree, html

from disclosure_agent.domain.models import IssueSeverity, ParseIssue
from disclosure_agent.parsing.markup_repair import repair_dart_xml

_ENCODING = re.compile(rb"<\?xml[^>]+encoding=[\"']([^\"']+)", re.IGNORECASE)


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
        root = etree.fromstring(repaired.content, parser=parser)
        parser_issues = _issues_from_error_log(parser.error_log, source_file_id)
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
