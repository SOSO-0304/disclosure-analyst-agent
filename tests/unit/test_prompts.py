from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack


def _item(
    *,
    rank: int = 1,
    source_kind: str = "sql_revenue",
    content_text: str = "연결기준 매출액: 100원",
    filing_id: str = "periodic_1",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"evidence:{rank}",
        source_kind=source_kind,
        rank=rank,
        score=1.0,
        semantic_score=0.0,
        lexical_score=0.0,
        company_name="카카오",
        filing_id=filing_id,
        report_name="사업보고서 (2025.12)",
        document_id="document:1",
        section_id="section:1",
        content_text=content_text,
        truncated=False,
        matched_terms=(),
        block_ids=("block:1",),
        table_ids=("table:1",),
        fact_ids=("fact:1",),
    )


def _pack() -> EvidencePack:
    item = _item()
    return EvidencePack(
        query="카카오의 매출액은 얼마인가?",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )


def _fundraising_absence_pack() -> EvidencePack:
    items = tuple(
        _item(
            rank=index,
            source_kind="fundraising_event",
            content_text=f"전환사채(CB) canonical 이벤트 {index}",
            filing_id=f"periodic_{index}",
        )
        for index in range(1, 4)
    )
    analysis = "\n".join(
        (
            "analysis_type: fundraising_by_instrument",
            "status: ANSWERABLE",
            "company: 우리기술",
            "year: 2025",
            "derived_from: [E1],[E2],[E3]",
            "유형: 유상증자 | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음",
            "유형: 전환사채(CB) | 3건 | 합계 378억 원 | [E1],[E2],[E3]",
            "유형: 신주인수권부사채(BW) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음",
            "유형: 교환사채(EB) | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음",
        )
    )
    return EvidencePack(
        query="우리기술의 2025년 BW와 EB 발행 내역만 확인해줘",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
        deterministic_analysis=analysis,
    )


def test_grounding_prompt_requires_explicit_insufficient_evidence_phrase() -> None:
    assert "제공된 공시에서 확인되지 않는다." in GROUNDING_SYSTEM_PROMPT


def test_grounding_prompt_prefers_normalized_final_values() -> None:
    assert "정규화된 최종 값" in GROUNDING_SYSTEM_PROMPT


def test_grounding_prompt_separates_investment_results_from_plans() -> None:
    assert "지속·향후 투자 방향" in GROUNDING_SYSTEM_PROMPT
    assert "집행 실적을 '주요 투자 계획' 목록의 항목처럼 배치하지 마세요" in (
        GROUNDING_SYSTEM_PROMPT
    )


def test_grounding_prompt_respects_explicit_fundraising_subset() -> None:
    assert "특정 자금조달 유형을 명시했다면 요청한 유형만 답변하세요" in (
        GROUNDING_SYSTEM_PROMPT
    )


def test_build_grounded_answer_prompt_contains_question_and_evidence() -> None:
    prompt = build_grounded_answer_prompt("카카오의 매출액은 얼마인가?", _pack())

    assert "카카오의 매출액은 얼마인가?" in prompt
    assert "[E1] kind=sql_revenue" in prompt
    assert "연결기준 매출액: 100원" in prompt
    assert "fact_ids=fact:1" in prompt
    assert "사용자 표시 금액: X" in prompt
    assert "유효한 인용은 Evidence 번호인 [E숫자]뿐" in prompt
    assert "확인된 투자 실적" in prompt


def test_partial_fundraising_query_keeps_only_requested_zero_event_types() -> None:
    prompt = build_grounded_answer_prompt(
        "우리기술의 2025년 BW와 EB 발행 내역만 확인해줘",
        _fundraising_absence_pack(),
    )

    assert (
        "사용자가 명시적으로 요청한 자금조달 유형은 "
        "신주인수권부사채(BW), 교환사채(EB)입니다"
    ) in prompt
    assert "이 유형만 답변하고 다른 자금조달 유형은 설명하지 마세요" in prompt
    assert "집계 범위 근거: [E1][E2][E3]" in prompt
    assert "0건 유형 문장 자체에는 다른 유형의 Evidence를 억지로 붙이지 마세요" in prompt


def test_actual_execution_question_gets_facility_semantic_guardrail() -> None:
    item = _item(
        source_kind="semantic_chunk",
        content_text=(
            "신규시설투자등 공시이며 투자금액과 이사회결정일이 기재되어 있습니다. "
            "이 공시는 투자 결정 내역을 나타냅니다."
        ),
    )
    pack = EvidencePack(
        query="한화오션은 2025년에 실제로 6,008억 원을 투자한 것으로 보면 돼?",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "투자 결정 금액을 실제 집행액으로 해석해도 되는지" in prompt
    assert "실제 집행액이라고 단정하기는 어렵다" in prompt
    assert "투자 결정 금액과 실제 집행액을 명확히 구분하세요" in prompt


def test_comparison_prompt_prioritizes_concise_direct_difference() -> None:
    item = _item(
        source_kind="semantic_chunk",
        content_text="2024년에는 제품 경쟁력을 강화했고 2025년에는 AI 수요 대응을 확대했습니다.",
    )
    pack = EvidencePack(
        query="A사의 2024년과 2025년 사업보고서에서 전략 차이를 비교해줘",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "핵심 전략을 1~2개 수준으로 먼저 압축" in prompt
    assert "다음 연도 전망·시장 배경" in prompt


def test_investment_direction_prompt_does_not_pad_with_related_strategy() -> None:
    item = _item(
        source_kind="semantic_chunk",
        content_text=(
            "신규 생산라인 CAPA 확보를 위한 투자를 진행 중입니다. "
            "시장 수요는 확대될 전망입니다."
        ),
    )
    pack = EvidencePack(
        query="A사의 투자 방향과 목적을 설명해줘",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "별도의 '관련 사업 전략'" in prompt
    assert "투자와 직접 연결된 근거만 답변하세요" in prompt


def test_future_plan_exclusion_query_adds_completeness_checklist() -> None:
    content = "\n".join(
        (
            "2026년 1분기 11.2조원의 시설투자가 이루어졌습니다.",
            "메모리 차세대 기술 경쟁력 강화를 위한 투자를 지속 추진할 계획입니다.",
            "시스템 반도체 Advanced 노드 CAPA 확보를 위한 투자도 진행 중입니다.",
            "내실을 다지는 활동을 통해 투자 효율성 제고에도 집중할 계획입니다.",
        )
    )
    item = _item(source_kind="semantic_chunk", content_text=content)
    pack = EvidencePack(
        query=(
            "삼성전자의 2026년 1분기 분기보고서에서 이미 집행된 투자 금액은 빼고, "
            "현재 진행 중이거나 앞으로 계획한 투자 방향만 정리해줘"
        ),
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(content),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "집행 금액과 완료 실적은 답변에서 제외" in prompt
    assert "병렬로 제시된 서로 다른 지속·진행·계획 방향은 빠짐없이" in prompt
    assert "누락 방지용 계획 표현 체크리스트" in prompt
    assert "Advanced 노드 CAPA 확보" in prompt
    assert "투자 효율성 제고에도 집중할 계획입니다" in prompt
