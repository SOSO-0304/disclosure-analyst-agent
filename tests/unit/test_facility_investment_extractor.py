from __future__ import annotations

from datetime import date
from decimal import Decimal

from disclosure_agent.extractors.facility_investment import extract_facility_investment
from disclosure_agent.facts.generic import FactKind, GenericFact


def _fact(
    *,
    fact_id: str,
    label: str,
    value: str,
    row: int,
    numeric: Decimal | None = None,
    header: str | None = None,
    table_id: str = "table:1",
) -> GenericFact:
    return GenericFact(
        fact_id=fact_id,
        fact_kind=FactKind.NUMERIC if numeric is not None else FactKind.LABEL_VALUE,
        filing_id="exchange_20260327903037",
        document_id="document:1",
        section_id=None,
        block_id="block:1",
        table_id=table_id,
        row_index=row,
        column_index=2,
        label_text=label,
        header_text=header,
        path_text=f"[기재정정]신규시설투자등 | {label}",
        value_text=value,
        raw_value=value,
        numeric_value=numeric,
        unit_raw=None,
        currency=None,
        concept_code=None,
        context_ref=None,
        source_locator={
            "source_file_id": "source:1",
            "xpath": f"/TABLE/TR[{row + 1}]/TD[3]",
        },
    )


def test_extracts_core_facility_investment_fields_with_evidence() -> None:
    facts = [
        _fact(fact_id="f1", label="1. 투자구분", value="신규시설투자", row=0),
        _fact(
            fact_id="f2",
            label="2. 투자내역 > 투자금액(원)",
            value="341,100,000,000",
            row=1,
            numeric=Decimal("341100000000"),
        ),
        _fact(
            fact_id="f3",
            label="2. 투자내역 > 자기자본(원)",
            value="1,705,500,000,000",
            row=2,
            numeric=Decimal("1705500000000"),
        ),
        _fact(
            fact_id="f4",
            label="2. 투자내역 > 자기자본대비(%)",
            value="20.00",
            row=3,
            numeric=Decimal("20.00"),
        ),
        _fact(
            fact_id="f5",
            label="3. 투자목적",
            value="신모델 대응 및 경쟁력 향상을 위한 투자",
            row=4,
        ),
        _fact(
            fact_id="f6",
            label="4. 투자기간 > 시작일",
            value="2025.11.27",
            row=5,
        ),
        _fact(
            fact_id="f7",
            label="4. 투자기간 > 종료일",
            value="2026-12-31",
            row=6,
        ),
        _fact(
            fact_id="f8",
            label="5. 이사회결의일(결정일)",
            value="2025/11/27",
            row=7,
        ),
        _fact(
            fact_id="f9",
            label="8. 기타 투자판단에 참고할 사항",
            value="투자금액과 기간은 경영환경에 따라 변경될 수 있습니다.",
            row=8,
        ),
    ]

    extraction = extract_facility_investment(
        filing_id="exchange_20260327903037",
        receipt_number="20260327903037",
        company_name="테스트회사",
        stock_code="123456",
        is_correction=True,
        facts=facts,
    )

    event = extraction.event
    assert event.investment_type == "신규시설투자"
    assert event.investment_amount_krw == 341_100_000_000
    assert event.equity_krw == 1_705_500_000_000
    assert event.equity_ratio == Decimal("20.00")
    assert event.purpose == "신모델 대응 및 경쟁력 향상을 위한 투자"
    assert event.investment_start_date == date(2025, 11, 27)
    assert event.investment_end_date == date(2026, 12, 31)
    assert event.decision_date == date(2025, 11, 27)
    assert event.notes == "투자금액과 기간은 경영환경에 따라 변경될 수 있습니다."
    assert extraction.evidence["investment_amount_krw"].fact_id == "f2"
    assert extraction.evidence["equity_krw"].fact_id == "f3"


def test_equity_amount_does_not_prefer_equity_ratio_fact() -> None:
    facts = [
        _fact(
            fact_id="ratio",
            label="2. 투자내역 > 자기자본대비(%)",
            value="18.5",
            row=0,
            numeric=Decimal("18.5"),
        ),
        _fact(
            fact_id="equity",
            label="2. 투자내역 > 자기자본(원)",
            value="2,000,000,000,000",
            row=9,
            numeric=Decimal("2000000000000"),
        ),
    ]

    extraction = extract_facility_investment(
        filing_id="filing:1",
        receipt_number="20260101000001",
        company_name="테스트회사",
        stock_code="123456",
        is_correction=False,
        facts=facts,
    )

    assert extraction.event.equity_krw == 2_000_000_000_000
    assert extraction.event.equity_ratio == Decimal("18.5")
    assert extraction.evidence["equity_krw"].fact_id == "equity"


def test_correction_table_prefers_after_value_over_before_value() -> None:
    facts = [
        _fact(
            fact_id="before",
            label="2. 투자내역 > 투자금액(원)",
            value="100,000,000,000",
            row=1,
            numeric=Decimal("100000000000"),
            header="정정전",
            table_id="table:correction",
        ),
        _fact(
            fact_id="after",
            label="2. 투자내역 > 투자금액(원) > 100,000,000,000",
            value="120,000,000,000",
            row=1,
            numeric=Decimal("120000000000"),
            header="정정후",
            table_id="table:correction",
        ),
    ]

    extraction = extract_facility_investment(
        filing_id="filing:correction",
        receipt_number="20260327903037",
        company_name="테스트회사",
        stock_code="123456",
        is_correction=True,
        facts=facts,
    )

    assert extraction.event.investment_amount_krw == 120_000_000_000
    assert extraction.evidence["investment_amount_krw"].fact_id == "after"


def test_missing_dash_values_are_not_promoted_as_evidence() -> None:
    facts = [
        _fact(
            fact_id="defer",
            label="7. 공시유보 관련내용 > 유보기한",
            value="-",
            row=0,
        ),
        _fact(
            fact_id="purpose",
            label="3. 투자목적",
            value="생산능력 확대",
            row=1,
        ),
    ]

    extraction = extract_facility_investment(
        filing_id="filing:2",
        receipt_number="20260101000002",
        company_name="테스트회사",
        stock_code="123456",
        is_correction=False,
        facts=facts,
    )

    assert extraction.event.defer_until is None
    assert "defer_until" not in extraction.evidence
    assert extraction.event.purpose == "생산능력 확대"
