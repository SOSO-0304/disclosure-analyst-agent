from disclosure_agent.retrieval.evidence_pack import (
    build_semantic_evidence_pack,
    render_evidence_pack,
)
from disclosure_agent.retrieval.reranker import RerankedSemanticHit
from disclosure_agent.storage.retrieval_embedding_repository import SemanticSearchHit


def _hit(*, chunk_id: str, text: str, similarity: float = 0.7) -> RerankedSemanticHit:
    semantic_hit = SemanticSearchHit(
        chunk_id=chunk_id,
        company_name="카카오",
        filing_id="periodic_1",
        report_name="분기보고서 (2025.09)",
        document_id="periodic_1:primary_report",
        section_id="section:12",
        similarity=similarity,
        content_text=text,
        block_ids=(f"block:{chunk_id}",),
        table_ids=(f"table:{chunk_id}",),
    )
    return RerankedSemanticHit(
        hit=semantic_hit,
        lexical_score=0.8,
        final_score=0.725,
        matched_terms=("투자", "계획"),
    )


def test_build_semantic_evidence_pack_preserves_lineage_and_budget() -> None:
    hits = (
        _hit(chunk_id="c1", text="A" * 20),
        _hit(chunk_id="c2", text="B" * 20),
    )

    pack = build_semantic_evidence_pack(
        "투자 계획",
        hits,
        max_items=2,
        max_chars_per_item=15,
        max_total_chars=25,
    )

    assert pack.retrieval_status == "MATCHES_FOUND"
    assert pack.total_chars == 25
    assert len(pack.items) == 2
    assert pack.items[0].content_text == "A" * 15
    assert pack.items[0].block_ids == ("block:c1",)
    assert pack.items[0].table_ids == ("table:c1",)
    assert pack.items[0].truncated is True
    assert pack.items[1].content_text == "B" * 10
    assert pack.items[1].truncated is True


def test_empty_hits_produce_no_match_pack() -> None:
    pack = build_semantic_evidence_pack("없는 질문", ())

    assert pack.retrieval_status == "NO_MATCH"
    assert pack.items == ()
    assert pack.total_chars == 0


def test_render_evidence_pack_contains_source_identifiers() -> None:
    pack = build_semantic_evidence_pack("투자 계획", (_hit(chunk_id="c1", text="근거 본문"),))

    rendered = render_evidence_pack(pack)

    assert "[E1] kind=semantic_chunk" in rendered
    assert "filing_id=periodic_1" in rendered
    assert "block_ids=block:c1" in rendered
    assert "table_ids=table:c1" in rendered
    assert "근거 본문" in rendered
