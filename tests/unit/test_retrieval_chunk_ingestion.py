from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import orjson
import pytest

from disclosure_agent.retrieval.chunk_ingestion import (
    SourceTable,
    chunk_run_id,
    load_approved_plan,
    table_chunks,
)


def _table(text: str, cells: list[dict] | None = None) -> SourceTable:
    return SourceTable(
        table_id="table-1",
        document_group="exchange",
        filing_id="filing-1",
        document_id="document-1",
        section_id="section-1",
        section_title="계약 내용",
        block_id="block-1",
        block_order=7,
        caption="계약 표",
        normalized_text=text,
        grid={"header_row_indices": [0], "cells": cells or []},
    )


def test_direct_table_preserves_provenance() -> None:
    chunks = table_chunks(_table("계약상대 Tesla 계약금액 100억원"))

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "table-1:retrieval:table:1"
    assert chunk.content == "계약상대 Tesla 계약금액 100억원"
    assert chunk.source_table_id == "table-1"
    assert chunk.source_block_ids == ("block-1",)
    assert chunk.heading_path == ("계약 내용",)
    assert chunk.table_row_start is None


def test_long_table_splits_on_rows_and_repeats_headers() -> None:
    cells = [
        {"row_index": 0, "column_index": 0, "text_normalized": "항목"},
        {"row_index": 0, "column_index": 1, "text_normalized": "값"},
    ]
    for row in range(1, 7):
        cells.extend(
            [
                {
                    "row_index": row,
                    "column_index": 0,
                    "text_normalized": f"항목-{row}",
                },
                {
                    "row_index": row,
                    "column_index": 1,
                    "text_normalized": "가" * 35,
                },
            ]
        )
    chunks = table_chunks(_table("원본" * 200, cells), max_chars=120)

    assert len(chunks) >= 3
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert all("항목 | 값" in chunk.content for chunk in chunks)
    assert chunks[0].table_row_start == 1
    assert chunks[-1].table_row_end == 6
    assert [chunk.chunk_id for chunk in chunks] == [
        f"table-1:retrieval:table:{index}" for index in range(1, len(chunks) + 1)
    ]


def test_repeated_data_rows_are_not_deduplicated() -> None:
    cells = [
        {"row_index": 0, "column_index": 0, "text_normalized": "항목"},
        {"row_index": 1, "column_index": 0, "text_normalized": "동일"},
        {"row_index": 2, "column_index": 0, "text_normalized": "동일"},
    ]
    chunks = table_chunks(_table("원본" * 200, cells), max_chars=100)

    assert chunks[0].content.count("동일") == 2


def test_oversized_single_row_stays_bounded() -> None:
    cells = [
        {"row_index": 1, "column_index": 0, "text_normalized": "가" * 500},
    ]
    chunks = table_chunks(
        _table("가" * 500, cells),
        max_chars=100,
        overlap_chars=10,
    )

    assert len(chunks) > 1
    assert all(0 < len(chunk.content) <= 100 for chunk in chunks)
    assert all(chunk.table_row_start == 1 for chunk in chunks)
    assert all(chunk.table_row_end == 1 for chunk in chunks)


def test_plan_validation_and_run_identity(tmp_path: Path) -> None:
    payload = {
        "plan_version": "4.0.0",
        "mode": "read_only_table_dry_run",
        "load": {"load_run_id": "load-1", "manifest_sha256": "a" * 64},
        "policy": {
            "tables": "vector_structured_lexical_lanes_not_persisted",
            "narrative_min_chars": 400,
            "narrative_target_chars": 1000,
            "narrative_max_chars": 1200,
            "narrative_overlap_chars": 120,
            "table_max_chars": 2000,
        },
        "narrative": {"chunks": 133092},
        "tables": {
            "initial_chunk_estimate_excluding_review_buckets": 36959,
            "buckets": [
                {"decision_bucket": "exchange_direct", "tables": 3408},
                {"decision_bucket": "periodic_numeric_structured", "tables": 442453},
                {"decision_bucket": "direct_candidate", "tables": 28794},
                {"decision_bucket": "row_window_candidate", "tables": 934},
            ],
        },
    }
    path = tmp_path / "plan.json"
    raw = orjson.dumps(payload)
    path.write_bytes(raw)

    plan = load_approved_plan(path)

    assert plan.sha256 == sha256(raw).hexdigest()
    assert plan.vector_source_tables == 33136
    assert plan.estimated_table_chunks == 36959
    assert chunk_run_id(plan) == chunk_run_id(plan)


def test_rejects_old_plan_version(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(orjson.dumps({"plan_version": "3.0.0"}))

    with pytest.raises(ValueError, match="Expected retrieval plan"):
        load_approved_plan(path)
