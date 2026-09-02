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
        self.calls.append(user_prompt)
        content = self.answers.pop(0)
        return HcxAnswerResult(
            content=content,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )


def test_invalid_citation_tokens_reject_internal_and_out_of_range_labels() -> None:
    invalid = invalid_citation_tokens(
        "해지 계약이 있습니다 [DETERMINISTIC ANALYSIS]. 근거 [E1][E5]",
        evidence_count=4,
    )

    assert invalid == ("[DETERMINISTIC ANALYSIS]", "[E5]")


def test_generate_grounded_answer_repairs_invalid_citation_once() -> None:
    client = _FakeClient(
        [
            "해지 계약이 있습니다 [DETERMINISTIC ANALYSIS].",
            "해지 계약이 있습니다 [E1][E4].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=4,
    )

    assert answer.content == "해지 계약이 있습니다 [E1][E4]."
    assert len(client.calls) == 2
    assert "[DETERMINISTIC ANALYSIS] 같은 내부 섹션명" in client.calls[1]


def test_generate_grounded_answer_does_not_retry_valid_citations() -> None:
    client = _FakeClient(["매출액은 100억 원입니다 [E1]."])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=1,
    )

    assert answer.content == "매출액은 100억 원입니다 [E1]."
    assert len(client.calls) == 1


def test_generate_grounded_answer_strips_citations_from_empty_fundraising_types() -> None:
    prompt = """=== DETERMINISTIC ANALYSIS ===
analysis_type: fundraising_by_instrument
유형: 유상증자 | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음
유형: 전환사채(CB) | 3건 | 합계 378억 원 | [E1],[E2],[E3]
유형: 신주인수권부사채(BW) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음
유형: 교환사채(EB) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음
"""
    client = _FakeClient(
        [
            "\n".join(
                (
                    "유상증자: 확인된 내역 없음 [E1][E2][E3].",
                    "전환사채(CB): 3건, 합계 378억 원 [E1][E2][E3].",
                    "신주인수권부사채(BW)와 교환사채(EB): 확인된 내역 없음 [E1][E2][E3].",
                )
            )
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=3,
    )

    assert "유상증자: 확인된 내역 없음." in answer.content
    assert "전환사채(CB): 3건, 합계 378억 원 [E1][E2][E3]." in answer.content
    assert "신주인수권부사채(BW)와 교환사채(EB): 확인된 내역 없음." in answer.content
    assert len(client.calls) == 1
