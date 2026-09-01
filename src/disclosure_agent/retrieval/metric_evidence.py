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
    return metric.value


def _observation_content(metric: MetricName, observation: MetricObservation) -> str:
    source_value = observation.raw_value or "확인되지 않음"
    if observation.resolved_unit is not None and observation.raw_value is not None:
        source_value = f"{observation.raw_value}{observation.resolved_unit}"
    return "\n".join(
        (
            f"회사: {observation.target.company_name}",
            f"연도: {observation.target.year}",
            f"{_metric_label(metric)}: {format_krw(observation.amount_krw)}",
            f"사용자 표시 금액: {format_krw(observation.amount_krw)}",
            f"공시 원문 값: {source_value}",
            f"확정 단위: {observation.resolved_unit or '확인되지 않음'}",
            f"조회 상태: {observation.status}",
        )
    )


def render_deterministic_metric_analysis(
    result: MetricAnalysisResult,
    *,
    evidence_labels: tuple[str, ...],
) -> str:
    """Render a calculation result that the LLM must use without recalculation."""

    refs = ",".join(f"[{label}]" for label in evidence_labels)
    lines = [
        f"metric: {result.metric.value}",
        f"operation: {result.operation.value}",
        f"status: {result.status}",
        f"derived_from: {refs or '-'}",
    ]
    if result.reason is not None:
        lines.append(f"reason: {result.reason}")

    if result.operation is MetricOperation.VALUES:
        lines.append("계산 없음: 원본 관측값을 그대로 사용")
    elif result.operation is MetricOperation.RANKING:
        for item in result.ranking:
            observation = item.observation
            try:
                source_index = result.observations.index(observation)
                label = evidence_labels[source_index]
            except (ValueError, IndexError):
                label = "?"
            lines.append(
                f"{item.position}위: {observation.target.company_name} "
                f"{observation.target.year} {format_krw(observation.amount_krw)} [{label}]"
            )
    elif result.derived_value is not None:
        lines.append(f"deterministic_result: {_format_decimal(result.derived_value, result.operation)}")

    return "\n".join(lines)


def build_metric_evidence_pack(
    session: Session,
    *,
    query: str,
    result: MetricAnalysisResult,
    max_chars_per_item: int = 1600,
    max_total_chars: int = 12000,
) -> EvidencePack:
    """Build one source item per grounded observation plus deterministic analysis."""

    items: list[EvidenceItem] = []
    for observation in result.observations:
        if observation.filing_id is None or observation.fact_id is None:
            continue

        fact = session.get(GenericFactRow, observation.fact_id)
        filing = session.get(SourceFilingRow, observation.filing_id)
        if fact is None or filing is None:
            continue

        items.append(
            EvidenceItem(
                evidence_id=f"metric:{result.metric.value}:{observation.fact_id}",
                source_kind=f"metric_{result.metric.value}",
                rank=0,
                score=1.0,
                semantic_score=0.0,
                lexical_score=0.0,
                company_name=observation.target.company_name,
                filing_id=observation.filing_id,
                report_name=filing.report_name,
                document_id=fact.document_id,
                section_id=fact.section_id,
                content_text=_observation_content(result.metric, observation),
                truncated=False,
                matched_terms=(),
                block_ids=(fact.block_id,),
                table_ids=(fact.table_id,),
                fact_ids=(fact.fact_id,),
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

    if result.status == "ANSWERABLE" and len(pack.items) != len(result.observations):
        raise ValueError("answerable metric analysis requires evidence for every observation")

    labels = tuple(f"E{item.rank}" for item in pack.items)
    analysis = render_deterministic_metric_analysis(result, evidence_labels=labels)
    return replace(pack, deterministic_analysis=analysis[:4000])
