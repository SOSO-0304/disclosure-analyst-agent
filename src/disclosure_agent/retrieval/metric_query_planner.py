"""Deterministic planning for reusable structured metric analysis queries."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from disclosure_agent.domain.metrics import MetricName, MetricOperation


@dataclass(frozen=True, slots=True)
class MetricQueryIntent:
    """Metric and deterministic operation inferred from one user query."""

    metric: MetricName | None
    operation: MetricOperation | None
    matched_terms: tuple[str, ...]

    @property
    def is_metric_query(self) -> bool:
        return self.metric is not None

    @property
    def is_analysis_query(self) -> bool:
        return self.metric is not None and self.operation not in {None, MetricOperation.VALUES}


_REVENUE_TERMS = ("매출액", "영업수익", "연결매출", "매출")
_FACILITY_INVESTMENT_TERMS = ("설비투자", "신규시설투자", "시설투자")
_OPERATION_TERMS: tuple[tuple[MetricOperation, tuple[str, ...]], ...] = (
    (MetricOperation.GROWTH_RATE, ("증감률", "증가율", "성장률", "증감율")),
    (MetricOperation.AVERAGE, ("평균",)),
    (
        MetricOperation.RANKING,
        ("순위", "상위", "가장 큰", "가장 높은", "최대", "더 큰", "더 높은"),
    ),
    (MetricOperation.DIFFERENCE, ("차이", "비교", "얼마나 더", "격차")),
    (MetricOperation.SUM, ("합계", "총합", "합산", "더한 값", "더하면")),
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip()


def plan_metric_query(query: str) -> MetricQueryIntent:
    """Infer a reusable metric/operation plan without calculating any answer."""

    normalized = _normalize(query)
    matched_terms: list[str] = []

    revenue_matches = [term for term in _REVENUE_TERMS if term in normalized]
    facility_matches = [term for term in _FACILITY_INVESTMENT_TERMS if term in normalized]
    matched_terms.extend(revenue_matches)
    matched_terms.extend(facility_matches)

    metric = None
    if revenue_matches and not facility_matches:
        metric = MetricName.REVENUE
    elif facility_matches and not revenue_matches:
        metric = MetricName.FACILITY_INVESTMENT

    operation = MetricOperation.VALUES if metric is not None else None
    for candidate_operation, terms in _OPERATION_TERMS:
        matches = [term for term in terms if term in normalized]
        if matches:
            if metric is not None:
                operation = candidate_operation
            matched_terms.extend(matches)
            break

    return MetricQueryIntent(
        metric=metric,
        operation=operation,
        matched_terms=tuple(dict.fromkeys(matched_terms)),
    )
