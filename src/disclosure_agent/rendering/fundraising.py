"""Deterministic user-facing rendering for fundraising summaries."""

from __future__ import annotations

from disclosure_agent.domain.fundraising_analysis import FundraisingAnalysisResult
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import EvidencePack

INSTRUMENT_LABELS = {
    FundraisingInstrument.RIGHTS_ISSUE: "유상증자",
    FundraisingInstrument.CONVERTIBLE_BOND: "전환사채(CB)",
    FundraisingInstrument.BOND_WITH_WARRANTS: "신주인수권부사채(BW)",
    FundraisingInstrument.EXCHANGEABLE_BOND: "교환사채(EB)",
}


def _labels_by_event_id(pack: EvidencePack) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in pack.items:
        for event_id in item.event_ids:
            labels[event_id] = f"E{item.rank}"
    return labels


def _citation(labels: tuple[str, ...]) -> str:
    return "".join(f"[{label}]" for label in labels)


def render_fundraising_answer(
    result: FundraisingAnalysisResult,
    pack: EvidencePack,
) -> str:
    """Render type-by-type fundraising results without LLM citation drift."""

    if result.status != "ANSWERABLE":
        raise ValueError("deterministic fundraising answer requires ANSWERABLE result")

    labels_by_event_id = _labels_by_event_id(pack)
    lines = [f"{result.company_name}의 {result.year}년 자금조달 내역은 다음과 같습니다."]

    for category in result.categories:
        instrument_label = INSTRUMENT_LABELS[category.instrument_type]
        if category.status == "NO_MATCH":
            lines.append(
                f"{instrument_label}: 제공된 공시에서 확인된 이벤트가 없습니다."
            )
            continue

        category_labels = tuple(
            labels_by_event_id[event.event_id]
            for event in category.events
            if event.event_id in labels_by_event_id
        )
        if len(category_labels) != category.event_count:
            raise ValueError("fundraising Evidence count does not match event_count")

        if category.total_amount_krw is None:
            raise ValueError("ANSWERABLE fundraising category requires total amount")

        lines.append(
            f"{instrument_label}: {category.event_count}건, "
            f"합계 {format_krw(category.total_amount_krw)} {_citation(category_labels)}."
        )
        for event in category.events:
            label = labels_by_event_id.get(event.event_id)
            if label is None:
                raise ValueError(f"missing Evidence label for fundraising event: {event.event_id}")
            amount = (
                format_krw(event.amount_krw)
                if event.amount_krw is not None
                else "금액 확인 불가"
            )
            series = f"{event.series}: " if event.series else ""
            lines.append(
                f"- {series}{amount}, 발행일 {event.issue_date.isoformat()} [{label}]."
            )

    return "\n".join(lines)
