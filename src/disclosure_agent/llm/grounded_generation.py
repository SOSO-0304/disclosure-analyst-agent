"""Grounded HCX generation with bounded citation repair."""

from __future__ import annotations

import re
from typing import Protocol

from disclosure_agent.llm.hcx_client import HcxAnswerResult

_EVIDENCE_CITATION = re.compile(r"\[E(\d+)\]")
_INTERNAL_CITATION = re.compile(
    r"\[(?:DETERMINISTIC[ _]ANALYSIS|DETERMINISTIC_RESULT|DERIVED_FROM|ANALYSIS_TYPE)\]",
    re.IGNORECASE,
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


def generate_grounded_answer(
    client: GroundedAnswerClient,
    *,
    system_prompt: str,
    user_prompt: str,
    evidence_count: int,
    max_completion_tokens: int = 1200,
) -> HcxAnswerResult:
    """Generate an answer and retry once when citation tokens are invalid."""

    if evidence_count < 1:
        raise ValueError("evidence_count must be at least 1")

    answer = client.answer(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    invalid = invalid_citation_tokens(answer.content, evidence_count=evidence_count)
    if not invalid:
        return answer

    repair_prompt = "\n".join(
        (
            user_prompt,
            "",
            "인용 형식 재작성 요구사항:",
            f"- 사용할 수 있는 인용은 [E1]부터 [E{evidence_count}]까지뿐입니다.",
            "- [DETERMINISTIC ANALYSIS] 같은 내부 섹션명은 인용으로 쓰지 마세요.",
            "- 기존 답변의 사실관계와 계산 결과는 바꾸지 말고 인용 위치만 올바르게 고치세요.",
            "- 구체적 사실을 결론에서 다시 말하면 그 문장에도 해당 [E번호]를 다시 붙이세요.",
        )
    )
    repaired = client.answer(
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    remaining = invalid_citation_tokens(repaired.content, evidence_count=evidence_count)
    if remaining:
        joined = ", ".join(remaining)
        raise RuntimeError(f"HCX returned invalid grounded citations after repair: {joined}")
    return repaired
