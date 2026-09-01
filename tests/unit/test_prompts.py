from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack


def _pack() -> EvidencePack:
    item = EvidenceItem(
        evidence_id="sql:revenue:fact-1",
        source_kind="sql_revenue",
        rank=1,
        score=1.0,
        semantic_score=0.0,
        lexical_score=0.0,
        company_name="카카오",
        filing_id="periodic_1",
        report_name="사업보고서 (2025.12)",
        document_id="document:1",
        section_id="section:1",
        content_text="연결기준 매출액: 100원",
        truncated=False,
        matched_terms=(),
        block_ids=("block:1",),
        table_ids=("table:1",),
        fact_ids=("fact:1",),
    )
    return EvidencePack(
        query="카카오의 매출액은 얼마인가?",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )


def test_grounding_prompt_requires_explicit_insufficient_evidence_phrase() -> None:
    assert "제공된 공시에서 확인되지 않는다." in GROUNDING_SYSTEM_PROMPT


def test_grounding_prompt_prefers_normalized_final_values() -> None:
    assert "정규화된 최종 값" in GROUNDING_SYSTEM_PROMPT


def test_build_grounded_answer_prompt_contains_question_and_evidence() -> None:
    prompt = build_grounded_answer_prompt("카카오의 매출액은 얼마인가?", _pack())

    assert "카카오의 매출액은 얼마인가?" in prompt
    assert "[E1] kind=sql_revenue" in prompt
    assert "연결기준 매출액: 100원" in prompt
    assert "fact_ids=fact:1" in prompt
    assert "사용자 표시 금액: X" in prompt
    assert "유효한 인용은 Evidence 번호인 [E숫자]뿐" in prompt
