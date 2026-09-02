"""Deterministic rendering for closed structured metric answers."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from disclosure_agent.domain.metrics import (
    MetricAnalysisResult,
    MetricName,
    MetricObservation,
    MetricOperation,
)
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import EvidencePack


def _citation(labels: tuple[str, ...]) -> str:
    return "".join(f"[{label}]" for label in labels)


def _labels_for_observation(
    observation: MetricObservation,
    pack: EvidencePack,
) -> tuple[str, ...]:
    filing_ids = {source.filing_id for source in observation.sources}
    labels = tuple(
        f"E{item.rank}" for item in pack.items if item.filing_id in filing_ids
    )
    if not labels:
        raise ValueError(
            "metric observation has no matching Evidence label: "
            f"{observation.target.company_name}/{observation.target.year}"
        )
    return labels


def _all_labels(pack: EvidencePack) -> tuple[str, ...]:
    if not pack.items:
        raise ValueError("metric answer requires at least one Evidence item")
    return tuple(f"E{item.rank}" for item in pack.items)


def _metric_label(metric: MetricName) -> str:
    if metric is MetricName.REVENUE:
        return "연결기준 매출액"
    if metric is MetricName.FACILITY_INVESTMENT:
        return "신규시설투자 결정 금액 합계"
    raise ValueError(f"unsupported metric: {metric}")


def _display_decimal_krw(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integral = int(rounded)
    fraction = int((abs(rounded) - abs(Decimal(integral))) * 100)
    if fraction == 0:
        return format_krw(integral)

    base = format_krw(integral).removesuffix(" 원")
    return f"{base}.{fraction:02d}원"


def _display_percent(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".") + "%"


def _observation_value(observation: MetricObservation) -> int:
    if observation.amount_krw is None:
        raise ValueError("ANSWERABLE metric observation requires amount_krw")
    return observation.amount_krw


def _values_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    lines: list[str] = []
    for observation in result.observations:
        target = observation.target
        amount = format_krw(_observation_value(observation))
        citations = _citation(_labels_for_observation(observation, pack))
        if result.metric is MetricName.REVENUE:
            lines.append(
                f"{target.company_name}의 {target.year}년 연결기준 매출액은 "
                f"{amount}입니다 {citations}."
            )
        else:
            lines.append(
                f"{target.year}년에 공시된 신규시설투자 결정 금액 합계 기준으로 "
                f"{target.company_name}는 {amount}입니다 {citations}."
            )
    return "\n".join(lines)


def _derived_subject(result: MetricAnalysisResult) -> str:
    observations = result.observations
    years = {observation.target.year for observation in observations}
    companies = {observation.target.company_name for observation in observations}
    label = _metric_label(result.metric)

    if len(years) == 1:
        year = next(iter(years))
        return f"요청한 기업들의 {year}년 {label}"
    if len(companies) == 1:
        company = next(iter(companies))
        return f"{company}의 요청 연도 {label}"
    return f"요청한 대상의 {label}"


def _sum_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    if result.derived_value is None:
        raise ValueError("SUM result requires derived_value")
    return (
        f"{_derived_subject(result)} 합계는 {format_krw(int(result.derived_value))}입니다 "
        f"{_citation(_all_labels(pack))}."
    )


def _average_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    if result.derived_value is None:
        raise ValueError("AVERAGE result requires derived_value")
    return (
        f"{_derived_subject(result)} 평균은 약 "
        f"{_display_decimal_krw(result.derived_value)}입니다 "
        f"{_citation(_all_labels(pack))}."
    )


def _difference_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    if result.derived_value is None or len(result.observations) != 2:
        raise ValueError("DIFFERENCE result requires two observations and derived_value")

    first, second = result.observations
    amount = format_krw(int(result.derived_value))
    citations = _citation(_all_labels(pack))
    label = _metric_label(result.metric)

    if result.metric is MetricName.FACILITY_INVESTMENT:
        if first.target.year == second.target.year:
            return (
                f"{first.target.year}년에 공시된 신규시설투자 결정 금액 합계 기준으로 "
                f"{first.target.company_name}와 {second.target.company_name}의 차이는 "
                f"{amount}입니다 {citations}."
            )
        return (
            "공시된 신규시설투자 결정 금액 합계 기준으로 "
            f"{first.target.company_name}의 {first.target.year}년과 "
            f"{second.target.year}년 차이는 {amount}입니다 {citations}."
        )

    if first.target.company_name == second.target.company_name:
        return (
            f"{first.target.company_name}의 {first.target.year}년과 "
            f"{second.target.year}년 {label} 차이는 {amount}입니다 {citations}."
        )
    if first.target.year == second.target.year:
        return (
            f"{first.target.company_name}와 {second.target.company_name}의 "
            f"{first.target.year}년 {label} 차이는 {amount}입니다 {citations}."
        )
    return f"요청한 두 {label}의 차이는 {amount}입니다 {citations}."


def _growth_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    if result.derived_value is None or len(result.observations) != 2:
        raise ValueError("GROWTH_RATE result requires two observations and derived_value")
    first, second = result.observations
    company = first.target.company_name
    label = _metric_label(result.metric)
    return (
        f"{company}의 {first.target.year}년 대비 {second.target.year}년 {label} 증가율은 "
        f"{_display_percent(result.derived_value)}입니다 {_citation(_all_labels(pack))}."
    )


def _ranking_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    if not result.ranking:
        raise ValueError("RANKING result requires ranking entries")

    lines: list[str] = []
    if result.metric is MetricName.FACILITY_INVESTMENT:
        years = {rank.observation.target.year for rank in result.ranking}
        year_text = str(next(iter(years))) if len(years) == 1 else "각 대상 연도"
        lines.append(
            f"{year_text}에 공시된 신규시설투자 결정 금액 합계 기준 순위입니다."
        )
    else:
        lines.append("연결기준 매출액이 큰 순서입니다.")

    for rank in result.ranking:
        observation = rank.observation
        citations = _citation(_labels_for_observation(observation, pack))
        lines.append(
            f"{rank.position}위 {observation.target.company_name}: "
            f"{format_krw(_observation_value(observation))} {citations}"
        )
    return "\n".join(lines)


def render_metric_answer(result: MetricAnalysisResult, pack: EvidencePack) -> str:
    """Render an ANSWERABLE metric result without delegating calculations to an LLM."""

    if result.status != "ANSWERABLE":
        raise ValueError("deterministic metric answer requires ANSWERABLE result")
    if pack.retrieval_status == "NO_MATCH" or not pack.items:
        raise ValueError("deterministic metric answer requires Evidence")

    if result.operation is MetricOperation.VALUES:
        return _values_answer(result, pack)
    if result.operation is MetricOperation.SUM:
        return _sum_answer(result, pack)
    if result.operation is MetricOperation.AVERAGE:
        return _average_answer(result, pack)
    if result.operation is MetricOperation.DIFFERENCE:
        return _difference_answer(result, pack)
    if result.operation is MetricOperation.GROWTH_RATE:
        return _growth_answer(result, pack)
    if result.operation is MetricOperation.RANKING:
        return _ranking_answer(result, pack)
    raise ValueError(f"unsupported metric operation: {result.operation}")
