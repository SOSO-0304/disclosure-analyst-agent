"""Evidence construction for deterministic multi-target metric analysis."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from sqlalchemy.orm import Session

from disclosure_agent.domain.metrics import (
    MetricAnalysisResult,
    MetricName,
    MetricObservation,
    MetricOperation,
    MetricSource,
)
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import (
    EvidenceItem,
    EvidencePack,
    build_hybrid_evidence_pack,
)
from disclosure_agent.storage.db_models import SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow


def _format_decimal(value: Decimal | None, operation: MetricOperation) -> str:
    if value is None:
        return "확인되지 않음"
    if operation is MetricOperation.GROWTH_RATE:
        rendered = f"{value.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")
        return f"{rendered}%"
    if value == value.to_integral_value():
        return format_krw(int(value))
    return f"{value:,.2f}원"


def _metric_label(metric: MetricName) -> str:
    if metric is MetricName.REVENUE:
        return "연결기준 매출액"
    if metric is MetricName.FACILITY_INVESTMENT:
        return "해당 연도 의사결정·공시 기준 신규시설투자 결정 금액 합계"
    return metric.value


def _observation_sources(observation: MetricObservation) -> tuple[MetricSource, ...]:
    if observation.sources:
        return observation.sources
    if observation.filing_id is None or observation.fact_id is None:
        return ()
    return (
        MetricSource(
            filing_id=observation.filing_id,
            fact_ids=(observation.fact_id,),
            amount_krw=observation.amount_krw,
            raw_value=observation.raw_value,
            resolved_unit=observation.resolved_unit,
        ),
    )


def _source_content(
    metric: MetricName,
    observation: MetricObservation,
    source: MetricSource,
    *,
    source_index: int,
    source_count: int,
) -> str:
    lines = [
        f"회사: {observation.target.company_name}",
        f"연도: {observation.target.year}",
        f"근거 공시: {source_index}/{source_count}",
    ]

    if metric is MetricName.FACILITY_INVESTMENT:
        if source.amount_krw is not None:
            lines.append(f"이 공시의 신규시설투자 결정 금액: {format_krw(source.amount_krw)}")
        if source.description:
            lines.append(f"대상: {source.description}")
        if source.resolved_unit is not None:
            lines.append(f"확정 단위: {source.resolved_unit}")
        lines.append(
            "해석 기준: 이 Evidence의 금액은 이 공시 한 건의 신규시설투자 결정 금액이며, "
            "회사·연도 합계는 DETERMINISTIC ANALYSIS의 관측값을 사용해야 함. "
            "실제 집행액을 의미하지 않음"
        )
        lines.append(f"조회 상태: {observation.status}")
        return "\n".join(lines)

    lines.extend(
        (
            f"{_metric_label(metric)}: {format_krw(observation.amount_krw)}",
            f"사용자 표시 금액: {format_krw(observation.amount_krw)}",
        )
    )
    if source.amount_krw is not None:
        lines.append(f"이 공시의 기여 금액: {format_krw(source.amount_krw)}")
    if source.description:
        lines.append(f"대상: {source.description}")
    if source.raw_value is not None:
        source_value = source.raw_value
        if source.resolved_unit is not None:
            source_value = f"{source_value}{source.resolved_unit}"
        lines.append(f"공시 원문 값: {source_value}")
    if source.resolved_unit is not None:
        lines.append(f"확정 단위: {source.resolved_unit}")
    lines.append(f"조회 상태: {observation.status}")
    return "\n".join(lines)


def _refs(labels: tuple[str, ...]) -> str:
    return ",".join(f"[{label}]" for label in labels) or "-"


def render_deterministic_metric_analysis(
    result: MetricAnalysisResult,
    *,
    evidence_labels_by_observation: tuple[tuple[str, ...], ...],
) -> str:
    """Render calculation results that the LLM must use without recalculation."""

    all_labels = tuple(label for labels in evidence_labels_by_observation for label in labels)
    lines = [
        f"metric: {result.metric.value}",
        f"operation: {result.operation.value}",
        f"status: {result.status}",
        f"derived_from: {_refs(all_labels)}",
    ]
    if result.reason is not None:
        lines.append(f"reason: {result.reason}")

    for index, observation in enumerate(result.observations):
        labels = (
            evidence_labels_by_observation[index]
            if index < len(evidence_labels_by_observation)
            else ()
        )
        lines.append(
            f"관측값: {observation.target.company_name} {observation.target.year} "
            f"{format_krw(observation.amount_krw)} {_refs(labels)}"
        )

    if result.operation is MetricOperation.VALUES:
        lines.append("계산 결과: 위 관측값을 그대로 사용")
    elif result.operation is MetricOperation.RANKING:
        for item in result.ranking:
            observation = item.observation
            try:
                source_index = result.observations.index(observation)
                labels = evidence_labels_by_observation[source_index]
            except (ValueError, IndexError):
                labels = ()
            lines.append(
                f"{item.position}위: {observation.target.company_name} "
                f"{observation.target.year} {format_krw(observation.amount_krw)} "
                f"{_refs(labels)}"
            )
    elif result.derived_value is not None:
        lines.append(
            "deterministic_result: "
            f"{_format_decimal(result.derived_value, result.operation)}"
        )

    return "\n".join(lines)


def build_metric_evidence_pack(
    session: Session,
    *,
    query: str,
    result: MetricAnalysisResult,
    max_chars_per_item: int = 1600,
    max_total_chars: int = 12000,
) -> EvidencePack:
    """Build complete source evidence for every grounded metric observation."""

    items: list[EvidenceItem] = []
    observation_index_by_evidence_id: dict[str, int] = {}
    expected_source_count = 0

    for observation_index, observation in enumerate(result.observations):
        sources = _observation_sources(observation)
        expected_source_count += len(sources)
        for source_index, source in enumerate(sources, start=1):
            filing = session.get(SourceFilingRow, source.filing_id)
            if filing is None:
                continue

            facts = tuple(
                fact
                for fact_id in source.fact_ids
                if (fact := session.get(GenericFactRow, fact_id)) is not None
            )
            document_id = facts[0].document_id if facts else None
            section_id = facts[0].section_id if facts else None
            block_ids = tuple(dict.fromkeys(fact.block_id for fact in facts))
            table_ids = tuple(dict.fromkeys(fact.table_id for fact in facts))
            fact_ids = tuple(dict.fromkeys(fact.fact_id for fact in facts))

            identity = fact_ids[0] if fact_ids else f"{source.filing_id}:{source_index}"
            evidence_id = f"metric:{result.metric.value}:{identity}"
            observation_index_by_evidence_id[evidence_id] = observation_index
            items.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    source_kind=f"metric_{result.metric.value}",
                    rank=0,
                    score=1.0,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    company_name=observation.target.company_name,
                    filing_id=source.filing_id,
                    report_name=filing.report_name,
                    document_id=document_id,
                    section_id=section_id,
                    content_text=_source_content(
                        result.metric,
                        observation,
                        source,
                        source_index=source_index,
                        source_count=len(sources),
                    ),
                    truncated=False,
                    matched_terms=(),
                    block_ids=block_ids,
                    table_ids=table_ids,
                    fact_ids=fact_ids,
                    event_ids=source.event_ids,
                )
            )

    pack = build_hybrid_evidence_pack(
        query,
        structured_items=tuple(items),
        semantic_hits=(),
        max_semantic_items=0,
        max_chars_per_item=max_chars_per_item,
        max_total_chars=max_total_chars,
    )

    if result.status == "ANSWERABLE" and len(pack.items) != expected_source_count:
        raise ValueError("answerable metric analysis requires complete evidence for every source")

    labels_by_observation: list[list[str]] = [[] for _ in result.observations]
    for item in pack.items:
        observation_index = observation_index_by_evidence_id.get(item.evidence_id)
        if observation_index is not None:
            labels_by_observation[observation_index].append(f"E{item.rank}")

    if result.status == "ANSWERABLE" and any(not labels for labels in labels_by_observation):
        raise ValueError("answerable metric analysis requires evidence for every observation")

    analysis = render_deterministic_metric_analysis(
        result,
        evidence_labels_by_observation=tuple(tuple(labels) for labels in labels_by_observation),
    )
    return replace(pack, deterministic_analysis=analysis[:4000])
