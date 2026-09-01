from decimal import Decimal

from disclosure_agent.domain.metrics import (
    MetricName,
    MetricObservation,
    MetricOperation,
    MetricTarget,
)
from disclosure_agent.services.metric_analysis import analyze_metric_observations


def _observation(company: str, year: int, amount_krw: int | None) -> MetricObservation:
    status = "ANSWERABLE" if amount_krw is not None else "NO_MATCH"
    return MetricObservation(
        target=MetricTarget(company_name=company, year=year),
        status=status,
        amount_krw=amount_krw,
    )


def test_sum_uses_all_grounded_values() -> None:
    observations = (
        _observation("삼성전자", 2025, 333_605_938_000_000),
        _observation("현대차", 2025, 186_254_472_000_000),
        _observation("카카오", 2025, 8_099_147_815_086),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.SUM,
        observations=observations,
    )

    assert result.status == "ANSWERABLE"
    assert result.derived_value == Decimal(527_959_557_815_086)


def test_average_is_decimal_and_not_llm_calculated() -> None:
    observations = (
        _observation("A", 2025, 10),
        _observation("B", 2025, 11),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.AVERAGE,
        observations=observations,
    )

    assert result.status == "ANSWERABLE"
    assert result.derived_value == Decimal("10.5")


def test_difference_is_absolute() -> None:
    observations = (
        _observation("삼성전자", 2025, 333_605_938_000_000),
        _observation("현대차", 2025, 186_254_472_000_000),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.DIFFERENCE,
        observations=observations,
    )

    assert result.status == "ANSWERABLE"
    assert result.derived_value == Decimal(147_351_466_000_000)


def test_growth_rate_uses_first_value_as_baseline() -> None:
    observations = (
        _observation("A", 2024, 100),
        _observation("A", 2025, 125),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.GROWTH_RATE,
        observations=observations,
    )

    assert result.status == "ANSWERABLE"
    assert result.derived_value == Decimal(25)


def test_growth_rate_with_zero_baseline_is_partial() -> None:
    observations = (
        _observation("A", 2024, 0),
        _observation("A", 2025, 100),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.GROWTH_RATE,
        observations=observations,
    )

    assert result.status == "PARTIAL"
    assert result.derived_value is None
    assert result.reason == "zero_baseline"


def test_ranking_is_descending_and_deterministic() -> None:
    observations = (
        _observation("카카오", 2025, 8_099_147_815_086),
        _observation("현대차", 2025, 186_254_472_000_000),
        _observation("삼성전자", 2025, 333_605_938_000_000),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.RANKING,
        observations=observations,
    )

    assert [item.observation.target.company_name for item in result.ranking] == [
        "삼성전자",
        "현대차",
        "카카오",
    ]
    assert [item.position for item in result.ranking] == [1, 2, 3]


def test_missing_target_prevents_partial_sum() -> None:
    observations = (
        _observation("삼성전자", 2025, 333_605_938_000_000),
        _observation("없는회사", 2025, None),
    )

    result = analyze_metric_observations(
        metric=MetricName.REVENUE,
        operation=MetricOperation.SUM,
        observations=observations,
    )

    assert result.status == "PARTIAL"
    assert result.derived_value is None
    assert result.reason == "one_or_more_targets_unavailable"
