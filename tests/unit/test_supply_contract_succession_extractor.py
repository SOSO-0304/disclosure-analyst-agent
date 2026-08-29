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
from disclosure_agent.extractors.supply_contract_succession import (
    extract_supply_contract_succession,
)


class StubReader:
    def __init__(self, fields: list[SemanticField]) -> None:
        self.fields = fields

    def read_package(self, package: FilingPackage) -> list[SemanticField]:
        return self.fields


def _package(*, report_name: str | None = None) -> FilingPackage:
    source_id = "source:1"
    filing_id = "exchange_succession_1"
    report = report_name or (
        "[기재정정]투자판단관련주요경영사항(자회사의 주요경영사항) "
        "(단일판매 공급계약 잔여금액 승계)"
    )
    return FilingPackage(
        filing_id=filing_id,
        company=CompanyIdentity(
            corp_code="00123456",
            stock_code="123456",
            corp_name="OCI홀딩스",
            listed_name="OCI홀딩스",
            industry="테스트업",
            sector="테스트섹터",
        ),
        filing=FilingMetadata(
            doc_id=filing_id,
            document_group=DocumentGroup.EXCHANGE,
            document_subtype="투자판단관련주요경영사항",
            report_name_raw=report,
            receipt_number="20241224800227",
            receipt_date=date(2024, 12, 24),
            filer_name="OCI홀딩스",
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
        filing_id="exchange_succession_1",
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


def test_extract_supply_contract_succession_fields_and_source_references() -> None:
    details = (
        "- 계약상대방 변경 및 계약기간 연장 "
        "1) 판매ㆍ공급계약 구분: 폴리실리콘 공급 "
        "2) 계약금액: 잔여금액 승계 95,250,256,522원 "
        "(USD 96,671,325, 최초에 공시된 환율:985.30원/1USD) "
        "※'24년 2월 22일 승계된 잔여금액 USD 96,671,325 중 "
        "현재기준 USD 92,726,451 공급, 이행률: 95.92% "
        "3) 계약상대: VIETNAM SUNERGY CELL COMPANY LIMITED, VSUN CHINA CO.,LTD. "
        "4) 계약기간: 2024년 2월 22일 ~ 2024년 12월 24일까지 "
        "(양사 합의로 계약기간 종료)"
    )
    notes = (
        "본 공시는 승계된 계약의 건입니다. "
        "※ 관련공시(OCI 홀딩스㈜) "
        "- 2008년 04월 02일 : 단일판매·공급계약 체결 "
        "- 2015년 12월 30일 : [정정]단일판매·공급계약 체결 "
        "- 2018년 12월 21일 : [정정]단일판매·공급계약 체결"
    )
    fields = [
        _field("1. 제목", "단일판매·공급계약 잔여금액 승계"),
        _field("2. 주요내용", details),
        _field("3. 이사회결의일(결정일) 또는 사실확인일", "2024-12-24"),
        _field("3. 정정사유", "양사 합의에 의한 계약기간 종료"),
        _field("4. 기타 투자판단과 관련한 중요사항", notes),
        _field(
            "※ 관련공시",
            "2024-02-22 투자판단 관련 주요경영사항 "
            "2024-04-30 투자판단 관련 주요경영사항",
        ),
    ]

    result = extract_supply_contract_succession(_package(), reader=StubReader(fields))
    event = result.event

    assert event.contract_type == "폴리실리콘 공급"
    assert event.succession_amount_krw == 95_250_256_522
    assert event.succession_amount_usd == 96_671_325
    assert event.disclosed_exchange_rate == Decimal("985.30")
    assert event.fulfilled_amount_usd == 92_726_451
    assert event.fulfillment_ratio == Decimal("95.92")
    assert event.counterparty == (
        "VIETNAM SUNERGY CELL COMPANY LIMITED, VSUN CHINA CO.,LTD."
    )
    assert event.contract_start_date == date(2024, 2, 22)
    assert event.contract_end_date == date(2024, 12, 24)
    assert event.decision_date == date(2024, 12, 24)
    assert event.source_contract_reference_dates == (
        date(2008, 4, 2),
        date(2015, 12, 30),
        date(2018, 12, 21),
    )
    assert result.evidence["details"].path_key == "2. 주요내용"


def test_succession_extractor_rejects_other_investment_judgment() -> None:
    package = _package(report_name="투자판단관련주요경영사항(일반사항)")

    try:
        extract_supply_contract_succession(package, reader=StubReader([]))
    except ValueError as exc:
        assert "not the Supply Contract succession" in str(exc)
    else:
        raise AssertionError("expected ValueError")
