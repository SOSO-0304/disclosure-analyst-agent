from __future__ import annotations

from disclosure_agent.llm.grounded_generation import (
    generate_grounded_answer,
    invalid_citation_tokens,
    invalid_report_year_citations,
    missing_multi_company_comparison_synthesis,
    missing_required_company_mentions,
    unsupported_business_unit_attributions,
    unsupported_investment_purpose_claims,
    unsupported_investment_scope_structure,
    unsupported_money_literals,
    unsupported_narrow_business_scope_claims,
    unsupported_temporal_claims,
    unsupported_unrequested_investment_amounts,
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


def test_invalid_report_year_citations_allows_future_period_inside_single_report() -> None:
    invalid = invalid_report_year_citations(
        "2026년에는 선단 노드 HPC 수요가 확대될 전망입니다 [E1].",
        evidence_report_years={1: 2025},
    )

    assert invalid == ()


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


def test_business_unit_attribution_check_is_skipped_for_multi_year_memory_strategy() -> None:
    prompt = """사용자 질문:
삼성전자의 2023년과 2025년 사업보고서를 기준으로 메모리·반도체 사업 전략이 어떻게 달라졌는지 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2023.12)
섹션: 반도체 사업
DDR5 대응을 강화했습니다.

[E2] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2025.12)
섹션: 반도체 사업
HBM 중심의 고부가 제품 대응을 강화했습니다.
"""
    content = "\n".join(
        (
            "1. **DS 부문**:",
            "- 2023년에는 DDR5 대응을 강화했습니다 [E1].",
            "- 2025년에는 HBM 중심 대응을 강화했습니다 [E2].",
        )
    )

    assert unsupported_business_unit_attributions(content, user_prompt=prompt) == ()


def test_business_unit_attribution_does_not_use_body_only_oled_as_sdc_signal() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서에서 AI와 관련된 핵심 사업 전략을 정리해줘

=== EVIDENCE PACK ===
[E4] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2025.12)
문서: 사업의 내용
섹션: 영상디스플레이 사업

AI TV 라인업을 확대했습니다.
모바일 산업에서는 OLED 적용도 확대되고 있습니다.
"""
    content = "\n".join(
        (
            "3. **SDC**:",
            "- AI TV 라인업을 확대했습니다 [E4].",
        )
    )

    invalid = unsupported_business_unit_attributions(content, user_prompt=prompt)

    assert "3. **SDC**:" in invalid
    assert "- AI TV 라인업을 확대했습니다 [E4]." in invalid


def test_business_unit_attribution_accepts_shared_explicit_subunit() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서에서 AI와 관련된 핵심 사업 전략을 정리해줘

=== EVIDENCE PACK ===
[E3] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2025.12)
문서: 사업의 내용
섹션: 모바일 사업

MX(Mobile eXperience) 사업은 Galaxy AI 도입을 확대합니다.
"""
    content = "\n".join(
        (
            "1. **DX 부문**:",
            "- MX(Mobile eXperience) 사업은 Galaxy AI 도입을 확대합니다 [E3].",
        )
    )

    assert unsupported_business_unit_attributions(content, user_prompt=prompt) == ()


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


def test_narrow_system_semiconductor_scope_rejects_memory_product_claims() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체는 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.

[E2] kind=semantic_chunk score=0.9
text:
2026년에는 HBM4와 고용량 DDR5 수요 확대가 예상됩니다.
"""
    content = "\n".join(
        (
            "- Advanced 노드 CAPA 확보를 위한 투자 진행 중입니다 [E1].",
            "- 2026년에는 HBM4와 고용량 DDR5 수요 확대가 예상됩니다 [E2].",
        )
    )

    invalid = unsupported_narrow_business_scope_claims(
        content,
        user_prompt=prompt,
    )

    assert invalid == (
        "- 2026년에는 HBM4와 고용량 DDR5 수요 확대가 예상됩니다 [E2].",
    )


def test_direction_purpose_query_rejects_unrequested_completed_amount() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
2025년 DS 부문 및 SDC 등의 첨단공정 증설·전환과 인프라 투자를 중심으로
52.7조원의 시설투자가 이루어졌습니다.
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.
"""
    content = (
        "삼성전자는 2025년 DS 부문 및 SDC 등에 52.7조원의 시설투자를 진행했습니다 [E1]."
    )

    assert unsupported_unrequested_investment_amounts(
        content,
        user_prompt=prompt,
    ) == (content,)


def test_investment_scope_structure_separates_related_strategy_from_purpose() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.

[E3] kind=semantic_chunk score=0.9
text:
System LSI 사업은 AI 성장에 따른 중장기 수요 확대를 기회로
고부가 수주 확대를 통해 수익 구조를 개선하고 응용처를 다변화합니다.
"""
    content = "\n".join(
        (
            "삼성전자의 시스템 반도체 투자 방향과 목적은 다음과 같습니다:",
            "1. **Advanced 노드 CAPA 확보**: 투자가 진행 중입니다 [E1].",
            "2. **고부가 수주 확대 및 수익 구조 개선**: 전략을 추진합니다 [E3].",
            "3. **응용처 다변화**: 신규 사업 기회를 검토합니다 [E3].",
        )
    )

    invalid = unsupported_investment_scope_structure(content, user_prompt=prompt)

    assert "삼성전자의 시스템 반도체 투자 방향과 목적은 다음과 같습니다:" in invalid
    assert "2. **고부가 수주 확대 및 수익 구조 개선**: 전략을 추진합니다 [E3]." in invalid
    assert "3. **응용처 다변화**: 신규 사업 기회를 검토합니다 [E3]." in invalid
    assert "1. **Advanced 노드 CAPA 확보**: 투자가 진행 중입니다 [E1]." not in invalid


def test_investment_purpose_rejects_uncited_market_share_inference() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.
"""
    content = (
        "이러한 투자 방향과 목적을 통해 삼성전자는 시장 점유율을 "
        "높이고자 하는 것으로 보입니다."
    )

    assert unsupported_investment_purpose_claims(
        content,
        user_prompt=prompt,
    ) == (content,)


def test_investment_purpose_rejects_uncited_interpretive_goal_sentence() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.

[E3] kind=semantic_chunk score=0.9
text:
System LSI는 고부가 수주 확대를 통해 수익 구조를 개선하고 응용처를 다변화합니다.
"""
    content = (
        "이와 같이 삼성전자는 시스템 반도체 분야에서 기술 혁신과 응용처 다변화를 통해 "
        "시장 경쟁력을 강화하고, 고부가가치 제품 중심의 수익 구조 개선을 목표로 하고 있습니다."
    )

    assert unsupported_investment_purpose_claims(content, user_prompt=prompt) == (content,)


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


def test_generate_grounded_answer_keeps_multi_year_ds_comparison_without_attribution_repair() -> None:
    prompt = """사용자 질문:
삼성전자의 2023년과 2025년 사업보고서를 기준으로 메모리·반도체 사업 전략이 어떻게 달라졌는지 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2023.12)
섹션: 반도체 사업
DDR5 대응을 강화했습니다.

[E2] kind=semantic_chunk score=1.0
text:
회사: 삼성전자
공시: 사업보고서 (2025.12)
섹션: 반도체 사업
HBM 중심의 고부가 제품 대응을 강화했습니다.
"""
    answer_text = "\n".join(
        (
            "1. **DS 부문**:",
            "- 2023년에는 DDR5 대응을 강화했습니다 [E1].",
            "- 2025년에는 HBM 중심의 고부가 제품 대응을 강화했습니다 [E2].",
        )
    )
    client = _FakeClient([answer_text])

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=2,
        evidence_report_years={1: 2023, 2: 2025},
    )

    assert answer.content == answer_text
    assert answer.finish_reason == "stop"
    assert len(client.calls) == 1


def test_missing_required_company_mentions_detects_omitted_comparison_side() -> None:
    prompt = """사용자 질문:
삼성전자와 카카오의 2025년 사업보고서에서 핵심 사업 전략을 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
company=삼성전자 report=사업보고서 (2025.12)
text:
AI 제품 전략을 확대합니다.

[E2] kind=semantic_chunk score=1.0
company=카카오 report=사업보고서 (2025.12)
text:
AI 서비스 전략을 확대합니다.
"""
    content = "삼성전자는 AI 제품 전략을 확대합니다 [E1]."

    assert missing_required_company_mentions(
        content,
        user_prompt=prompt,
    ) == ("카카오",)


def test_multi_company_comparison_requires_cross_company_grounded_synthesis() -> None:
    prompt = """사용자 질문:
삼성전자와 카카오의 2025년 사업보고서에서 핵심 사업 전략을 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
company=삼성전자 report=사업보고서 (2025.12)
text:
AI 제품 전략을 확대합니다.

[E2] kind=semantic_chunk score=1.0
company=카카오 report=사업보고서 (2025.12)
text:
AI 서비스 전략을 확대합니다.
"""
    separate_summaries = (
        "삼성전자는 AI 제품 전략을 확대합니다 [E1]. "
        "카카오는 AI 서비스 전략을 확대합니다 [E2]."
    )
    comparison = (
        "삼성전자는 AI를 제품 경쟁력 강화에 활용하는 반면, "
        "카카오는 플랫폼 서비스 확대에 활용합니다 [E1][E2]."
    )

    assert missing_multi_company_comparison_synthesis(
        separate_summaries,
        user_prompt=prompt,
    ) == ("[MULTI_COMPANY_COMPARISON_REQUIRED]",)
    assert missing_multi_company_comparison_synthesis(
        comparison,
        user_prompt=prompt,
    ) == ()


def test_multi_company_comparison_accepts_company_specific_citations_across_answer() -> None:
    prompt = """사용자 질문:
A사와 B사의 2025년 사업보고서에서 전략 차이를 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
company=A사 report=사업보고서 (2025.12)
text:
AI 제품 전략을 확대합니다.

[E2] kind=semantic_chunk score=1.0
company=B사 report=사업보고서 (2025.12)
text:
플랫폼 서비스 전략을 확대합니다.
"""
    content = (
        "A사는 AI 제품 전략을 확대합니다 [E1]. "
        "반면 B사는 플랫폼 서비스 전략을 확대합니다 [E2]."
    )

    assert missing_multi_company_comparison_synthesis(
        content,
        user_prompt=prompt,
    ) == ()


def test_generate_grounded_answer_repairs_missing_comparison_synthesis() -> None:
    prompt = """사용자 질문:
삼성전자와 카카오의 2025년 사업보고서에서 핵심 사업 전략을 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
company=삼성전자 report=사업보고서 (2025.12)
text:
AI 제품 전략을 확대합니다.

[E2] kind=semantic_chunk score=1.0
company=카카오 report=사업보고서 (2025.12)
text:
AI 서비스 전략을 확대합니다.
"""
    client = _FakeClient(
        [
            (
                "삼성전자는 AI 제품 전략을 확대합니다 [E1]. "
                "카카오는 AI 서비스 전략을 확대합니다 [E2]."
            ),
            (
                "삼성전자는 AI를 제품 경쟁력 강화에 활용하는 반면, "
                "카카오는 AI를 플랫폼 서비스 확대에 활용합니다 [E1][E2]."
            ),
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=2,
    )

    assert "[E1][E2]" in answer.content
    assert "삼성전자" in answer.content
    assert "카카오" in answer.content
    assert len(client.calls) == 2
    assert "기업별 요약만 나열하지 말고" in client.calls[1]


def test_generate_grounded_answer_repairs_missing_comparison_company() -> None:
    prompt = """사용자 질문:
삼성전자와 카카오의 2025년 사업보고서에서 핵심 사업 전략을 비교해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
company=삼성전자 report=사업보고서 (2025.12)
text:
AI 제품 전략을 확대합니다.

[E2] kind=semantic_chunk score=1.0
company=카카오 report=사업보고서 (2025.12)
text:
AI 서비스 전략을 확대합니다.
"""
    client = _FakeClient(
        [
            "삼성전자는 AI 제품 전략을 확대합니다 [E1].",
            (
                "삼성전자는 AI 제품 전략을 확대합니다 [E1]. "
                "카카오는 AI 서비스 전략을 확대합니다 [E2]. "
                "두 기업을 비교하면 삼성전자는 AI를 제품 전략에, "
                "카카오는 AI를 서비스 전략에 활용한다는 차이가 있습니다 [E1][E2]."
            ),
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=2,
    )

    assert "삼성전자" in answer.content
    assert "카카오" in answer.content
    assert len(client.calls) == 2
    assert "비교 대상: 삼성전자, 카카오" in client.calls[1]


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


def test_generate_grounded_answer_repairs_narrow_scope_without_rejecting_forecast_year() -> None:
    prompt = """사용자 질문:
삼성전자의 2025년 사업보고서를 기준으로 시스템 반도체의 투자 방향과 목적을 설명해줘

=== EVIDENCE PACK ===
[E1] kind=semantic_chunk score=1.0
text:
시스템 반도체는 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.
메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진하였습니다.

[E3] kind=semantic_chunk score=0.9
text:
2026년에는 선단 노드 HPC 및 모바일 본격 양산에 따른 수요가 확대될 전망입니다.
"""
    client = _FakeClient(
        [
            (
                "직접 확인되는 투자 방향/목적:\n"
                "- Advanced 노드 CAPA 확보를 위한 투자 진행 중입니다 [E1].\n"
                "관련 사업 전략:\n"
                "- 메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진합니다 [E1].\n"
                "- 2026년에는 선단 노드 HPC 수요가 확대될 전망입니다 [E3]."
            ),
            (
                "직접 확인되는 투자 방향/목적:\n"
                "- Advanced 노드 CAPA 확보를 위한 투자 진행 중입니다 [E1].\n"
                "관련 사업 전략:\n"
                "- 2026년에는 선단 노드 HPC 수요가 확대될 전망입니다 [E3]."
            ),
        ]
    )

    answer = generate_grounded_answer(
        client,
        system_prompt="system",
        user_prompt=prompt,
        evidence_count=3,
        evidence_report_years={1: 2025, 3: 2025},
    )

    assert "Advanced 노드 CAPA" in answer.content
    assert "2026년" in answer.content
    assert "메모리 차세대" not in answer.content
    assert answer.finish_reason == "stop"
    assert len(client.calls) == 2


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