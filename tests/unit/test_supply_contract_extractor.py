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
from disclosure_agent.extractors.supply_contract import extract_supply_contract


class StubReader:
    def __init__(self, fields: list[SemanticField]) -> None:
        self.fields = fields

    def read_package(self, package: FilingPackage) -> list[SemanticField]:
        return self.fields


def _package(*, subtype: str = "단일판매공급계약체결") -> FilingPackage:
    source_id = "source:1"
    filing_id = "exchange_1"
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
        filing_id="exchange_1",
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


def test_extract_supply_contract_old_form_aliases_and_provenance() -> None:
    fields = [
        _field("1. 판매ㆍ공급계약 구분", "기타 판매ㆍ공급계약"),
        _field("- 체결계약명", "배전변압기 3,500대"),
        _field("2. 계약내역 > 계약금액(원)", "97,000,000,000"),
        _field("2. 계약내역 > 최근매출액(원)", "1,806,000,000,000"),
        _field("2. 계약내역 > 매출액대비(%)", "5.37"),
        _field("3. 계약상대", "Hyundai Electric America Corporation"),
        _field("- 회사와의 관계", "자회사"),
        _field("4. 판매ㆍ공급지역", "미국"),
        _field("5. 계약기간 > 시작일", "2023-01-30"),
        _field("5. 계약기간 > 종료일", "2024-10-31"),
        _field("7. 계약(수주)일자", "2023-01-30"),
    ]

    result = extract_supply_contract(_package(), reader=StubReader(fields))

    assert result.event.contract_amount == 97_000_000_000
    assert result.event.recent_revenue == 1_806_000_000_000
    assert result.event.revenue_ratio == Decimal("5.37")
    assert result.event.contract_start_date == date(2023, 1, 30)
    assert result.event.contract_end_date == date(2024, 10, 31)
    assert result.event.contract_date == date(2023, 1, 30)
    assert result.evidence["contract_amount"].path_key == "2. 계약내역 > 계약금액(원)"


def test_extract_supply_contract_new_form_aliases() -> None:
    fields = [
        _field("1. 판매ㆍ공급계약 내용", "신규 공급계약"),
        _field("2. 계약내역 > 확정 계약금액", "12,345,000"),
        _field("2. 계약내역 > 최근 매출액(원)", "100,000,000"),
        _field("2. 계약내역 > 매출액 대비(%)", "12.345"),
        _field("3. 계약상대방", "고객사"),
        _field("4. 판매ㆍ공급지역", "대한민국"),
        _field("5. 계약기간 > 시작일", "2026.01.01"),
        _field("5. 계약기간 > 종료일", "2026.12.31"),
        _field("8. 계약(수주)일자", "2025/12/31"),
    ]

    result = extract_supply_contract(_package(), reader=StubReader(fields))

    assert result.event.contract_type is None
    assert result.event.contract_name == "신규 공급계약"
    assert result.event.contract_amount == 12_345_000
    assert result.event.recent_revenue == 100_000_000
    assert result.event.revenue_ratio == Decimal("12.345")
    assert result.event.counterparty == "고객사"
    assert result.event.contract_date == date(2025, 12, 31)


def test_missing_dash_is_not_treated_as_value() -> None:
    fields = [
        _field("2. 계약내역 > 계약금액(원)", "-"),
        _field("4. 판매ㆍ공급지역", "-"),
    ]

    result = extract_supply_contract(_package(), reader=StubReader(fields))

    assert result.event.contract_amount is None
    assert result.event.region is None
    assert "contract_amount" not in result.evidence
