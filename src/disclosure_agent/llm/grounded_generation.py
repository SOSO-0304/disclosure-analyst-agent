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


def _all_invalid_citations(
    content: str,
    *,
    evidence_count: int,
    evidence_report_years: dict[int, int] | None,
) -> tuple[str, ...]:
    invalid = list(invalid_citation_tokens(content, evidence_count=evidence_count))
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
    """Generate an answer and retry once when citation tokens are invalid."""

    if evidence_count < 1:
        raise ValueError("evidence_count must be at least 1")

    answer = client.answer(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    answer = _sanitize_answer(answer, user_prompt=user_prompt)
    invalid = _all_invalid_citations(
        answer.content,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if not invalid:
        return answer

    repair_lines = [
        user_prompt,
        "",
        "인용 형식 재작성 요구사항:",
        f"- 사용할 수 있는 인용은 [E1]부터 [E{evidence_count}]까지뿐입니다.",
        "- [DETERMINISTIC ANALYSIS] 같은 내부 섹션명은 인용으로 쓰지 마세요.",
        "- 기존 답변의 사실관계와 계산 결과는 바꾸지 말고 인용 위치만 올바르게 고치세요.",
        "- 구체적 사실을 결론에서 다시 말하면 그 문장에도 해당 [E번호]를 다시 붙이세요.",
    ]
    if evidence_report_years:
        repair_lines.append(
            "- 연도별 사업보고서 비교에서는 각 연도 단락에 같은 연도의 사업보고서 Evidence만 인용하세요."
        )
    repair_prompt = "\n".join(repair_lines)
    repaired = client.answer(
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    repaired = _sanitize_answer(repaired, user_prompt=user_prompt)
    remaining = _all_invalid_citations(
        repaired.content,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if remaining:
        joined = ", ".join(remaining)
        raise RuntimeError(f"HCX returned invalid grounded citations after repair: {joined}")
    return repaired