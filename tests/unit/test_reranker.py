from disclosure_agent.retrieval.reranker import query_terms, rerank_semantic_hits
from disclosure_agent.storage.retrieval_embedding_repository import SemanticSearchHit


def _hit(chunk_id: str, similarity: float, text: str) -> SemanticSearchHit:
    return SemanticSearchHit(
        chunk_id=chunk_id,
        company_name="카카오",
        filing_id=f"filing-{chunk_id}",
        report_name="사업보고서 (2025.12)",
        document_id=f"document-{chunk_id}",
        section_id=None,
        similarity=similarity,
        content_text=text,
        block_ids=(),
        table_ids=(),
    )


def test_query_terms_strip_company_year_and_particles() -> None:
    terms = query_terms(
        "카카오의 2025년 주요 투자 계획과 투자 목적을 정리해줘",
        company_name="카카오",
        year=2025,
    )

    assert "카카오" not in terms
    assert "2025" not in terms
    assert "투자" in terms
    assert "계획" in terms
    assert "목적" in terms


def test_reranker_promotes_specific_lexical_evidence() -> None:
    candidates = (
        _hit("semantic", 0.70, "유형자산 취득과 기계장치 현황"),
        _hit("plan", 0.66, "신규 투자 계획과 투자 목적 및 예상 투자금액"),
        _hit("investment", 0.68, "타법인 투자 현황과 출자 내역"),
    )

    reranked = rerank_semantic_hits(
        "카카오의 2025년 주요 투자 계획과 투자 목적",
        candidates,
        company_name="카카오",
        year=2025,
        top_k=3,
    )

    assert reranked[0].hit.chunk_id == "plan"
    assert reranked[0].matched_terms == ("투자", "계획", "목적")
    assert reranked[0].lexical_score == 1.0
