"""Reusable structured metric retrieval and deterministic aggregation service."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from disclosure_agent.domain.metrics import (
    MetricAnalysisResult,
    MetricName,
    MetricObservation,
    MetricOperation,
    MetricRank,
    MetricTarget,
)
from disclosure_agent.storage.revenue_repository import RevenueRepository


def analyze_metric_observations(
    *,
    metric: MetricName,
    operation: MetricOperation,
    observations: tuple[MetricObservation, ...],
) -> MetricAnalysisResult:
    """Apply one deterministic operation to already-grounded metric observations."""

    if not observations:
        raise ValueError("observations must not be empty")
    if operation in {MetricOperation.DIFFERENCE, MetricOperation.GROWTH_RATE}:
        if len(observations) != 2:
            raise ValueError(f"{operation.value} requires exactly two observations")

    available = tuple(
        observation
        for observation in observations
        if observation.status == "ANSWERABLE" and observation.amount_krw is not None
    )
    if not available:
        return MetricAnalysisResult(
            status="NO_MATCH",
            metric=metric,
            operation=operation,
            observations=observations,
            reason="no_answerable_observations",
        )
    if len(available) != len(observations):
        return MetricAnalysisResult(
            status="PARTIAL",
            metric=metric,
            operation=operation,
            observations=observations,
            reason="one_or_more_targets_unavailable",
        )

    values = tuple(Decimal(observation.amount_krw) for observation in available)

    if operation is MetricOperation.VALUES:
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
        )

    if operation is MetricOperation.SUM:
        derived_value = sum(values, Decimal(0))
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
            derived_value=derived_value,
        )

    if operation is MetricOperation.AVERAGE:
        derived_value = sum(values, Decimal(0)) / Decimal(len(values))
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
            derived_value=derived_value,
        )

    if operation is MetricOperation.DIFFERENCE:
        derived_value = abs(values[1] - values[0])
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
            derived_value=derived_value,
        )

    if operation is MetricOperation.GROWTH_RATE:
        baseline = values[0]
        if baseline == 0:
            return MetricAnalysisResult(
                status="PARTIAL",
                metric=metric,
                operation=operation,
                observations=observations,
                reason="zero_baseline",
            )
        derived_value = ((values[1] - baseline) / baseline) * Decimal(100)
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
            derived_value=derived_value,
        )

    if operation is MetricOperation.RANKING:
        ordered = sorted(
            available,
            key=lambda observation: (
                -(observation.amount_krw or 0),
                observation.target.company_name,
                observation.target.year,
            ),
        )
        ranking = tuple(
            MetricRank(position=index, observation=observation)
            for index, observation in enumerate(ordered, start=1)
        )
        return MetricAnalysisResult(
            status="ANSWERABLE",
            metric=metric,
            operation=operation,
            observations=observations,
            ranking=ranking,
        )

    raise ValueError(f"unsupported metric operation: {operation}")


class MetricAnalysisService:
    """Retrieve structured metric values and apply generic deterministic operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def analyze(
        self,
        *,
        metric: MetricName,
        targets: tuple[MetricTarget, ...],
        operation: MetricOperation,
    ) -> MetricAnalysisResult:
        """Retrieve all targets first, then calculate only from grounded values."""

        if not targets:
            raise ValueError("targets must not be empty")

        observations = tuple(self._read_metric(metric=metric, target=target) for target in targets)
        return analyze_metric_observations(
            metric=metric,
            operation=operation,
            observations=observations,
        )

    def _read_metric(self, *, metric: MetricName, target: MetricTarget) -> MetricObservation:
        if metric is MetricName.REVENUE:
            return self._read_revenue(target)
        raise ValueError(f"unsupported metric: {metric}")

    def _read_revenue(self, target: MetricTarget) -> MetricObservation:
        result = RevenueRepository(self.session).query_annual_consolidated_revenue(
            company_name=target.company_name,
            year=target.year,
        )
        candidate = result.candidate
        return MetricObservation(
            target=target,
            status=result.status,
            amount_krw=result.amount_krw,
            filing_id=candidate.filing_id if candidate is not None else None,
            fact_id=candidate.fact_id if candidate is not None else None,
            raw_value=candidate.raw_value if candidate is not None else None,
            resolved_unit=result.resolved_unit,
        )
