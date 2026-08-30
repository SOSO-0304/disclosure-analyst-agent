from disclosure_agent.retrieval.chunk_planner import (
    ChunkPolicy,
    NarrativeChunkPlanner,
    SourceBlock,
)


def _block(
    order: int,
    block_type: str,
    text: str | None = None,
    *,
    document_id: str = "doc-1",
    section_id: str = "section-1",
    section_title: str = "사업의 내용",
    heading_level: int | None = None,
) -> SourceBlock:
    return SourceBlock(
        document_group="periodic",
        filing_id="filing-1",
        document_id=document_id,
        section_id=section_id,
        section_title=section_title,
        block_id=f"{document_id}:block:{order}",
        block_order=order,
        block_type=block_type,
        text=text,
        heading_level=heading_level,
    )


def _plan(blocks: list[SourceBlock], policy: ChunkPolicy | None = None):
    planner = NarrativeChunkPlanner(policy or ChunkPolicy())
    chunks = []
    for block in blocks:
        chunks.extend(planner.push(block))
    chunks.extend(planner.finish())
    return chunks


def test_adjacent_paragraphs_merge_with_section_and_heading_provenance() -> None:
    chunks = _plan(
        [
            _block(0, "heading", "주요 제품", heading_level=1),
            _block(1, "paragraph", "첫 번째 문단"),
            _block(2, "paragraph", "두 번째 문단"),
        ]
    )

    assert len(chunks) == 1
    assert chunks[0].text == "첫 번째 문단\n\n두 번째 문단"
    assert chunks[0].heading_path == ("사업의 내용", "주요 제품")
    assert chunks[0].block_ids == ("doc-1:block:1", "doc-1:block:2")


def test_table_and_unknown_are_hard_boundaries_but_page_break_is_not() -> None:
    chunks = _plan(
        [
            _block(0, "paragraph", "앞"),
            _block(1, "page_break"),
            _block(2, "paragraph", "뒤"),
            _block(3, "table"),
            _block(4, "paragraph", "표 다음"),
            _block(5, "unknown", "검토"),
            _block(6, "paragraph", "unknown 다음"),
        ]
    )

    assert [chunk.text for chunk in chunks] == [
        "앞\n\n뒤",
        "표 다음",
        "unknown 다음",
    ]


def test_heading_and_section_changes_flush_previous_context() -> None:
    chunks = _plan(
        [
            _block(0, "paragraph", "A"),
            _block(1, "heading", "새 제목", heading_level=2),
            _block(2, "paragraph", "B"),
            _block(
                3,
                "paragraph",
                "C",
                section_id="section-2",
                section_title="위험 요인",
            ),
        ]
    )

    assert [chunk.text for chunk in chunks] == ["A", "B", "C"]
    assert chunks[1].heading_path == ("사업의 내용", "새 제목")
    assert chunks[2].heading_path == ("위험 요인",)


def test_oversized_paragraph_splits_with_bounded_overlap() -> None:
    policy = ChunkPolicy(target_chars=10, max_chars=12, overlap_chars=3)
    chunks = _plan([_block(0, "paragraph", "abcdefghijklmnopqrstuvwxyz")], policy)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= 12 for chunk in chunks)
    assert chunks[0].text[-3:] == chunks[1].text[:3]
    assert chunks[1].text[-3:] == chunks[2].text[:3]
    assert all(chunk.block_ids == ("doc-1:block:0",) for chunk in chunks)


def test_document_boundary_never_joins_text_and_resets_chunk_key() -> None:
    chunks = _plan(
        [
            _block(0, "paragraph", "첫 문서"),
            _block(0, "paragraph", "둘째 문서", document_id="doc-2"),
        ]
    )

    assert [chunk.text for chunk in chunks] == ["첫 문서", "둘째 문서"]
    assert chunks[0].chunk_key == "doc-1:narrative:1"
    assert chunks[1].chunk_key == "doc-2:narrative:1"


def test_policy_rejects_invalid_boundaries() -> None:
    try:
        ChunkPolicy(target_chars=1_000, max_chars=500)
    except ValueError as exc:
        assert "max_chars" in str(exc)
    else:
        raise AssertionError("invalid policy was accepted")
