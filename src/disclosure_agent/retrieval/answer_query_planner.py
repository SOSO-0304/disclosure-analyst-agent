"""Deterministic planning for the unified answer execution path."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from disclosure_agent.retrieval.metric_query_planner import plan_metric_query
from disclosure_agent.retrieval.query_router import QueryRoute, RetrievalRail, route_query


class AnswerExecutionMode(StrEnum):
    """Top-level engine that should produce the final answer."""

    METRIC_STRUCTURED = "metric_structured"
    FUNDRAISING_STRUCTURED = "fundraising_structured"
    SUPPLY_CONTRACT_STRUCTURED = "supply_contract_structured"
    HYBRID_GROUNDED = "hybrid_grounded"


@dataclass(frozen=True, slots=True)
class AnswerQueryPlan:
    """Resolved execution mode plus the lower-level retrieval route."""

    mode: AnswerExecutionMode
    route: QueryRoute
    reason: str


_EXPLANATORY_TERMS = (
    "이유",
    "배경",
    "목적",
    "계획",
    "전략",
    "전망",
    "영향",
    "변화",
    "변경",
    "기여",
    "때문",
    "원인",
    "덕분",
    "인과",
    "효과",
)


def _normalize(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query)
    return re.sub(r"\s+", " ", normalized).strip()


def plan_answer_query(query: str) -> AnswerQueryPlan:
    """Choose one production answer engine without using an LLM."""

    normalized = _normalize(query)
    route = route_query(normalized)
    metric_intent = plan_metric_query(normalized)
    explanatory = any(term in normalized for term in _EXPLANATORY_TERMS)

    if metric_intent.metric is not None and metric_intent.operation is not None and not explanatory:
        return AnswerQueryPlan(
            mode=AnswerExecutionMode.METRIC_STRUCTURED,
            route=route,
            reason="deterministic metric and operation resolved",
        )

    if (
        RetrievalRail.FUNDRAISING in route.rails
        and RetrievalRail.SEMANTIC not in route.rails
    ):
        return AnswerQueryPlan(
            mode=AnswerExecutionMode.FUNDRAISING_STRUCTURED,
            route=route,
            reason="closed fundraising aggregation query",
        )

    if (
        RetrievalRail.SUPPLY_CONTRACT in route.rails
        and RetrievalRail.SEMANTIC not in route.rails
    ):
        return AnswerQueryPlan(
            mode=AnswerExecutionMode.SUPPLY_CONTRACT_STRUCTURED,
            route=route,
            reason="closed supply-contract lifecycle query",
        )

    return AnswerQueryPlan(
        mode=AnswerExecutionMode.HYBRID_GROUNDED,
        route=route,
        reason="narrative or unsupported closed-form query requires grounded retrieval",
    )
