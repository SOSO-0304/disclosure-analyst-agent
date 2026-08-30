from disclosure_agent.storage.retrieval_chunk_repository import (
    DocumentContext,
    SourceBlockText,
    build_document_chunks,
)


def _context() -> DocumentContext:
    return DocumentContext(
        corp_code="001",
        company_name="카카오",
        filing_id="periodic_1",
        report_name="사업보고서 (2025.12)",
        document_id="periodic_1:main",
        document_title="사업보고서",
    )


def test_chunk_builder_does_not_cross_sections() -> None:
    blocks = (
        SourceBlockText("b1", 1, "s1", "사업의 내용", None, "첫 번째 문단"),
        SourceBlockText("b2", 2, "s2", "재무에 관한 사항", None, "두 번째 문단"),
    )

    chunks = build_document_chunks(_context(), blocks)

    assert len(chunks) == 2
    assert chunks[0].section_id == "s1"
    assert chunks[1].section_id == "s2"
    assert "섹션: 사업의 내용" in chunks[0].content_text
    assert "섹션: 재무에 관한 사항" in chunks[1].content_text


def test_long_table_is_split_and_keeps_table_lineage() -> None:
    blocks = (
        SourceBlockText(
            "b1",
            1,
            "s1",
            "재무에 관한 사항",
            "t1",
            "매출액 " * 900,
        ),
    )

    chunks = build_document_chunks(
        _context(),
        blocks,
        max_chars=1200,
        overlap_chars=100,
    )

    assert len(chunks) > 1
    assert all(chunk.table_ids == ("t1",) for chunk in chunks)
    assert all(chunk.block_ids == ("b1",) for chunk in chunks)
    assert all(len(chunk.content_text) <= 1200 for chunk in chunks)


def test_chunk_ids_are_deterministic() -> None:
    blocks = (
        SourceBlockText("b1", 1, "s1", "사업의 내용", None, "동일한 문단"),
    )

    first = build_document_chunks(_context(), blocks)
    second = build_document_chunks(_context(), blocks)

    assert first[0].chunk_id == second[0].chunk_id
    assert first[0].content_sha256 == second[0].content_sha256
