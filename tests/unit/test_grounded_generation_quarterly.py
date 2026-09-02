from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    unsupported_temporal_claims,
)
from disclosure_agent.llm.hcx_client import HcxAnswerResult


class _FakeClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult:
        del system_prompt, user_prompt, max_completion_tokens
        return HcxAnswerResult(
            content=self.answers.pop(0),
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_temporal_validator_allows_future_plan_in_same_report_year() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
company=삼성전자 report=분기보고서 (2026.03)
text:
2026년 1분기 중 기존 설비 투자를 집행하였습니다.
향후 AI 수요 대응을 위한 신규 설비 투자를 계획하고 있습니다.
"""
    content = "2026년에는 AI 수요 대응을 위한 신규 설비 투자를 계획하고 있습니다 [E1]."

    assert unsupported_temporal_claims(content, user_prompt=prompt) == ()


def test_temporal_validator_still_rejects_same_completed_period_as_future() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
52.7조원의 시설투자가 이루어졌습니다.
투자기간 2025.01~2025.12
"""
    content = "2025년 1월부터 12월까지 진행될 예정입니다 [E1]."

    assert unsupported_temporal_claims(content, user_prompt=prompt) == (content,)


def test_grounded_generation_returns_safe_fallback_when_all_claims_are_removed() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
52.7조원의 시설투자가 이루어졌습니다.
"""
    bad = "52.7조원의 시설투자를 계획하고 있습니다 [E1]."
    client = _FakeClient([bad, bad])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert answer.finish_reason == "grounding_exhausted"
    assert "근거 정합성 검증" in answer.content
    assert "52.7조원의 시설투자를 계획" not in answer.content
