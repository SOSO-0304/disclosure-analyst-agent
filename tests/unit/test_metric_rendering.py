from __future__ import annotations

from decimal import Decimal

from disclosure_agent.domain.metrics import (
    MetricAnalysisResult,
    MetricName,
    MetricObservation,
    MetricOperation,
    MetricRank,
    MetricSource,
    MetricTarget,
)
from disclosure_agent.rendering.metric import render_metric_answer
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack


def _observation(company: str, year: int, amount: int, filing_id: str) -> MetricObservation:
    return MetricObservation(
        target=MetricTarget(company_name=company, year=year),
        status="ANSWERABLE",
        amount_krw=amount,
        sources=(MetricSource(filing_id=filing_id, amount_krw=amount),),
    )


def _pack(*filings: tuple[str, str]) -> EvidencePack:
    items = tuple(
        EvidenceItem(
            evidence_id=f"metric:{filing_id}",
            source_kind="metric_fact",
            rank=index,
            score=1.0,
            semantic_score=0.0,
            lexical_score=0.0,
            company_name=company,
            filing_id=filing_id,
            report_name="사업보고서 (2025.12)",
            document_id=None,
            section_id=None,
            content_text="metric evidence",
            truncated=False,
            matched_terms=(),
            block_ids=(),
            table_ids=(),
        )
        for index, (filing_id, company) in enumerate(filings, 1)
    )
    return EvidencePack(
        query="metric query",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )


def test_renders_revenue_value_with_local_citation() -> None:
    observation = _observation("삼성전자", 2025, 333_605_938_000_000, "f1")
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.REVENUE,
        operation=MetricOperation.VALUES,
        observations=(observation,),
    )

    answer = render_metric_answer(result, _pack(("f1", "삼성전자")))

    assert answer == (
        "삼성전자의 2025년 연결기준 매출액은 "
        "333조 6,059억 3,800만 원입니다 [E1]."
    )


def test_renders_average_instead_of_only_listing_inputs() -> None:
    observations = (
        _observation("삼성전자", 2025, 333_605_938_000_000, "f1"),
        _observation("현대차", 2025, 186_254_472_000_000, "f2"),
        _observation("카카오", 2025, 8_099_147_815_086, "f3"),
    )
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.REVENUE,
        operation=MetricOperation.AVERAGE,
        observations=observations,
        derived_value=Decimal("175986519271695.3333333333333"),
    )

    answer = render_metric_answer(
        result,
        _pack(("f1", "삼성전자"), ("f2", "현대차"), ("f3", "카카오")),
    )

    assert "평균은 약 175조 9,865억 1,927만 1,695.33원" in answer
    assert "[E1][E2][E3]" in answer


def test_renders_facility_difference_with_disclosure_semantics() -> None:
    observations = (
        _observation("두산에너빌리티", 2025, 806_800_000_000, "f1"),
        _observation("한화오션", 2025, 600_800_000_000, "f2"),
    )
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.FACILITY_INVESTMENT,
        operation=MetricOperation.DIFFERENCE,
        observations=observations,
        derived_value=Decimal(206_000_000_000),
    )

    answer = render_metric_answer(
        result,
        _pack(("f1", "두산에너빌리티"), ("f2", "한화오션")),
    )

    assert "공시된 신규시설투자 결정 금액 합계 기준" in answer
    assert "2,060억 원" in answer
    assert "[E1][E2]" in answer


def test_ranking_uses_each_observations_own_evidence() -> None:
    samsung = _observation("삼성전자", 2025, 333_605_938_000_000, "f1")
    hyundai = _observation("현대차", 2025, 186_254_472_000_000, "f2")
    kakao = _observation("카카오", 2025, 8_099_147_815_086, "f3")
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.REVENUE,
        operation=MetricOperation.RANKING,
        observations=(samsung, hyundai, kakao),
        ranking=(
            MetricRank(position=1, observation=samsung),
            MetricRank(position=2, observation=hyundai),
            MetricRank(position=3, observation=kakao),
        ),
    )

    answer = render_metric_answer(
        result,
        _pack(("f1", "삼성전자"), ("f2", "현대차"), ("f3", "카카오")),
    )

    assert "1위 삼성전자: 333조 6,059억 3,800만 원 [E1]" in answer
    assert "2위 현대차: 186조 2,544억 7,200만 원 [E2]" in answer
    assert "3위 카카오: 8조 991억 4,781만 5,086 원 [E3]" in answer
