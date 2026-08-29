from __future__ import annotations

from datetime import date
from decimal import Decimal

from disclosure_agent.domain.models import (
    CanonicalDocument,
    CompanyIdentity,
    ContentFormat,
    DocumentGroup,
    FilingMetadata,
    FilingPackage,
    ParserProfile,
    SourceFile,
    SourceRole,
)
from disclosure_agent.extractors.exchange_fields import SemanticField
from disclosure_agent.extractors.supply_contract_termination import (
    extract_supply_contract_termination,
)


class StubReader:
    def __init__(self, fields: list[SemanticField]) -> None:
        self.fields = fields

    def read_package(self, package: FilingPackage) -> list[SemanticField]:
        return self.fields


def _package(*, subtype: str = "단일판매공급계약해지") -> FilingPackage:
    source_id = "source:1"
    filing_id = "exchange_termination_1"
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code="00123456",
            stock_code="123456",
            corp_name="테스트회사",
            listed_name="테스트회사",
            industry="테스트업",
            sector="테스트섹터",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.EXCHANGE,
            document_subtype=subtype,
            report_name_raw=subtype,
            receipt_number="20260102800001",
            receipt_date=date(2026, 1, 2),
            filer_name="테스트회사",
        ),
        source_files=[
            SourceFile(
                source_file_id=source_id,
                archive_path_raw="raw/exchange/test.xml",
                archive_path_normalized="raw/exchange/test.xml",
                file_name="test.xml",
                source_role=SourceRole.PRIMARY_REPORT,
                declared_extension="xml",
                detected_content_format=ContentFormat.HTML,
                parser_profile=ParserProfile.XFORMS_HTML,
                is_primary=True,
            )
        ],
        documents=[
            CanonicalDocument(
                document_id="document:1",
                filing_id=filing_id,
                document_role=SourceRole.PRIMARY_REPORT,
                primary_source_file_id=source_id,
                source_file_ids=[source_id],
            )
        ],
    )


def _field(path: str, value: str) -> SemanticField:
    return SemanticField(
        filing_id="exchange_termination_1",
        document_id="document:1",
        table_id="table:1",
        path=tuple(path.split(" > ")),
        value=value,
        raw_value=value,
        row_index=0,
        value_column_index=1,
        value_locator=None,
        label_locators=(),
    )


def test_extract_supply_contract_termination_fields_and_provenance() -> None:
    fields = [
        _field("1. 판매ㆍ공급계약 해지 구분", "기타 판매ㆍ공급계약"),
        _field("- 해지계약명", "전기차 배터리 공급계약"),
        _field("2. 해지내역 > 해지금액(원)", "3,921,711,000,000"),
        _field("2. 해지내역 > 최근매출액(원)", "33,745,469,740,463"),
        _field("2. 해지내역 > 매출액대비(%)", "11.6"),
        _field("3. 계약상대", "Ford Motor Company"),
        _field("- 회사와의 관계", "-"),
        _field("4. 계약기간 > 시작일", "2027-01-01"),
        _field("4. 계약기간 > 종료일", "2032-12-31"),
        _field("5. 해지 주요사유", "거래 상대방의 계약 해지 통보"),
        _field("6. 해지일자", "2025.12.17"),
        _field("8. 기타 투자판단과 관련한 중요사항", "원계약 해지 공시입니다."),
        _field(
            "8. 기타 투자판단과 관련한 중요사항 > 관련공시",
            "2024-10-15 단일판매ㆍ공급계약체결",
        ),
    ]

    result = extract_supply_contract_termination(_package(), reader=StubReader(fields))

    assert result.event.contract_name == "전기차 배터리 공급계약"
    assert result.event.termination_amount == 3_921_711_000_000
    assert result.event.recent_revenue == 33_745_469_740_463
    assert result.event.revenue_ratio == Decimal("11.6")
    assert result.event.relationship is None
    assert result.event.contract_start_date == date(2027, 1, 1)
    assert result.event.contract_end_date == date(2032, 12, 31)
    assert result.event.termination_date == date(2025, 12, 17)
    assert result.event.related_disclosures == "2024-10-15 단일판매ㆍ공급계약체결"
    assert result.evidence["termination_amount"].path_key == "2. 해지내역 > 해지금액(원)"


def test_extract_legacy_termination_aliases() -> None:
    fields = [
        _field("- 세부물건", "코로나19 백신 위탁생산 계약해지 합의"),
        _field("7. 기타 투자판단과 관련한 중요사항", "단일판매ㆍ공급계약 체결 해지건"),
        _field(
            "7. 기타 투자판단과 관련한 중요사항 > ※ 관련공시",
            "2021-05-18 단일판매ㆍ공급계약 체결(자율공시)",
        ),
    ]

    result = extract_supply_contract_termination(_package(), reader=StubReader(fields))

    assert result.event.contract_name == "코로나19 백신 위탁생산 계약해지 합의"
    assert result.event.notes == "단일판매ㆍ공급계약 체결 해지건"
    assert result.event.related_disclosures == "2021-05-18 단일판매ㆍ공급계약 체결(자율공시)"


def test_termination_extractor_rejects_non_termination_subtype() -> None:
    try:
        extract_supply_contract_termination(
            _package(subtype="단일판매공급계약체결"),
            reader=StubReader([]),
        )
    except ValueError as exc:
        assert "document_subtype=단일판매공급계약해지" in str(exc)
    else:
        raise AssertionError("expected ValueError")
