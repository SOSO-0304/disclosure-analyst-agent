from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from disclosure_agent.storage.db_models import SourceBlockRow, SourceTableRow
from disclosure_agent.storage.revenue_repository import (
    RevenueCandidate,
    RevenueRepository,
    _mark_current_fiscal_periods,
    extract_monetary_unit,
    extract_monetary_unit_before_value,
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


def test_spaced_audited_consolidated_revenue_is_primary() -> None:
    score, signals = score_revenue_fields(
        label_text="Ⅰ. 매 출 액 > 30",
        header_text="제 57 (당) 기",
        path_text=(
            "사업보고서 (2025.12) - 연결감사보고서 | "
            "(첨부)연 결 재 무 제 표 | 제 57 (당) 기 | Ⅰ. 매 출 액 > 30"
        ),
        year=2025,
    )

    assert score > 0
    assert "revenue_label" in signals
    assert "audited_consolidated_financial_statements" in signals
    assert "consolidated_context" in signals
    assert "current_period_header" in signals
    assert is_primary_revenue_candidate(signals)


def _fiscal_term_candidate(
    *,
    fact_id: str,
    table_id: str,
    header_text: str,
    raw_value: str,
) -> RevenueCandidate:
    return RevenueCandidate(
        score=270,
        company_name="현대차",
        filing_id="periodic_20260310000000",
        receipt_date=date(2026, 3, 10),
        report_name="사업보고서 (2025.12)",
        fact_id=fact_id,
        block_id=f"block:{fact_id}",
        table_id=table_id,
        row_index=0,
        column_index=0,
        label_text="I. 매출액",
        header_text=header_text,
        path_text="사업보고서 - 연결감사보고서 | (첨부)연 결 재 무 제 표 | I. 매출액",
        raw_value=raw_value,
        numeric_value=Decimal(raw_value.replace(",", "")),
        unit_raw=None,
        currency=None,
        signals=(
            "revenue_label",
            "audited_consolidated_financial_statements",
            "consolidated_context",
        ),
    )


def test_highest_fiscal_term_in_same_statement_table_is_current_period() -> None:
    current = _fiscal_term_candidate(
        fact_id="current",
        table_id="table:1",
        header_text="제58기",
        raw_value="186,254,472",
    )
    prior = _fiscal_term_candidate(
        fact_id="prior",
        table_id="table:1",
        header_text="제57기",
        raw_value="175,231,153",
    )
    unrelated = _fiscal_term_candidate(
        fact_id="unrelated",
        table_id="table:2",
        header_text="제99기",
        raw_value="1",
    )

    marked = _mark_current_fiscal_periods((current, prior, unrelated))
    by_fact_id = {candidate.fact_id: candidate for candidate in marked}

    assert "current_fiscal_period_header" in by_fact_id["current"].signals
    assert is_primary_revenue_candidate(by_fact_id["current"].signals)
    assert "current_fiscal_period_header" not in by_fact_id["prior"].signals
    assert not is_primary_revenue_candidate(by_fact_id["prior"].signals)
    assert "current_fiscal_period_header" not in by_fact_id["unrelated"].signals


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


def test_statement_unit_before_revenue_value_is_usable() -> None:
    unit = extract_monetary_unit_before_value(
        "연결손익계산서 (단위 : 원) I. 영업수익 8,099,147,815,086",
        "8,099,147,815,086",
    )

    assert unit == "원"


def test_row_specific_unit_after_revenue_value_is_not_usable() -> None:
    unit = extract_monetary_unit_before_value(
        "I. 매 출 액 333,605,938 ... 기본주당이익(단위 : 원) 6,605",
        "333,605,938",
    )

    assert unit is None


def test_table_body_unit_is_not_used_for_revenue_resolution() -> None:
    candidate = _fiscal_term_candidate(
        fact_id="samsung-revenue",
        table_id="table:income-statement",
        header_text="제 57 (당) 기",
        raw_value="333,605,938",
    )
    candidate = replace(candidate, block_id="block:income-statement")

    block = SimpleNamespace()
    table = SimpleNamespace(
        caption_normalized=None,
        caption_raw=None,
        normalized_text=(
            "Ⅰ. 매 출 액 333,605,938 ... "
            "기본주당이익(단위 : 원) 6,605"
        ),
    )

    class FakeSession:
        def get(self, model, key):
            if model is SourceBlockRow and key == candidate.block_id:
                return block
            if model is SourceTableRow and key == candidate.table_id:
                return table
            return None

    class TestRevenueRepository(RevenueRepository):
        def _immediate_previous_table_unit(self, block):
            return None

        def _nearby_blocks(self, block, *, same_section):
            return ()

    repository = TestRevenueRepository(FakeSession())

    assert repository._resolve_unit(candidate) is None


def test_matching_summary_fact_can_corroborate_revenue_unit() -> None:
    chosen = _fiscal_term_candidate(
        fact_id="primary",
        table_id="table:primary",
        header_text="제 57 (당) 기",
        raw_value="333,605,938",
    )
    summary = replace(
        chosen,
        fact_id="summary",
        block_id="block:summary",
        table_id="table:summary",
        path_text="사업보고서 | III. 재무에 관한 사항 | 1. 요약재무정보 | 제57기 | 매출액",
        signals=("exact_revenue_label", "summary_financial_context"),
    )

    class TestRevenueRepository(RevenueRepository):
        def _resolve_local_unit(self, candidate):
            if candidate.fact_id == "summary":
                return "백만원"
            return None

    repository = TestRevenueRepository(SimpleNamespace())

    assert repository._resolve_corroborated_unit(chosen, (chosen, summary)) == "백만원"


def test_conflicting_summary_units_do_not_resolve_revenue_unit() -> None:
    chosen = _fiscal_term_candidate(
        fact_id="primary",
        table_id="table:primary",
        header_text="제 57 (당) 기",
        raw_value="333,605,938",
    )
    summary_a = replace(
        chosen,
        fact_id="summary-a",
        block_id="block:summary-a",
        table_id="table:summary-a",
        signals=("exact_revenue_label", "summary_financial_context"),
    )
    summary_b = replace(
        chosen,
        fact_id="summary-b",
        block_id="block:summary-b",
        table_id="table:summary-b",
        signals=("exact_revenue_label", "summary_financial_context"),
    )

    class TestRevenueRepository(RevenueRepository):
        def _resolve_local_unit(self, candidate):
            if candidate.fact_id == "summary-a":
                return "백만원"
            if candidate.fact_id == "summary-b":
                return "원"
            return None

    repository = TestRevenueRepository(SimpleNamespace())

    assert repository._resolve_corroborated_unit(chosen, (chosen, summary_a, summary_b)) is None
