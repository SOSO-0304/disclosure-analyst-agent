"""Grounded HCX generation with bounded citation repair."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Protocol

from disclosure_agent.llm.hcx_client import HcxAnswerResult

_EVIDENCE_CITATION = re.compile(r"\[E(\d+)\]")
_INTERNAL_CITATION = re.compile(
    r"\[(?:DETERMINISTIC[ _]ANALYSIS|DETERMINISTIC_RESULT|DERIVED_FROM|ANALYSIS_TYPE)\]",
    re.IGNORECASE,
)
_EMPTY_FUNDRAISING_CATEGORY = re.compile(
    r"^유형:\s*(?P<label>.+?)\s*\|\s*확인된 이벤트 0건\s*\|",
    re.MULTILINE,
)
_POSITIVE_FUNDRAISING_CATEGORY = re.compile(
    r"^유형:\s*(?P<label>.+?)\s*\|\s*[1-9]\d*건\s*\|",
    re.MULTILINE,
)
_REPORT_CONTEXT = re.compile(r"(?P<year>20\d{2})년\s*사업보고서")
_MONEY_LITERAL = re.compile(
    r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?:조\s*원|억\s*원|만\s*원|천\s*원|원)"
)
_TABLE_UNIT = re.compile(r"단위\s*[:：]\s*(조\s*원|억\s*원|만\s*원|천\s*원|원)")
_GROUPED_NUMBER = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")
_DOTTED_PERIOD = re.compile(
    r"(?P<start_year>20\d{2})\.(?P<start_month>\d{1,2})\s*[~～-]\s*"
    r"(?:(?P<end_year>20\d{2})\.)?(?P<end_month>\d{1,2})"
)
_KOREAN_PERIOD = re.compile(
    r"(?P<start_year>20\d{2})년\s*(?P<start_month>\d{1,2})월부터\s*"
    r"(?:(?P<end_year>20\d{2})년\s*)?(?P<end_month>\d{1,2})월까지"
)
_COMPLETED_MARKERS = (
    "이루어졌",
    "완료했",
    "완료하였",
    "취득 완료",
    "실시했",
    "실시하였",
    "집행했",
    "집행하였",
    "투자했",
    "투자하였",
)
_FUTURE_MARKERS = (
    "계획",
    "예정",
    "진행할",
    "진행될",
    "추진할",
    "투자할",
    "집행할",
)
_ABSENCE_PHRASES = (
    "확인된 내역 없음",
    "확인된 이벤트가 없습니다",
    "확인된 이벤트 없음",
    "확인되지 않습니다",
)


class GroundedAnswerClient(Protocol):
    """Minimal client surface required for grounded generation."""

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult: ...


def invalid_citation_tokens(content: str, *, evidence_count: int) -> tuple[str, ...]:
    """Return internal or out-of-range citation tokens that must not reach users."""

    invalid: list[str] = []
    for match in _INTERNAL_CITATION.finditer(content):
        token = match.group(0)
        if token not in invalid:
            invalid.append(token)

    for match in _EVIDENCE_CITATION.finditer(content):
        number = int(match.group(1))
        if number < 1 or number > evidence_count:
            token = match.group(0)
            if token not in invalid:
                invalid.append(token)
    return tuple(invalid)


def invalid_report_year_citations(
    content: str,
    *,
    evidence_report_years: dict[int, int],
) -> tuple[str, ...]:
    """Reject citations from the wrong annual-report year inside a year-scoped paragraph."""

    invalid: list[str] = []
    for paragraph in re.split(r"\n\s*\n", content):
        context_years = {
            int(match.group("year")) for match in _REPORT_CONTEXT.finditer(paragraph)
        }
        if len(context_years) != 1:
            continue
        context_year = next(iter(context_years))
        for match in _EVIDENCE_CITATION.finditer(paragraph):
            number = int(match.group(1))
            evidence_year = evidence_report_years.get(number)
            if evidence_year is None or evidence_year == context_year:
                continue
            token = match.group(0)
            if token not in invalid:
                invalid.append(token)
    return tuple(invalid)


def _normalize_money_token(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _supported_money_literals(user_prompt: str) -> set[str]:
    supported = {
        _normalize_money_token(match.group(0))
        for match in _MONEY_LITERAL.finditer(user_prompt)
    }
    table_units = {
        _normalize_money_token(match.group(1))
        for match in _TABLE_UNIT.finditer(user_prompt)
    }
    grouped_numbers = {match.group(0) for match in _GROUPED_NUMBER.finditer(user_prompt)}
    for unit in table_units:
        for number in grouped_numbers:
            supported.add(f"{number}{unit}")
    return supported


def unsupported_money_literals(content: str, *, user_prompt: str) -> tuple[str, ...]:
    """Return answer money literals whose numeric value is absent from grounded input."""

    supported = _supported_money_literals(user_prompt)
    unsupported: list[str] = []
    for match in _MONEY_LITERAL.finditer(content):
        token = match.group(0)
        if _normalize_money_token(token) in supported:
            continue
        if token not in unsupported:
            unsupported.append(token)
    return tuple(unsupported)


def _period_tuple(match: re.Match[str]) -> tuple[int, int, int, int]:
    start_year = int(match.group("start_year"))
    start_month = int(match.group("start_month"))
    end_year_raw = match.group("end_year")
    end_year = int(end_year_raw) if end_year_raw else start_year
    end_month = int(match.group("end_month"))
    return start_year, start_month, end_year, end_month


def _completed_context(
    user_prompt: str,
) -> tuple[set[str], set[tuple[int, int, int, int]]]:
    completed_money: set[str] = set()
    completed_periods: set[tuple[int, int, int, int]] = set()
    lowered = user_prompt.lower()
    window_radius = 240

    for marker in _COMPLETED_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker.lower(), start)
            if index < 0:
                break
            window_start = max(0, index - window_radius)
            window_end = min(len(user_prompt), index + len(marker) + window_radius)
            window = user_prompt[window_start:window_end]
            completed_money.update(
                _normalize_money_token(match.group(0))
                for match in _MONEY_LITERAL.finditer(window)
            )
            completed_periods.update(
                _period_tuple(match) for match in _DOTTED_PERIOD.finditer(window)
            )
            start = index + len(marker)

    return completed_money, completed_periods


def unsupported_temporal_claims(content: str, *, user_prompt: str) -> tuple[str, ...]:
    """Reject completed disclosure facts that HCX rewrites as future plans or schedules."""

    completed_money, completed_periods = _completed_context(user_prompt)
    if not completed_money and not completed_periods:
        return ()

    invalid: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or not any(marker in stripped for marker in _FUTURE_MARKERS):
            continue

        normalized_line = _normalize_money_token(stripped)
        money_conflict = any(token in normalized_line for token in completed_money)
        claimed_periods = {
            _period_tuple(match) for match in _KOREAN_PERIOD.finditer(stripped)
        }
        period_conflict = bool(claimed_periods & completed_periods)
        if (money_conflict or period_conflict) and stripped not in invalid:
            invalid.append(stripped)
    return tuple(invalid)


def _strip_unsupported_fundraising_absence_citations(
    content: str,
    *,
    user_prompt: str,
) -> str:
    """Remove positive-event citations from deterministic zero-event category statements."""

    if "analysis_type: fundraising_by_instrument" not in user_prompt:
        return content

    empty_labels = tuple(
        match.group("label").strip()
        for match in _EMPTY_FUNDRAISING_CATEGORY.finditer(user_prompt)
    )
    positive_labels = tuple(
        match.group("label").strip()
        for match in _POSITIVE_FUNDRAISING_CATEGORY.finditer(user_prompt)
    )
    if not empty_labels:
        return content

    sanitized_lines: list[str] = []
    for line in content.splitlines():
        mentions_empty = any(label in line for label in empty_labels)
        mentions_positive = any(label in line for label in positive_labels)
        states_absence = any(phrase in line for phrase in _ABSENCE_PHRASES)
        if mentions_empty and not mentions_positive and states_absence:
            line = _EVIDENCE_CITATION.sub("", line)
            line = re.sub(r"\s+([.,])", r"\1", line)
            line = re.sub(r" {2,}", " ", line).rstrip()
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines)


def _strip_lines_with_grounding_violations(
    content: str,
    *,
    user_prompt: str,
) -> str:
    """Drop lines that still contain unsupported money or temporal-state claims."""

    unsupported_money = set(unsupported_money_literals(content, user_prompt=user_prompt))
    unsupported_temporal = set(unsupported_temporal_claims(content, user_prompt=user_prompt))
    if not unsupported_money and not unsupported_temporal:
        return content

    retained = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in unsupported_temporal:
            continue
        if any(token in line for token in unsupported_money):
            continue
        retained.append(line)

    while retained and retained[-1].strip().endswith(":"):
        retained.pop()
    return "\n".join(retained).strip()


def _sanitize_answer(
    answer: HcxAnswerResult,
    *,
    user_prompt: str,
) -> HcxAnswerResult:
    content = _strip_unsupported_fundraising_absence_citations(
        answer.content,
        user_prompt=user_prompt,
    )
    return replace(answer, content=content)


def _all_invalid_grounding_tokens(
    content: str,
    *,
    user_prompt: str,
    evidence_count: int,
    evidence_report_years: dict[int, int] | None,
) -> tuple[str, ...]:
    invalid = list(invalid_citation_tokens(content, evidence_count=evidence_count))
    invalid.extend(unsupported_money_literals(content, user_prompt=user_prompt))
    invalid.extend(unsupported_temporal_claims(content, user_prompt=user_prompt))
    if evidence_report_years:
        invalid.extend(
            invalid_report_year_citations(
                content,
                evidence_report_years=evidence_report_years,
            )
        )
    return tuple(dict.fromkeys(invalid))


def generate_grounded_answer(
    client: GroundedAnswerClient,
    *,
    system_prompt: str,
    user_prompt: str,
    evidence_count: int,
    max_completion_tokens: int = 1200,
    evidence_report_years: dict[int, int] | None = None,
) -> HcxAnswerResult:
    """Generate, repair once, then conservatively drop unsupported grounded claims."""

    if evidence_count < 1:
        raise ValueError("evidence_count must be at least 1")

    answer = client.answer(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    answer = _sanitize_answer(answer, user_prompt=user_prompt)
    invalid = _all_invalid_grounding_tokens(
        answer.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if not invalid:
        return answer

    repair_lines = [
        user_prompt,
        "",
        "근거 정합성 재작성 요구사항:",
        f"- 사용할 수 있는 인용은 [E1]부터 [E{evidence_count}]까지뿐입니다.",
        "- [DETERMINISTIC ANALYSIS] 같은 내부 섹션명은 인용으로 쓰지 마세요.",
        "- Evidence가 뒷받침하는 사실관계는 유지하되 잘못된 인용이나 숫자 표기만 고치세요.",
        "- 구체적 사실을 결론에서 다시 말하면 그 문장에도 해당 [E번호]를 다시 붙이세요.",
        "- 금액은 Evidence에 실제로 등장하는 숫자와 단위만 사용하세요. 표의 숫자를 옮길 때 "
        "자릿수나 쉼표를 바꾸지 말고, 근거에 없는 축약이나 임의 환산을 하지 마세요.",
        "- Evidence가 '이루어졌습니다', '완료했습니다'처럼 완료 사실로 밝힌 투자나 취득을 "
        "계획 또는 예정으로 미래화하지 마세요.",
        "- 이미 종료된 투자기간을 '진행될 예정' 같은 미래 일정으로 바꾸지 마세요.",
    ]
    if evidence_report_years:
        repair_lines.append(
            "- 연도별 사업보고서 비교에서는 각 연도 단락에 같은 연도의 "
            "사업보고서 Evidence만 인용하세요."
        )
    repair_prompt = "\n".join(repair_lines)
    repaired = client.answer(
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    repaired = _sanitize_answer(repaired, user_prompt=user_prompt)
    remaining = _all_invalid_grounding_tokens(
        repaired.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if not remaining:
        return repaired

    conservative_content = _strip_lines_with_grounding_violations(
        repaired.content,
        user_prompt=user_prompt,
    )
    conservative = replace(repaired, content=conservative_content)
    remaining = _all_invalid_grounding_tokens(
        conservative.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if conservative.content and not remaining:
        return conservative
    if not conservative.content and not remaining:
        return replace(
            repaired,
            content=(
                "검색된 공시는 있으나, 근거 정합성 검증을 통과한 서술형 답변을 "
                "안전하게 구성하지 못했습니다. 근거 공시를 직접 확인해 주세요."
            ),
            finish_reason="grounding_exhausted",
        )

    joined = ", ".join(remaining)
    raise RuntimeError(f"HCX returned invalid grounded tokens after repair: {joined}")