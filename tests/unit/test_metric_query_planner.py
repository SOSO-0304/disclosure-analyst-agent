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


def test_growth_rate_has_priority_over_generic_comparison_words() -> None:
    intent = plan_metric_query("2024년과 2025년 매출 증가율을 비교해줘")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.GROWTH_RATE


def test_ranking_query_is_detected() -> None:
    intent = plan_metric_query("2025년 매출액이 가장 큰 기업 순위는?")

    assert intent.metric is MetricName.REVENUE
    assert intent.operation is MetricOperation.RANKING


def test_unknown_metric_stays_unplanned_for_semantic_fallback() -> None:
    intent = plan_metric_query("삼성전자의 AI 반도체 사업 전략은?")

    assert intent.metric is None
    assert intent.operation is None
    assert not intent.is_metric_query
