from datetime import UTC, date, datetime
from decimal import Decimal

import orjson

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CompanyIdentity,
    ContentFormat,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    ParserProfile,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceLocator,
    SourceRole,
    TableCell,
    TableData,
)
from disclosure_agent.storage.jsonl import (
    COMPACT_CANONICAL_PROFILE,
    LEGACY_CANONICAL_PROFILE,
    CanonicalJsonlWriter,
    read_canonical,
    serialize_canonical,
)


def _package() -> FilingPackage:
    filing_id = "exchange_20260829000001"
    source_id = f"{filing_id}:source:1"
    document_id = f"{filing_id}:primary_report"
    locator = SourceLocator(
        source_file_id=source_id,
        xpath="/html/body/table/tr/td",
    )
    table = TableData(
        table_id=f"{source_id}:table:1",
        row_count=1,
        column_count=1,
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                text_raw="1,000",
                text_normalized="1,000",
                numeric_value=Decimal("1000"),
                source_locator=locator,
            )
        ],
    )
    block = CanonicalBlock(
        block_id=f"{source_id}:block:1",
        order=0,
        block_type=BlockType.TABLE,
        table=table,
        source_locator=locator,
    )
    document = CanonicalDocument(
        document_id=document_id,
        filing_id=filing_id,
        document_role=SourceRole.PRIMARY_REPORT,
        primary_source_file_id=source_id,
        source_file_ids=[source_id],
        blocks=[block],
        parse_summary=ParseSummary(
            status=ParseStatus.SUCCESS,
            parser_name="ExchangeParser",
            parser_version="2.2.0",
            emitted_block_count=1,
            emitted_table_count=1,
        ),
    )
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code="00123456",
            stock_code="123456",
            corp_name="테스트",
            listed_name="테스트",
            industry="테스트 산업",
            sector="테스트 섹터",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.EXCHANGE,
            report_name_raw="단일판매ㆍ공급계약체결",
            receipt_number="20260829000001",
            receipt_date=date(2026, 8, 29),
            filer_name="테스트",
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw="raw/test.xml",
                archive_path_normalized="raw/test.xml",
                file_name="test.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                detected_content_format=ContentFormat.HTML,
                parser_profile=ParserProfile.XFORMS_HTML,
                size_bytes=100,
                is_primary=True,
            )
        ],
        documents=[document],
        created_at=datetime(2026, 8, 29, tzinfo=UTC),
    )


def test_legacy_profile_is_byte_compatible() -> None:
    package = _package()

    expected = orjson.dumps(
        package.model_dump(mode="json"),
        option=orjson.OPT_SORT_KEYS,
    )

    assert serialize_canonical(package, profile=LEGACY_CANONICAL_PROFILE) == expected


def test_compact_profile_round_trips_and_omits_nulls() -> None:
    package = _package()

    legacy = serialize_canonical(package, profile=LEGACY_CANONICAL_PROFILE)
    compact = serialize_canonical(package, profile=COMPACT_CANONICAL_PROFILE)
    payload = orjson.loads(compact)
    cell = payload["documents"][0]["blocks"][0]["table"]["cells"][0]

    assert payload["schema_version"] == "2.2.0"
    assert "unit_raw" not in cell
    assert "page_number" not in cell["source_locator"]
    assert len(compact) < len(legacy)
    assert FilingPackage.model_validate(payload) == package


def test_streaming_writer_reads_legacy_and_compact_records(tmp_path) -> None:
    package = _package()
    output = tmp_path / "canonical.jsonl"

    with CanonicalJsonlWriter(output, profile=COMPACT_CANONICAL_PROFILE) as writer:
        first_bytes = writer.write(package)
        second_bytes = writer.write(package)

    assert output.stat().st_size == first_bytes + second_bytes
    assert list(read_canonical(output)) == [package, package]


def test_gzip_writer_is_deterministic_and_auto_detected(tmp_path) -> None:
    package = _package()
    first = tmp_path / "first.jsonl.gz"
    second = tmp_path / "second.bin"

    for output in (first, second):
        with CanonicalJsonlWriter(
            output,
            profile=COMPACT_CANONICAL_PROFILE,
            compression="gzip",
        ) as writer:
            logical_bytes = writer.write(package)
            writer.write(package)

        assert output.read_bytes().startswith(b"\x1f\x8b")
        assert output.stat().st_size < logical_bytes * 2
        assert list(read_canonical(output)) == [package, package]

    assert first.read_bytes() == second.read_bytes()
