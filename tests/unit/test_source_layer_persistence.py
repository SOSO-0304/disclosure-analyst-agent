from __future__ import annotations

from datetime import date

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
    CompanyIdentity,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceLocator,
    SourceRole,
    TableCell,
    TableData,
)
from disclosure_agent.storage.source_layer_persistence import project_source_package


def _package() -> FilingPackage:
    source_id = "f:source:1"
    section_id = "f:section:1"
    block_id = "f:block:1"
    table_id = "f:table:1"
    locator = SourceLocator(source_file_id=source_id, xpath="/DOCUMENT/BODY/TABLE")
    table = TableData(
        table_id=table_id,
        row_count=1,
        column_count=2,
        header_row_indices=[0],
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                is_header=True,
                text_raw="매출액",
                text_normalized="매출액",
                source_locator=locator,
            ),
            TableCell(
                row_index=0,
                column_index=1,
                text_raw="100",
                text_normalized="100",
                source_locator=locator,
            ),
        ],
    )
    return FilingPackage(
        filing_id="f",
        company=CompanyIdentity(
            corp_code="00000001",
            stock_code="000001",
            corp_name="테스트",
            listed_name="테스트",
            industry="테스트",
            sector="테스트",
        ),
        filing=FilingMetadata(
            doc_id="f",
            document_group=DocumentGroup.PERIODIC,
            document_subtype="사업보고서",
            report_name_raw="사업보고서 (2024.12)",
            receipt_number="20250331000001",
            receipt_date=date(2025, 3, 31),
            filer_name="테스트",
            base_year=2024,
            base_month=12,
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw="raw/20250331000001.xml",
                archive_path_normalized="raw/20250331000001.xml",
                file_name="20250331000001.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                is_primary=True,
            )
        ],
        documents=[
            CanonicalDocument(
                document_id="f:primary_report",
                filing_id="f",
                document_role=SourceRole.PRIMARY_REPORT,
                primary_source_file_id=source_id,
                source_file_ids=[source_id],
                sections=[
                    CanonicalSection(
                        section_id=section_id,
                        order=0,
                        level=0,
                        title_raw="재무",
                        title_normalized="재무",
                    )
                ],
                blocks=[
                    CanonicalBlock(
                        block_id=block_id,
                        section_id=section_id,
                        order=0,
                        block_type=BlockType.TABLE,
                        table=table,
                        source_locator=locator,
                    )
                ],
                parse_summary=ParseSummary(
                    status=ParseStatus.SUCCESS,
                    parser_name="DartParser",
                    parser_version="2.2.1",
                    emitted_section_count=1,
                    emitted_block_count=1,
                    emitted_table_count=1,
                ),
            )
        ],
    )


def test_projection_keeps_table_as_one_row_with_json_grid() -> None:
    rows = project_source_package(_package(), load_run_id="run")

    assert rows.counts == {
        "companies": 1,
        "filings": 1,
        "documents": 1,
        "sections": 1,
        "blocks": 1,
        "tables": 1,
    }
    assert rows.blocks[0]["table_id"] == "f:table:1"
    assert rows.tables[0]["normalized_text"] == "매출액 100"
    assert len(rows.tables[0]["grid"]["cells"]) == 2
    assert rows.tables[0]["grid"]["cells"][1]["text_raw"] == "100"
