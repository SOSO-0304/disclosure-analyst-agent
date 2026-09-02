from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    invalid_citation_tokens,
    invalid_report_year_citations,
    unsupported_money_literals,
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


def test_invalid_report_year_citations_reject_wrong_year_inside_scoped_paragraph() -> None:
    invalid = invalid_report_year_citations(
        "2025년 사업보고서에서는 AI 전략이 강조됩니다 [E2][E3].",
        evidence_report_years={1: 2023, 2: 2025, 3: 2023},
    )

    assert invalid == ("[E3]",)


def test_unsupported_money_literals_reject_changed_table_digits() -> None:
    prompt = """text:
(단위 : 억원)
DS 부문 474,764 SDC 27,970 기타 21,225 합계 526,511
52.7조원의 시설투자가 이루어졌습니다.
"""

    invalid = unsupported_money_literals(
        "DS 47,476억원, SDC 2,797억원, 총 52.7조원입니다.",
        user_prompt=prompt,
    )

    assert invalid == ("47,476억원", "2,797억원")


def test_unsupported_money_literals_allow_exact_table_numbers_with_declared_unit() -> None:
    prompt = """text:
(단위 : 억원)
DS 부문 474,764 SDC 27,970 기타 21,225 합계 526,511
"""

    invalid = unsupported_money_literals(
        "DS 474,764억 원, SDC 27,970억원입니다.",
        user_prompt=prompt,
    )

    assert invalid == ()


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


def test_generate_grounded_answer_repairs_wrong_annual_report_year_citation() -> None:
    client = _FakeClient(
        [
            "2025년 사업보고서에서는 HBM4 대응이 강조됩니다 [E3].",
            "2025년 사업보고서에서는 HBM4 대응이 강조됩니다 [E2].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=3,
        evidence_report_years={1: 2023, 2: 2025, 3: 2023},
    )

    assert answer.content.endswith("[E2].")
    assert len(client.calls) == 2
    assert "같은 연도의 사업보고서 Evidence" in client.calls[1]


def test_generate_grounded_answer_repairs_changed_money_digits() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
(단위 : 억원)
DS 부문 474,764 SDC 27,970 기타 21,225 합계 526,511
"""
    client = _FakeClient(
        [
            "DS 부문은 47,476억원을 투자했습니다 [E1].",
            "DS 부문은 474,764억원을 투자했습니다 [E1].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert "474,764억원" in answer.content
    assert len(client.calls) == 2
    assert "자릿수나 쉼표를 바꾸지 말고" in client.calls[1]


def test_generate_grounded_answer_does_not_retry_valid_citations() -> None:
    client = _FakeClient(["매출액은 100억 원입니다 [E1]."])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="Evidence: 매출액 100억 원",
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
                    (
                        "신주인수권부사채(BW)와 교환사채(EB): "
                        "확인된 내역 없음 [E1][E2][E3]."
                    ),
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
    assert (
        "신주인수권부사채(BW)와 교환사채(EB): 확인된 내역 없음."
        in answer.content
    )
    assert len(client.calls) == 1
