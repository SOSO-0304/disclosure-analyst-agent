"""Evidence construction for deterministic fundraising aggregation."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from disclosure_agent.domain.fundraising_analysis import FundraisingAnalysisResult
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import (
    EvidenceItem,
    EvidencePack,
    build_hybrid_evidence_pack,
)
from disclosure_agent.storage.db_models import SourceBlockRow, SourceFilingRow, SourceTableRow

INSTRUMENT_LABELS = {
    FundraisingInstrument.RIGHTS_ISSUE: "유상증자",
    FundraisingInstrument.CONVERTIBLE_BOND: "전환사채(CB)",
    FundraisingInstrument.BOND_WITH_WARRANTS: "신주인수권부사채(BW)",
    FundraisingInstrument.EXCHANGEABLE_BOND: "교환사채(EB)",
}


def _event_content(result: FundraisingAnalysisResult, event) -> str:
    label = INSTRUMENT_LABELS[event.instrument_type]
    lines = [
        f"회사: {result.company_name}",
        f"연도: {result.year}",
        f"자금조달 유형: {label}",
        f"발행일: {event.issue_date.isoformat()}",
        (
            f"이 canonical 이벤트의 조달 금액: {format_krw(event.amount_krw)}"
            if event.amount_krw is not None
            else "이 canonical 이벤트의 조달 금액: 확인되지 않음"
        ),
    ]
    if event.security_name:
        lines.append(f"증권명: {event.security_name}")
    if event.series:
        lines.append(f"회차: {event.series}")
    if event.issuance_method:
        lines.append(f"발행방법: {event.issuance_method}")
    if event.stock_kind:
        lines.append(f"주식종류: {event.stock_kind}")
    if event.share_quantity is not None:
        lines.append(f"발행수량: {event.share_quantity:,}주")
    if event.issue_price_krw is not None:
        lines.append(f"발행가액: {format_krw(event.issue_price_krw)}")
    lines.extend(
        (
            f"반복 출처 수: {event.source_count}",
            "해석 기준: 같은 경제 이벤트가 여러 정기공시에 반복 기재된 경우 canonical 이벤트 1건으로 중복 제거한 결과임",
        )
    )
    return "\n".join(lines)


def _refs(labels: tuple[str, ...]) -> str:
    return ",".join(f"[{label}]" for label in labels) or "-"


def render_deterministic_fundraising_analysis(
    result: FundraisingAnalysisResult,
    *,
    evidence_labels_by_event_id: dict[str, str],
) -> str:
    """Render type totals and event lineage that the LLM must not recalculate."""

    all_labels = tuple(
        evidence_labels_by_event_id[event.event_id]
        for category in result.categories
        for event in category.events
        if event.event_id in evidence_labels_by_event_id
    )
    lines = [
        "analysis_type: fundraising_by_instrument",
        f"status: {result.status}",
        f"company: {result.company_name}",
        f"year: {result.year}",
        f"derived_from: {_refs(all_labels)}",
        "집계 기준: 반복 정기공시를 canonical 경제 이벤트 단위로 중복 제거한 뒤 유형별로 집계",
    ]

    if result.status == "ANSWERABLE" and result.total_amount_krw is not None:
        lines.append(f"전체 확인 금액 합계: {format_krw(result.total_amount_krw)} {_refs(all_labels)}")
    elif result.status == "PARTIAL":
        lines.append(
            f"전체 합계: 확정 불가; 확인된 금액 합계 {format_krw(result.known_amount_sum_krw)}, "
            f"금액 미확인 {result.missing_amount_count}건"
        )
    else:
        lines.append("전체 결과: 해당 연도에 확인된 canonical 자금조달 이벤트 없음")

    for category in result.categories:
        label = INSTRUMENT_LABELS[category.instrument_type]
        category_labels = tuple(
            evidence_labels_by_event_id[event.event_id]
            for event in category.events
            if event.event_id in evidence_labels_by_event_id
        )
        if category.status == "NO_MATCH":
            lines.append(
                f"유형: {label} | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음"
            )
            continue
        if category.status == "PARTIAL":
            lines.append(
                f"유형: {label} | {category.event_count}건 | 합계 확정 불가 | "
                f"확인 금액 {format_krw(category.known_amount_sum_krw)} | "
                f"금액 미확인 {category.missing_amount_count}건 | {_refs(category_labels)}"
            )
        else:
            lines.append(
                f"유형: {label} | {category.event_count}건 | "
                f"합계 {format_krw(category.total_amount_krw)} | {_refs(category_labels)}"
            )
        for event in category.events:
            evidence_label = evidence_labels_by_event_id.get(event.event_id)
            amount = (
                format_krw(event.amount_krw)
                if event.amount_krw is not None
                else "금액 확인 불가"
            )
            details = [event.issue_date.isoformat(), amount]
            if event.series:
                details.append(event.series)
            if event.security_name:
                details.append(event.security_name)
            suffix = f" [{evidence_label}]" if evidence_label else ""
            lines.append(f"이벤트: {' | '.join(details)}{suffix}")

    return "\n".join(lines)


def build_fundraising_evidence_pack(
    session: Session,
    *,
    query: str,
    result: FundraisingAnalysisResult,
    max_chars_per_item: int = 1600,
    max_total_chars: int = 12000,
) -> EvidencePack:
    """Build one evidence item per canonical fundraising economic event."""

    structured_items: list[EvidenceItem] = []
    event_id_by_evidence_id: dict[str, str] = {}

    for category in result.categories:
        for event in category.events:
            filing = session.get(SourceFilingRow, event.representative_filing_id)
            table = session.get(SourceTableRow, event.representative_table_id)
            if filing is None or table is None:
                continue
            block = session.get(SourceBlockRow, table.block_id)
            evidence_id = f"fundraising:{event.event_id}"
            event_id_by_evidence_id[evidence_id] = event.event_id
            structured_items.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    source_kind="fundraising_event",
                    rank=0,
                    score=1.0,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    company_name=result.company_name,
                    filing_id=event.representative_filing_id,
                    report_name=filing.report_name,
                    document_id=table.document_id,
                    section_id=block.section_id if block is not None else None,
                    content_text=_event_content(result, event),
                    truncated=False,
                    matched_terms=(),
                    block_ids=(table.block_id,),
                    table_ids=(table.table_id,),
                    fact_ids=(),
                    event_ids=(event.event_id,),
                )
            )

    pack = build_hybrid_evidence_pack(
        query,
        structured_items=tuple(structured_items),
        semantic_hits=(),
        max_semantic_items=0,
        max_chars_per_item=max_chars_per_item,
        max_total_chars=max_total_chars,
    )

    if result.status in {"ANSWERABLE", "PARTIAL"} and len(pack.items) != result.event_count:
        raise ValueError("fundraising analysis requires evidence for every canonical event")

    labels_by_event_id: dict[str, str] = {}
    for item in pack.items:
        event_id = event_id_by_evidence_id.get(item.evidence_id)
        if event_id is not None:
            labels_by_event_id[event_id] = f"E{item.rank}"

    analysis = render_deterministic_fundraising_analysis(
        result,
        evidence_labels_by_event_id=labels_by_event_id,
    )
    return replace(pack, deterministic_analysis=analysis[:5000])
