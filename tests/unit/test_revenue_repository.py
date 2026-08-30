from decimal import Decimal

from disclosure_agent.storage.revenue_repository import (
    extract_monetary_unit,
    is_primary_revenue_candidate,
    scale_to_krw,
    score_revenue_fields,
)


def test_audited_consolidated_current_period_is_primary() -> None:
    score, signals = score_revenue_fields(
        label_text="I. 영업수익 > 6,11",
        header_text="제 31 (당) 기",
        path_text=(
            "사업보고서 (2025.12) - 연결감사보고서 | "
            "(첨부)연 결 재 무 제 표 | 제 31 (당) 기 | I. 영업수익 > 6,11"
        ),
        year=2025,
    )

    assert score > 0
    assert "audited_consolidated_financial_statements" in signals
    assert "current_period_header" in signals
    assert is_primary_revenue_candidate(signals)


def test_notes_candidate_is_not_primary() -> None:
    _, signals = score_revenue_fields(
        label_text="매출액",
        header_text="(주)카카오",
        path_text="사업보고서 - 연결감사보고서 | (첨부)연 결 재 무 제 표 | 주석 | 매출액",
        year=2025,
    )

    assert "notes_context" in signals
    assert not is_primary_revenue_candidate(signals)


def test_extract_and_scale_million_won_unit() -> None:
    unit = extract_monetary_unit("(기준일 : 2025년 12월 31일) (단위 : 백만원, 주)")

    assert unit == "백만원"
    assert scale_to_krw(Decimal("8099148"), unit) == 8_099_148_000_000
