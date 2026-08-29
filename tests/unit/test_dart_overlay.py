from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.domain.models import (
    CanonicalDocument,
    CompanyIdentity,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    IssueSeverity,
    ParseIssue,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceRole,
)
from disclosure_agent.parsing.dart_overlay import is_dart_overlay_candidate
from disclosure_agent.storage.jsonl import read_effective_canonical, write_canonical


def _package(
    filing_id: str,
    receipt_number: str,
    *,
    status: ParseStatus,
    parser_name: str = "DartParser",
    parser_version: str = "2.2.0",
    markup_recovery: bool = False,
    corp_code: str = "00000001",
) -> FilingPackage:
    source_id = f"{filing_id}:source:1"
    issues = (
        [
            ParseIssue(
                issue_code="markup_recovery",
                severity=IssueSeverity.ERROR,
                message="malformed source",
                source_file_id=source_id,
            )
        ]
        if markup_recovery
        else []
    )
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code=corp_code,
            stock_code="000001",
            corp_name="테스트",
            listed_name="테스트",
            industry="테스트",
            sector="테스트",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.MAJOR,
            document_subtype="테스트",
            report_name_raw="테스트 공시",
            receipt_number=receipt_number,
            receipt_date=date(2024, 1, 1),
            filer_name="테스트",
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw=f"raw/{receipt_number}.xml",
                archive_path_normalized=f"raw/{receipt_number}.xml",
                file_name=f"{receipt_number}.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                is_primary=True,
            )
        ],
        documents=[
            CanonicalDocument(
                document_id=f"{filing_id}:primary_report",
                filing_id=filing_id,
                document_role=SourceRole.PRIMARY_REPORT,
                primary_source_file_id=source_id,
                source_file_ids=[source_id],
                parse_summary=ParseSummary(
                    status=status,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    error_count=1 if markup_recovery else 0,
                ),
                parse_issues=issues,
            )
        ],
    )


def test_overlay_candidate_requires_partial_dart_markup_recovery() -> None:
    assert is_dart_overlay_candidate(
        _package(
            "a",
            "20240101000001",
            status=ParseStatus.PARTIAL,
            markup_recovery=True,
        )
    )
    assert not is_dart_overlay_candidate(
        _package(
            "b",
            "20240101000002",
            status=ParseStatus.SUCCESS,
            markup_recovery=True,
        )
    )
    assert not is_dart_overlay_candidate(
        _package(
            "c",
            "20240101000003",
            status=ParseStatus.PARTIAL,
            parser_name="ExchangeParser",
            markup_recovery=True,
        )
    )


def test_effective_canonical_replaces_by_filing_id_and_keeps_base_order(tmp_path: Path) -> None:
    base_path = tmp_path / "base.jsonl"
    overlay_path = tmp_path / "overlay.jsonl"
    base_a = _package(
        "a",
        "20240101000001",
        status=ParseStatus.PARTIAL,
        markup_recovery=True,
    )
    base_b = _package("b", "20240101000002", status=ParseStatus.SUCCESS)
    replacement = _package(
        "a",
        "20240101000001",
        status=ParseStatus.SUCCESS,
        parser_version="2.2.1",
    )
    write_canonical(base_path, [base_a, base_b])
    write_canonical(overlay_path, [replacement])

    effective = list(read_effective_canonical(base_path, overlay_path))

    assert [package.filing_id for package in effective] == ["a", "b"]
    assert effective[0].documents[0].parse_summary.parser_version == "2.2.1"
    assert effective[1].documents[0].parse_summary.parser_version == "2.2.0"


def test_effective_canonical_rejects_overlay_receipt_mismatch(tmp_path: Path) -> None:
    base_path = tmp_path / "base.jsonl"
    overlay_path = tmp_path / "overlay.jsonl"
    write_canonical(
        base_path,
        [_package("a", "20240101000001", status=ParseStatus.PARTIAL, markup_recovery=True)],
    )
    write_canonical(
        overlay_path,
        [_package("a", "20240101000099", status=ParseStatus.SUCCESS, parser_version="2.2.1")],
    )

    with pytest.raises(ValueError, match="receipt mismatch"):
        list(read_effective_canonical(base_path, overlay_path))
