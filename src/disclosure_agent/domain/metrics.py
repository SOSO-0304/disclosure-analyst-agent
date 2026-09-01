"""Domain types for reusable metric retrieval and deterministic analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MetricName(StrEnum):
    """Structured metrics supported by the generic analysis layer."""

    REVENUE = "revenue"
    FACILITY_INVESTMENT = "facility_investment"


class MetricOperation(StrEnum):
    """Deterministic operations that can be applied to metric observations."""

    VALUES = "values"
    SUM = "sum"
    AVERAGE = "average"
    DIFFERENCE = "difference"
    GROWTH_RATE = "growth_rate"
    RANKING = "ranking"


@dataclass(frozen=True, slots=True)
class MetricTarget:
    """One company/year coordinate requested for a structured metric."""

    company_name: str
    year: int


@dataclass(frozen=True, slots=True)
class MetricSource:
    """One public-disclosure source contributing to a metric observation."""

    filing_id: str
    fact_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    amount_krw: int | None = None
    raw_value: str | None = None
    resolved_unit: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class MetricObservation:
    """One grounded metric value plus all source lineage used to derive it."""

    target: MetricTarget
    status: str
    amount_krw: int | None
    sources: tuple[MetricSource, ...] = ()
    filing_id: str | None = None
    fact_id: str | None = None
    raw_value: str | None = None
    resolved_unit: str | None = None


@dataclass(frozen=True, slots=True)
class MetricRank:
    """One ranked metric observation."""

    position: int
    observation: MetricObservation


@dataclass(frozen=True, slots=True)
class MetricAnalysisResult:
    """Result of a deterministic multi-observation metric operation."""

    status: str
    metric: MetricName
    operation: MetricOperation
    observations: tuple[MetricObservation, ...]
    derived_value: Decimal | None = None
    ranking: tuple[MetricRank, ...] = ()
    reason: str | None = None
