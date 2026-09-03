from __future__ import annotations

from disclosure_agent.llm.grounded_generation import generate_grounded_answer
from disclosure_agent.llm.hcx_client import HcxAnswerResult


class _FakeClient:
    def __init__(self, answer: str) -> None:
        self.answer_text = answer
        self.calls = 0

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult:
        self.calls += 1
        return HcxAnswerResult(
            content=self.answer_text,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_zero_event_subset_preserves_separate_scope_citation() -> None:
    prompt = """=== DETERMINISTIC ANALYSIS ===
analysis_type: fundraising_by_instrument
status: ANSWERABLE
derived_from: [E1],[E2],[E3]
유형: 전환사채(CB) | 3건 | 합계 378억 원 | [E1],[E2],[E3]
유형: 신주인수권부사채(BW) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음
유형: 교환사채(EB) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음
"""
    client = _FakeClient(
        "\n".join(
            (
                "신주인수권부사채(BW): 확인된 내역 없음 [E1][E2][E3].",
                "교환사채(EB): 확인된 내역 없음 [E1][E2][E3].",
                "집계 범위 근거: [E1][E2][E3].",
            )
        )
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=3,
    )

    assert "신주인수권부사채(BW): 확인된 내역 없음." in answer.content
    assert "교환사채(EB): 확인된 내역 없음." in answer.content
    assert "집계 범위 근거: [E1][E2][E3]." in answer.content
    assert answer.finish_reason == "stop"
    assert client.calls == 1
