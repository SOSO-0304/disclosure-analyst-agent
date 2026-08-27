"""Unit tests for the loss-minimising canonical domain contract."""

from __future__ import annotations

import json
import unicodedata

import pytest
from pydantic import ValidationError

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CompanyIdentity,
    ContentFormat,
    CorpusManifestEntry,
    CorrectionMetadata,
    CorrectionType,
    FilingMetadata,
    FilingPackage,
    ParserProfile,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceRole,
    TableCell,
    TableData,
    company_from_manifest,
    correction_from_manifest,
    filing_from_manifest,
)


def manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "doc_id": "periodic-00126380-20240312000736",
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "listed_name": "삼성전자",
        "stock_code": "005930",
        "industry": "제조업",
        "sector": "IT",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2023.12)",
        "is_correction": False,
        "rcept_no": "20240312000736",
        "rcept_dt": "20240312",
        "flr_nm": "삼성전자",
        "base_year": 2023,
        "base_month": 12,
        "file_path": "corpus/reports/periodic/삼성전자/20240312000736/",
        "file_format": "xml",
        "n_files": 3,
    }
    payload.update(overrides)
    return payload


def build_source(
    source_id: str,
    role: SourceRole,
    *,
    primary: bool = False,
    companion_to: str | None = None,
    content_format: ContentFormat = ContentFormat.DART_XML,
    parser_profile: ParserProfile = ParserProfile.DART_DOCUMENT_XML,
    extension: str = "xml",
) -> SourceFile:
    file_name = f"{source_id}.{extension}"
    return SourceFile(
        source_file_id=source_id,
        archive_path_raw=f"corpus/reports/periodic/삼성전자/rcept/{file_name}",
        archive_path_normalized=f"corpus/reports/periodic/삼성전자/rcept/{file_name}",
        file_name=file_name,
        source_role=role,
        declared_extension=extension,
        detected_content_format=content_format,
        parser_profile=parser_profile,
        is_primary=primary,
        companion_to_source_file_id=companion_to,
    )


@pytest.mark.parametrize(
    ("group", "subtype"),
    [
        ("periodic", "annual"),
        ("major", None),
        ("exchange", "contract"),
        ("holding", None),
    ],
)
def test_all_four_corpus_groups_are_supported(group: str, subtype: str | None) -> None:
    overrides: dict[str, object] = {
        "doc_id": f"{group}-00126380-20240312000736",
        "doc_group": group,
        "doc_subtype": subtype,
    }
    if group != "periodic":
        overrides.update(base_year=None, base_month=None, n_files=1)

    entry = CorpusManifestEntry.model_validate(manifest_payload(**overrides))

    assert entry.doc_group.value == group
    assert entry.doc_subtype == subtype


def test_periodic_manifest_requires_base_period() -> None:
    with pytest.raises(ValidationError, match="require base_year and base_month"):
        CorpusManifestEntry.model_validate(manifest_payload(base_year=None, base_month=None))


def test_manifest_preserves_raw_path_and_exposes_normalized_lookup_path() -> None:
    decomposed_name = unicodedata.normalize("NFD", "삼성전자")
    entry = CorpusManifestEntry.model_validate(
        manifest_payload(
            corp_name=decomposed_name,
            listed_name=decomposed_name,
            file_path=f"corpus\\reports\\periodic\\{decomposed_name}\\receipt",
        )
    )

    assert entry.corp_name == "삼성전자"
    assert entry.file_path == f"corpus\\reports\\periodic\\{decomposed_name}\\receipt"
    assert entry.normalized_file_path == "corpus/reports/periodic/삼성전자/receipt"
    assert unicodedata.is_normalized("NFC", entry.normalized_file_path)


def test_table_preserves_empty_cells_and_merged_cell_spans() -> None:
    table = TableData(
        table_id="table-1",
        row_count=2,
        column_count=3,
        header_row_indices=[0],
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                column_span=2,
                is_header=True,
                text_raw="구분",
                text_normalized="구분",
            ),
            TableCell(
                row_index=0,
                column_index=2,
                is_header=True,
                text_raw="금액",
                text_normalized="금액",
            ),
            TableCell(
                row_index=1,
                column_index=0,
                text_raw="",
                text_normalized="",
            ),
            TableCell(
                row_index=1,
                column_index=1,
                text_raw="매출액",
                text_normalized="매출액",
            ),
            TableCell(
                row_index=1,
                column_index=2,
                text_raw="1,000",
                text_normalized="1,000",
                numeric_value="1000",
                unit_raw="백만원",
            ),
        ],
    )

    assert table.cells[0].column_span == 2
    assert table.cells[2].text_raw == ""
    assert str(table.cells[4].numeric_value) == "1000"


def test_table_rejects_duplicate_coordinates() -> None:
    with pytest.raises(ValidationError, match="duplicate table-cell coordinate"):
        TableData(
            table_id="table-1",
            row_count=1,
            column_count=1,
            cells=[
                TableCell(row_index=0, column_index=0),
                TableCell(row_index=0, column_index=0),
            ],
        )


def test_table_block_requires_table_payload() -> None:
    with pytest.raises(ValidationError, match="table blocks require table data"):
        CanonicalBlock(
            block_id="block-1",
            order=0,
            block_type=BlockType.TABLE,
        )


def test_correction_lineage_can_remain_unresolved_during_initial_parse() -> None:
    correction = CorrectionMetadata(
        is_correction=True,
        correction_type=CorrectionType.FILING_CORRECTION,
        correction_reason_raw="기재정정",
    )

    assert correction.original_receipt_number is None


def test_periodic_filing_package_supports_three_semantic_documents() -> None:
    entry = CorpusManifestEntry.model_validate(manifest_payload())
    sources = [
        build_source("main", SourceRole.PRIMARY_REPORT, primary=True),
        build_source("00760", SourceRole.SEPARATE_AUDIT_REPORT),
        build_source("00761", SourceRole.CONSOLIDATED_AUDIT_REPORT),
    ]
    documents = [
        CanonicalDocument(
            document_id=f"{entry.doc_id}:{source.source_role.value}",
            filing_id=entry.doc_id,
            document_role=source.source_role,
            primary_source_file_id=source.source_file_id,
            source_file_ids=[source.source_file_id],
            parse_summary=ParseSummary(status=ParseStatus.SUCCESS),
        )
        for source in sources
    ]

    package = FilingPackage(
        filing_id=entry.doc_id,
        company=company_from_manifest(entry),
        filing=filing_from_manifest(entry),
        correction=correction_from_manifest(entry),
        source_files=sources,
        documents=documents,
    )

    assert len(package.source_files) == 3
    assert len(package.documents) == 3


def test_pdf_and_viewer_html_can_feed_one_semantic_document() -> None:
    entry = CorpusManifestEntry.model_validate(manifest_payload(file_format="pdf+html", n_files=2))
    pdf = build_source(
        "report",
        SourceRole.PRIMARY_REPORT,
        primary=True,
        content_format=ContentFormat.PDF,
        parser_profile=ParserProfile.PDF_TEXT,
        extension="pdf",
    )
    viewer = build_source(
        "viewer",
        SourceRole.COMPANION_VIEWER,
        companion_to="report",
        content_format=ContentFormat.HTML,
        parser_profile=ParserProfile.COMPANION_HTML,
        extension="html",
    )
    document = CanonicalDocument(
        document_id=f"{entry.doc_id}:primary_report",
        filing_id=entry.doc_id,
        document_role=SourceRole.PRIMARY_REPORT,
        primary_source_file_id=pdf.source_file_id,
        source_file_ids=[pdf.source_file_id, viewer.source_file_id],
    )

    package = FilingPackage(
        filing_id=entry.doc_id,
        company=company_from_manifest(entry),
        filing=filing_from_manifest(entry),
        correction=correction_from_manifest(entry),
        source_files=[pdf, viewer],
        documents=[document],
    )

    assert len(package.source_files) == 2
    assert len(package.documents) == 1


def test_xml_extension_can_be_routed_to_html_parser() -> None:
    source = build_source(
        "exchange-report",
        SourceRole.PRIMARY_REPORT,
        primary=True,
        content_format=ContentFormat.HTML,
        parser_profile=ParserProfile.XFORMS_HTML,
        extension="xml",
    )

    assert source.declared_extension == "xml"
    assert source.detected_content_format is ContentFormat.HTML
    assert source.parser_profile is ParserProfile.XFORMS_HTML


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CorpusManifestEntry.model_validate(manifest_payload(unexpected="value"))


def test_filing_package_round_trips_through_json() -> None:
    entry = CorpusManifestEntry.model_validate(manifest_payload(n_files=1))
    source = build_source("main", SourceRole.PRIMARY_REPORT, primary=True)
    document = CanonicalDocument(
        document_id=f"{entry.doc_id}:primary_report",
        filing_id=entry.doc_id,
        document_role=SourceRole.PRIMARY_REPORT,
        primary_source_file_id=source.source_file_id,
        source_file_ids=[source.source_file_id],
    )
    package = FilingPackage(
        filing_id=entry.doc_id,
        company=company_from_manifest(entry),
        filing=filing_from_manifest(entry),
        correction=correction_from_manifest(entry),
        source_files=[source],
        documents=[document],
    )

    encoded = package.model_dump_json()
    decoded = FilingPackage.model_validate_json(encoded)

    assert decoded == package
    assert json.loads(encoded)["schema_version"] == "2.2.0"


def test_schema_generation_is_serializable() -> None:
    schema = FilingPackage.model_json_schema()

    assert schema["title"] == "FilingPackage"
    assert "CanonicalDocument" in schema["$defs"]


def test_manifest_helpers_do_not_mix_responsibilities() -> None:
    entry = CorpusManifestEntry.model_validate(manifest_payload())

    company = company_from_manifest(entry)
    filing = filing_from_manifest(entry)
    correction = correction_from_manifest(entry)

    assert isinstance(company, CompanyIdentity)
    assert isinstance(filing, FilingMetadata)
    assert company.corp_code == entry.corp_code
    assert filing.receipt_number == entry.rcept_no
    assert correction.is_correction is False
    assert correction.correction_type is CorrectionType.NONE
