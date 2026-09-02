from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    unsupported_investment_plan_structure,
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


def test_temporal_validator_rejects_completed_quarter_as_future() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1] kind=semantic_chunk
text:
2026년 1분기 11.2조원의 시설투자가 이루어졌습니다.
(단위 : 억원)
구 분 투자기간 투자액 DS 2026.01~2026.03 101,927 합계 112,332
"""
    content = "이와 같은 투자 계획은 2026년 1분기 동안 진행될 예정입니다 [E1]."

    assert unsupported_temporal_claims(content, user_prompt=prompt) == (content,)


def test_temporal_validator_rejects_present_tense_for_completed_table_amount() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1] kind=semantic_chunk
text:
2026년 1분기 11.2조원의 시설투자가 이루어졌습니다.
(단위 : 억원)
구 분 투자기간 투자액 DS 2026.01~2026.03 101,927 SDC 5,881 합계 107,808
"""
    content = "- DS 부문 신·증설 및 보완에 101,927억원을 투자합니다 [E1]."

    assert unsupported_temporal_claims(content, user_prompt=prompt) == (content,)


def test_plan_structure_rejects_completed_table_amounts_under_plan_heading() -> None:
    prompt = """사용자 질문:
삼성전자의 2026년 1분기 분기보고서를 기준으로 주요 투자 계획을 정리해줘
=== EVIDENCE PACK ===
[E1] kind=semantic_chunk
text:
2026년 1분기 11.2조원의 시설투자가 이루어졌습니다.
(단위 : 억원)
구 분 투자기간 투자액 DS 2026.01~2026.03 101,927 SDC 5,881 기타 4,524 합계 112,332
"""
    content = """세부 투자 계획은 다음과 같습니다:
- DS 부문: 101,927억원 [E1]
- SDC: 5,881억원 [E1]
- 기타: 4,524억원 [E1]
"""

    assert unsupported_investment_plan_structure(content, user_prompt=prompt) == (
        "세부 투자 계획은 다음과 같습니다:",
    )


def test_grounded_generation_repairs_quarter_plan_structure() -> None:
    prompt = """사용자 질문:
삼성전자의 2026년 1분기 분기보고서를 기준으로 주요 투자 계획을 정리해줘
=== EVIDENCE PACK ===
[E1] kind=semantic_chunk
text:
2026년 1분기 11.2조원의 시설투자가 이루어졌습니다.
메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진하였습니다.
시스템 반도체 Advanced 노드 CAPA 확보 투자도 진행 중입니다.
투자 효율성 제고에도 집중할 계획입니다.
(단위 : 억원)
구 분 투자기간 투자액 DS 2026.01~2026.03 101,927 SDC 5,881 기타 4,524 합계 112,332
"""
    bad = """주요 투자 계획은 다음과 같습니다:
- 11.2조원의 시설투자가 이루어졌습니다 [E1].
- Advanced 노드 CAPA 확보 투자가 진행 중입니다 [E1].

세부 투자 계획은 다음과 같습니다:
- DS 부문: 101,927억원 [E1]

이와 같은 투자 계획은 2026년 1분기 동안 진행될 예정입니다 [E1].
"""
    repaired = """지속·향후 투자 방향은 다음과 같습니다:
- 메모리 차세대 기술 경쟁력 강화 투자를 지속 추진하고 있습니다 [E1].
- 시스템 반도체 Advanced 노드 CAPA 확보 투자도 진행 중입니다 [E1].
- 투자 효율성 제고에도 집중할 계획입니다 [E1].

확인된 2026년 1분기 투자 실적으로 11.2조원의 시설투자가 이루어졌습니다 [E1].
"""
    client = _FakeClient([bad, repaired])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert "지속·향후 투자 방향" in answer.content
    assert "확인된 2026년 1분기 투자 실적" in answer.content
    assert "진행될 예정" not in answer.content
    assert "세부 투자 계획" not in answer.content


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
