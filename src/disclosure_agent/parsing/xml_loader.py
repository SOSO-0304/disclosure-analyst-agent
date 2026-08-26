"""XML/HTML loading with explicit recovery and diagnostic reporting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree, html

from disclosure_agent.domain.models import IssueSeverity, ParseIssue

_ENCODING = re.compile(rb"<\?xml[^>]+encoding=[\"']([^\"']+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LoadedMarkup:
    """Parsed markup together with diagnostics that must not be discarded."""

    root: etree._Element
    detected_encoding: str | None
    recovered: bool
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
    issues: list[ParseIssue] = []
    for error in error_log:
        severity = (
            IssueSeverity.ERROR if error.level_name in {"ERROR", "FATAL"} else IssueSeverity.WARNING
        )
        issues.append(
            ParseIssue(
                issue_code="markup_recovery",
                severity=severity,
                message=error.message,
                source_file_id=source_file_id,
                details={
                    "line": error.line,
                    "column": error.column,
                    "domain": error.domain_name,
                    "type": error.type_name,
                },
            )
        )
    return issues


def load_dart_xml(path: str | Path, source_file_id: str) -> LoadedMarkup:
    """Load DART XML while retaining every lxml recovery warning."""

    raw = Path(path).read_bytes()
    parser = etree.XMLParser(
        recover=True,
        huge_tree=True,
        resolve_entities=False,
        no_network=True,
    )
    root = etree.fromstring(raw, parser=parser)
    issues = _issues_from_error_log(parser.error_log, source_file_id)
    return LoadedMarkup(
        root=root,
        detected_encoding=_declared_encoding(raw),
        recovered=bool(issues),
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
        issues=issues,
    )
