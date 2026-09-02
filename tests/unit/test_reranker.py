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

    assert len(reranked) == 1
    assert reranked[0].hit.chunk_id == "plan"
    assert reranked[0].matched_terms == ("투자", "계획", "목적")
    assert reranked[0].lexical_score == 1.0


def test_investment_plan_query_filters_shareholder_return_chunk() -> None:
    candidates = (
        _hit(
            "shareholder-return",
            0.75,
            "배당정책과 주주환원 계획 및 자사주 취득 계획을 설명합니다.",
        ),
        _hit(
            "facility",
            0.64,
            "시설투자 현황: 첨단공정 증설·전환과 인프라 투자를 추진합니다.",
        ),
    )

    reranked = rerank_semantic_hits(
        "삼성전자의 2025년 사업보고서 주요 투자 계획과 목적",
        candidates,
        company_name="삼성전자",
        year=2025,
        top_k=5,
    )

    assert tuple(item.hit.chunk_id for item in reranked) == ("facility",)


def test_business_change_comparison_filters_company_history_chunk() -> None:
    candidates = (
        _hit(
            "history",
            0.72,
            "회사의 연혁과 대표이사 선임, 최대주주의 변동 사항을 설명합니다.",
        ),
        _hit(
            "business",
            0.63,
            "DX 부문은 Galaxy AI를 확대하고 DS 부문은 HBM 수요에 대응했습니다.",
        ),
    )

    reranked = rerank_semantic_hits(
        "삼성전자의 2023년과 2025년 사업보고서 핵심 사업 변화를 비교해줘",
        candidates,
        company_name="삼성전자",
        year=2025,
        top_k=5,
    )

    assert tuple(item.hit.chunk_id for item in reranked) == ("business",)
