from disclosure_agent.domain.metrics import MetricName, MetricOperation
from disclosure_agent.retrieval.metric_query_planner import plan_metric_query


def test_plain_revenue_query_is_values_operation() -> None:
    intent = plan_metric_query("삼성전자의 2025년 연결기준 매출액은 얼마인가?")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.VALUES
    assert not intent.is_analysis_query


def test_period_comparison_is_difference_operation() -> None:
    intent = plan_metric_query("현대차의 2024년과 2025년 연결 매출액을 비교해줘")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.DIFFERENCE
    assert intent.is_analysis_query


def test_sum_query_is_generic_sum_operation() -> None:
    intent = plan_metric_query("삼성전자, 현대차, 카카오의 2025년 매출 합계는?")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.SUM


def test_total_difference_prefers_difference_over_sum() -> None:
    intent = plan_metric_query(
        "두산에너빌리티와 한화오션의 2025년 신규시설투자 결정 금액 합계 차이는 얼마인가?"
    )

    assert intent.metric is MetricName.FACILITY_INVESTMENT
    assert intent.operation is MetricOperation.DIFFERENCE


def test_growth_rate_has_priority_over_generic_comparison_words() -> None:
    intent = plan_metric_query("2024년과 2025년 매출 증가율을 비교해줘")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.GROWTH_RATE


def test_ranking_query_is_detected() -> None:
    intent = plan_metric_query("2025년 매출액이 가장 큰 기업 순위는?")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.RANKING


def test_natural_ordering_phrase_is_ranking() -> None:
    intent = plan_metric_query(
        "삼성전자, 현대차, 카카오를 2025년 연결기준 매출액이 큰 순서대로 정리해줘"
    )

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.RANKING
    assert intent.is_analysis_query


def test_ranking_has_priority_over_total_wording() -> None:
    intent = plan_metric_query("A와 B 중 신규시설투자 합계가 더 큰 기업은 어디인가?")

    assert intent.metric is MetricName.FACILITY_INVESTMENT
    assert intent.operation is MetricOperation.RANKING


def test_facility_investment_comparison_uses_ranking() -> None:
    intent = plan_metric_query("A와 B 중 2025년 설비투자 규모가 더 큰 기업은 어디인가?")

    assert intent.metric is MetricName.FACILITY_INVESTMENT
    assert intent.operation is MetricOperation.RANKING
    assert intent.is_analysis_query


def test_new_facility_investment_term_is_detected() -> None:
    intent = plan_metric_query("A기업의 2025년 신규시설투자 합계는?")

    assert intent.metric is MetricName.FACILITY_INVESTMENT
    assert intent.operation is MetricOperation.SUM


def test_multiple_metric_families_fall_back_instead_of_guessing() -> None:
    intent = plan_metric_query("2025년 매출액 대비 설비투자 비중은?")

    assert intent.metric is None
    assert intent.operation is None


def test_unknown_metric_stays_unplanned_for_semantic_fallback() -> None:
    intent = plan_metric_query("삼성전자의 AI 반도체 사업 전략은?")

    assert intent.metric is None
    assert intent.operation is None
    assert not intent.is_metric_query
