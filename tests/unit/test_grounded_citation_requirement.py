from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    invalid_citation_tokens,
)
from disclosure_agent.llm.hcx_client import HcxAnswerResult


class _FakeClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult:
        del system_prompt, max_completion_tokens
        self.calls.append(user_prompt)
        return HcxAnswerResult(
            content=self.answers.pop(0),
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_invalid_citation_tokens_flags_missing_evidence_citation() -> None:
    invalid = invalid_citation_tokens(
        "2025년에는 AI 사업 전략을 강화했습니다.",
        evidence_count=3,
    )

    assert invalid == ("[EVIDENCE_CITATION_REQUIRED]",)


def test_generate_grounded_answer_repairs_uncited_answer() -> None:
    client = _FakeClient(
        [
            "2025년에는 AI 사업 전략을 강화했습니다.",
            "2025년에는 AI 사업 전략을 강화했습니다 [E2].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=3,
    )

    assert answer.content.endswith("[E2].")
    assert len(client.calls) == 2
    assert "최소 하나 이상의 유효한 [E번호] 인용" in client.calls[1]
