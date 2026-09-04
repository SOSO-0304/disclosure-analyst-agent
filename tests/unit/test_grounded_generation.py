from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    invalid_citation_tokens,
    invalid_report_year_citations,
    unsupported_business_unit_attributions,
    unsupported_investment_purpose_claims,
    unsupported_money_literals,
    unsupported_narrow_business_scope_claims,
    unsupported_temporal_claims,
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


def test_invalid_citation_tokens_requires_at_least_one_evidence_citation() -> None:
    invalid = invalid_citation_tokens(
        "사업 전략을 설명하지만 근거 인용이 없습니다.",
        evidence_count=2,
    )

    assert invalid == ("[EVIDENCE_CITATION_REQUIRED]",)


def test_invalid_report_year_citations_reject_wrong_year_inside_scoped_paragraph() -> None:
    invalid = invalid_report_year_citations(
        "2025년 사업보고서에서는 AI 전략이 강조됩니다 [E2][E3].",
        evidence_report_years={1: 2023, 2: 2025, 3: 2023},
    )

    assert invalid == ("[E3]",)


def test_invalid_report_year_citations_allows_multi_year_sentences_in_one_paragraph() -> None:
    invalid = invalid_report_year_citations(
        (
            "2023년 사업보고서에서는 DDR5 중심의 대응이 강조됩니다 [E1]. "
            "반면 2025년에는 HBM 중심의 대응이 강화됩니다 [E2]."
        ),
        evidence_report_years={1: 2023, 2: 2025},
    )

    assert invalid == ()


def test_unsupported_business_unit_attributions_reject_unlinked_sdc_grouping() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서에서 AI와 관련된 핵심 사업 전략을 정리해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2025.12)
섹션: 영상디스플레이 사업
AI TV 라인업과 AI 홈 기능을 확대했습니다.
"""
    content = "\n".join(
        (
            "3. **SDC**:",
            "- AI TV 라인업을 확대했습니다 [E1].",
        )
    )

    invalid = unsupported_business_unit_attributions(content, user_prompt=prompt)

    assert "3. **SDC**:" in invalid
    assert "- AI TV 라인업을 확대했습니다 [E1]." in invalid


def test_narrow_system_semiconductor_scope_rejects_memory_only_claim() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
메모리 차세대 기술 경쟁력 강화 및 중장기 수요 대비를 위한 투자를 지속 추진합니다.
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.
"""
    content = "- 메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진합니다 [E1]."

    invalid = unsupported_narrow_business_scope_claims(content, user_prompt=prompt)

    assert invalid == (content,)


def test_investment_purpose_requires_explicit_purpose_relation_in_evidence() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.

[E2] kind=semantic_chunk score=0.9
text:
AI 성장에 따른 중장기 수요 확대를 기회로 고부가 수주 확대와 수익 구조 개선을 추진합니다.
"""
    content = "\n".join(
        (
            "2. **투자 목적**:",
            "- Advanced 노드 CAPA 확보를 위해 투자합니다 [E1].",
            "- AI 수요 대응과 수익 구조 개선이 투자 목적입니다 [E2].",
            "이는 AI 신기술 대응을 위한 전략적 움직임으로 해석됩니다.",
        )
    )

    invalid = unsupported_investment_purpose_claims(content, user_prompt=prompt)

    assert "- Advanced 노드 CAPA 확보를 위해 투자합니다 [E1]." not in invalid
    assert "- AI 수요 대응과 수익 구조 개선이 투자 목적입니다 [E2]." in invalid
    assert "이는 AI 신기술 대응을 위한 전략적 움직임으로 해석됩니다." in invalid


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


def test_unsupported_temporal_claims_reject_completed_investment_as_future_plan() -> None:
    prompt = """text:
2025년 DS 부문 및 SDC 등의 첨단공정 증설을 중심으로
52.7조원의 시설투자가 이루어졌습니다.
투자기간 2025.01~2025.12
"""
    content = "\n".join(
        (
            "삼성전자의 2025년 사업보고서에 따른 투자 계획입니다.",
            "- 52.7조원의 시설투자를 계획하고 있습니다 [E1].",
            "- 투자 효율성 제고에 집중할 계획입니다 [E1].",
            "2025년 1월부터 12월까지 진행될 예정입니다 [E1].",
        )
    )

    invalid = unsupported_temporal_claims(content, user_prompt=prompt)

    assert invalid == (
        "- 52.7조원의 시설투자를 계획하고 있습니다 [E1].",
        "2025년 1월부터 12월까지 진행될 예정입니다 [E1].",
    )


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


def test_generate_grounded_answer_repairs_missing_citation_once() -> None:
    client = _FakeClient(
        [
            "AI 관련 사업 전략을 확대하고 있습니다.",
            "AI 관련 사업 전략을 확대하고 있습니다 [E1].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=2,
    )

    assert answer.content.endswith("[E1].")
    assert len(client.calls) == 2
    assert "최소 하나 이상의 유효한 [E번호] 인용" in client.calls[1]


def test_generate_grounded_answer_exhausts_when_repair_still_has_no_citation() -> None:
    client = _FakeClient(
        [
            "AI 관련 사업 전략을 확대하고 있습니다.",
            "AI 관련 사업 전략을 확대하고 있습니다.",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt="question and evidence",
        evidence_count=2,
    )

    assert answer.finish_reason == "grounding_exhausted"
    assert "근거 정합성 검증을 통과한 서술형 답변" in answer.content
    assert len(client.calls) == 2


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


def test_generate_grounded_answer_repairs_completed_investment_future_tense() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
2025년 DS 부문 및 SDC 등의 첨단공정 증설을 중심으로
52.7조원의 시설투자가 이루어졌습니다.
투자 효율성 제고에도 집중할 계획입니다.
"""
    client = _FakeClient(
        [
            "52.7조원의 시설투자를 계획하고 있습니다 [E1].",
            "52.7조원의 시설투자가 이루어졌습니다 [E1].",
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert "시설투자가 이루어졌습니다" in answer.content
    assert len(client.calls) == 2
    assert "계획 또는 예정으로 미래화하지 마세요" in client.calls[1]


def test_generate_grounded_answer_drops_bad_money_lines_after_failed_repair() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
(시설투자 현황) 52.7조원의 시설투자가 이루어졌습니다.
(단위 : 억원)
DS 부문 474,764 SDC 27,970 기타 21,225 합계 526,511
투자 효율성 제고에도 집중할 계획입니다.
"""
    client = _FakeClient(
        [
            "\n".join(
                (
                    "2025년 시설투자는 52.7조원입니다 [E1].",
                    "- DS 부문: 47,476억원 [E1]",
                    "투자 효율성 제고에 집중할 계획입니다 [E1].",
                )
            ),
            "\n".join(
                (
                    "2025년 시설투자는 52.7조원입니다 [E1].",
                    "- DS 부문: 47,476억원 [E1]",
                    "- SDC: 2,797억원 [E1]",
                    "투자 효율성 제고에 집중할 계획입니다 [E1].",
                )
            ),
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert "52.7조원" in answer.content
    assert "투자 효율성 제고" in answer.content
    assert "47,476억원" not in answer.content
    assert "2,797억원" not in answer.content
    assert len(client.calls) == 2


def test_generate_grounded_answer_drops_temporal_lines_after_failed_repair() -> None:
    prompt = """=== EVIDENCE PACK ===
[E1]
text:
2025년 첨단공정 증설을 중심으로 52.7조원의 시설투자가 이루어졌습니다.
투자기간 2025.01~2025.12
메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진하였습니다.
투자 효율성 제고에도 집중할 계획입니다.
"""
    bad_answer = "\n".join(
        (
            "- 52.7조원의 시설투자를 계획하고 있습니다 [E1].",
            "- 메모리 경쟁력 강화를 위한 투자를 지속 추진합니다 [E1].",
            "2025년 1월부터 12월까지 진행될 예정입니다 [E1].",
        )
    )
    client = _FakeClient([bad_answer, bad_answer])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=1,
    )

    assert "52.7조원의 시설투자를 계획" not in answer.content
    assert "진행될 예정" not in answer.content
    assert "메모리 경쟁력 강화" in answer.content
    assert len(client.calls) == 2


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