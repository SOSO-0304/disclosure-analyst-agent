from disclosure_agent.llm.prompts import build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack


def _item(*, content_text: str, source_kind: str = "semantic_chunk") -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evidence:1",
        source_kind=source_kind,
        rank=1,
        score=1.0,
        semantic_score=1.0,
        lexical_score=1.0,
        company_name="테스트기업",
        filing_id="filing:1",
        report_name="사업보고서 (2025.12)",
        document_id="document:1",
        section_id="section:1",
        content_text=content_text,
        truncated=False,
        matched_terms=(),
        block_ids=("block:1",),
        table_ids=("table:1",),
    )


def test_capex_actual_execution_question_gets_semantic_guardrail() -> None:
    item = _item(
        content_text=(
            "신규시설투자 결정 공시입니다. 투자금액은 806,800,000,000원이며 "
            "이사회결정일이 기재되어 있습니다."
        )
    )
    pack = EvidencePack(
        query="두산에너빌리티가 2025년에 실제로 집행한 CAPEX가 8,068억 원이라는 뜻이야?",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "투자 결정 금액을 실제 집행액으로 해석해도 되는지" in prompt
    assert "실제 집행액이라고 단정하기는 어렵다" in prompt


def test_supply_correction_cause_question_forbids_causal_inference() -> None:
    item = _item(
        content_text="첫 번째 정정공시에서 거래상대방이 ㈜태영건설로 확인됩니다."
    )
    pack = EvidencePack(
        query=(
            "두산퓨얼셀의 2023년 연료전지 시스템 공급 계약에서 첫 번째 정정공시는 "
            "거래상대방을 태영건설로 변경하기 위해 낸 거야?"
        ),
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "정정 사유는 제공된 공시에서 확인되지 않습니다" in prompt
    assert "그 값 때문에 정정했다는 인과 해석을 반드시 구분" in prompt
    assert "거래상대방을 변경하기 위해 정정했다" in prompt


def test_zero_event_subset_requires_explicit_absence_wording() -> None:
    items = tuple(
        EvidenceItem(
            evidence_id=f"event:{index}",
            source_kind="sql_fundraising",
            rank=index,
            score=1.0,
            semantic_score=0.0,
            lexical_score=0.0,
            company_name="우리기술",
            filing_id=f"filing:{index}",
            report_name="사업보고서 (2025.12)",
            document_id=f"document:{index}",
            section_id=f"section:{index}",
            content_text=f"전환사채(CB) 이벤트 {index}",
            truncated=False,
            matched_terms=(),
            block_ids=(),
            table_ids=(),
        )
        for index in range(1, 4)
    )
    analysis = "\n".join(
        (
            "analysis_type: fundraising_by_instrument",
            "derived_from: [E1],[E2],[E3]",
            "유형: 유상증자 | 확인된 이벤트 0건 | 금액 0원으로 해석하지 않음",
            "유형: 전환사채(CB) | 3건 | 합계 378억 원 | [E1],[E2],[E3]",
        )
    )
    pack = EvidencePack(
        query="우리기술이 2025년에 유상증자로 조달한 내역이 있어?",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
        deterministic_analysis=analysis,
    )

    prompt = build_grounded_answer_prompt(pack.query, pack)

    assert "사용자가 명시적으로 요청한 자금조달 유형은 유상증자입니다" in prompt
    assert "요청 유형이 0건이면 유형명을 생략하지 말고 '확인된 내역 없음'으로 답하세요" in prompt
    assert (
        "최종 답변의 첫 부분에 다음 표준 표현을 그대로 포함하세요: "
        "유상증자: 확인된 내역 없음"
    ) in prompt
    assert "0건 유형 문장 자체에는 다른 유형의 Evidence를 억지로 붙이지 마세요" in prompt
