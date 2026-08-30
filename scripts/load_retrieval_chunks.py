#!/usr/bin/env python3
"""Materialize the approved v4 retrieval plan into the perf PostgreSQL database."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import orjson
from sqlalchemy import Connection, text

from disclosure_agent.retrieval.chunk_ingestion import (
    ApprovedChunkPlan,
    MaterializedChunk,
    SourceTable,
    chunk_run_id,
    load_approved_plan,
    narrative_chunk,
    table_chunks,
)
from disclosure_agent.retrieval.chunk_planner import (
    ChunkPolicy,
    NarrativeChunkPlanner,
    SourceBlock,
)
from disclosure_agent.storage.database import get_engine

BLOCK_STREAM_SQL = """
    SELECT
        f.document_group,
        b.filing_id,
        b.document_id,
        b.section_id,
        s.title_normalized AS section_title,
        b.block_id,
        b.block_order,
        b.block_type,
        CASE
            WHEN b.block_type IN ('paragraph', 'heading', 'unknown')
            THEN b.text_normalized
            ELSE NULL
        END AS text_normalized,
        b.heading_level
    FROM public.source_blocks b
    JOIN public.source_filings f USING (filing_id)
    LEFT JOIN public.source_sections s USING (section_id)
    WHERE b.block_type <> 'page_break'
    ORDER BY b.document_id, b.block_order
"""

TABLE_STREAM_SQL = """
    SELECT
        t.table_id,
        f.document_group,
        t.filing_id,
        t.document_id,
        b.section_id,
        s.title_normalized AS section_title,
        t.block_id,
        b.block_order,
        t.caption_normalized,
        t.normalized_text,
        t.grid
    FROM public.source_tables t
    JOIN public.source_filings f USING (filing_id)
    JOIN public.source_blocks b USING (block_id)
    LEFT JOIN public.source_sections s ON s.section_id = b.section_id
    WHERE t.normalized_text <> ''
      AND (
        f.document_group = 'exchange'
        OR (
          f.document_group IN ('holding', 'major')
          AND t.parent_table_id IS NULL
          AND NOT (
            char_length(t.normalized_text) <= 50
            AND t.row_count::bigint * t.column_count::bigint <= 4
          )
        )
      )
    ORDER BY t.document_id, b.block_order, t.table_id
"""

INSERT_CHUNK_SQL = """
    INSERT INTO public.retrieval_chunks (
        chunk_id,
        chunk_run_id,
        filing_id,
        document_id,
        section_id,
        document_group,
        chunk_type,
        content,
        content_sha256,
        char_count,
        heading_path,
        source_block_ids,
        source_table_id,
        start_block_order,
        end_block_order,
        table_row_start,
        table_row_end,
        metadata
    ) VALUES (
        :chunk_id,
        :chunk_run_id,
        :filing_id,
        :document_id,
        :section_id,
        :document_group,
        :chunk_type,
        :content,
        :content_sha256,
        :char_count,
        CAST(:heading_path AS jsonb),
        CAST(:source_block_ids AS jsonb),
        :source_table_id,
        :start_block_order,
        :end_block_order,
        :table_row_start,
        :table_row_end,
        CAST(:metadata AS jsonb)
    )
"""


def _json(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def _insert_batch(
    connection: Connection,
    chunks: list[MaterializedChunk],
    run_id: str,
) -> None:
    if not chunks:
        return
    rows = []
    for chunk in chunks:
        row = chunk.as_row(run_id)
        row["heading_path"] = _json(row["heading_path"])
        row["source_block_ids"] = _json(row["source_block_ids"])
        row["metadata"] = _json(row["metadata"])
        rows.append(row)
    connection.execute(text(INSERT_CHUNK_SQL), rows)


def _flush_if_full(
    connection: Connection,
    buffer: list[MaterializedChunk],
    run_id: str,
    batch_size: int,
) -> None:
    if len(buffer) < batch_size:
        return
    _insert_batch(connection, buffer, run_id)
    buffer.clear()


def _narrative_chunks(
    connection: Connection,
    plan: ApprovedChunkPlan,
    *,
    fetch_size: int,
    progress_every: int,
) -> Iterator[MaterializedChunk]:
    policy = ChunkPolicy(
        min_chars=int(plan.policy["narrative_min_chars"]),
        target_chars=int(plan.policy["narrative_target_chars"]),
        max_chars=int(plan.policy["narrative_max_chars"]),
        overlap_chars=int(plan.policy["narrative_overlap_chars"]),
    )
    planner = NarrativeChunkPlanner(policy)
    result = connection.execution_options(stream_results=True).execute(
        text(BLOCK_STREAM_SQL)
    ).mappings().yield_per(fetch_size)
    for index, row in enumerate(result, 1):
        source = SourceBlock(
            document_group=str(row["document_group"]),
            filing_id=str(row["filing_id"]),
            document_id=str(row["document_id"]),
            section_id=str(row["section_id"]) if row["section_id"] else None,
            section_title=row["section_title"],
            block_id=str(row["block_id"]),
            block_order=int(row["block_order"]),
            block_type=str(row["block_type"]),
            text=row["text_normalized"],
            heading_level=row["heading_level"],
        )
        for chunk in planner.push(source):
            yield narrative_chunk(chunk)
        if progress_every and index % progress_every == 0:
            print(f"  blocks read: {index}", flush=True)
    for chunk in planner.finish():
        yield narrative_chunk(chunk)


def _table_chunks(
    connection: Connection,
    *,
    fetch_size: int,
    progress_every: int,
    max_chars: int,
) -> Iterator[tuple[str, list[MaterializedChunk]]]:
    result = connection.execution_options(stream_results=True).execute(
        text(TABLE_STREAM_SQL)
    ).mappings().yield_per(fetch_size)
    for index, row in enumerate(result, 1):
        source = SourceTable(
            table_id=str(row["table_id"]),
            document_group=str(row["document_group"]),
            filing_id=str(row["filing_id"]),
            document_id=str(row["document_id"]),
            section_id=str(row["section_id"]) if row["section_id"] else None,
            section_title=row["section_title"],
            block_id=str(row["block_id"]),
            block_order=int(row["block_order"]),
            caption=row["caption_normalized"],
            normalized_text=str(row["normalized_text"]),
            grid=dict(row["grid"] or {}),
        )
        yield source.table_id, table_chunks(source, max_chars=max_chars)
        if progress_every and index % progress_every == 0:
            print(f"  tables read: {index}", flush=True)


def _latest_source_load(connection: Connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT load_run_id, manifest_sha256
            FROM public.load_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        )
    ).mappings().one()
    return dict(row)


def _validate_source_identity(
    connection: Connection,
    plan: ApprovedChunkPlan,
) -> None:
    load = _latest_source_load(connection)
    for field, expected in (
        ("load_run_id", plan.load_run_id),
        ("manifest_sha256", plan.manifest_sha256),
    ):
        actual = str(load[field])
        if actual != expected:
            raise RuntimeError(
                f"Source Layer {field} changed: expected={expected}, actual={actual}"
            )


def _create_run(
    connection: Connection,
    plan: ApprovedChunkPlan,
    run_id: str,
) -> None:
    existing = connection.execute(
        text(
            "SELECT status FROM public.retrieval_chunk_runs "
            "WHERE chunk_run_id = :run_id"
        ),
        {"run_id": run_id},
    ).scalar_one_or_none()
    if existing is not None:
        raise RuntimeError(
            f"Chunk run {run_id} already exists with status={existing}; "
            "verify it instead of overwriting it"
        )
    connection.execute(
        text(
            """
            INSERT INTO public.retrieval_chunk_runs (
                chunk_run_id,
                source_load_run_id,
                plan_version,
                plan_sha256,
                status,
                policy,
                counts,
                started_at,
                is_active
            ) VALUES (
                :run_id,
                :source_load_run_id,
                :plan_version,
                :plan_sha256,
                'loading',
                CAST(:policy AS jsonb),
                '{}'::jsonb,
                :started_at,
                false
            )
            """
        ),
        {
            "run_id": run_id,
            "source_load_run_id": plan.load_run_id,
            "plan_version": "4.0.0",
            "plan_sha256": plan.sha256,
            "policy": _json(plan.policy),
            "started_at": datetime.now(UTC),
        },
    )


def _complete_run(
    connection: Connection,
    run_id: str,
    counts: Counter[str],
) -> None:
    connection.execute(
        text("UPDATE public.retrieval_chunk_runs SET is_active = false WHERE is_active")
    )
    connection.execute(
        text(
            """
            UPDATE public.retrieval_chunk_runs
            SET status = 'completed',
                counts = CAST(:counts AS jsonb),
                completed_at = :completed_at,
                is_active = true
            WHERE chunk_run_id = :run_id
            """
        ),
        {
            "run_id": run_id,
            "counts": _json(dict(sorted(counts.items()))),
            "completed_at": datetime.now(UTC),
        },
    )


def _validate_materialized(
    connection: Connection,
    plan: ApprovedChunkPlan,
    run_id: str,
    counts: Counter[str],
) -> None:
    if counts["narrative_chunks"] != plan.narrative_chunks:
        raise RuntimeError(
            "Narrative chunk count changed: "
            f"plan={plan.narrative_chunks}, actual={counts['narrative_chunks']}"
        )
    if counts["vector_source_tables"] != plan.vector_source_tables:
        raise RuntimeError(
            "Vector table source count changed: "
            f"plan={plan.vector_source_tables}, "
            f"actual={counts['vector_source_tables']}"
        )
    checks = connection.execute(
        text(
            """
            SELECT
                count(*) FILTER (WHERE content = '')::bigint AS empty_chunks,
                max(char_count) FILTER (
                    WHERE chunk_type = 'narrative'
                )::bigint AS narrative_max,
                max(char_count) FILTER (
                    WHERE chunk_type = 'table'
                )::bigint AS table_max,
                count(DISTINCT source_table_id) FILTER (
                    WHERE chunk_type = 'table'
                )::bigint AS vector_source_tables
            FROM public.retrieval_chunks
            WHERE chunk_run_id = :run_id
            """
        ),
        {"run_id": run_id},
    ).mappings().one()
    expected_narrative_max = int(plan.policy["narrative_max_chars"])
    expected_table_max = int(plan.policy["table_max_chars"])
    if int(checks["empty_chunks"] or 0):
        raise RuntimeError("Empty retrieval chunks were materialized")
    if int(checks["narrative_max"] or 0) > expected_narrative_max:
        raise RuntimeError("Narrative chunk exceeds the approved maximum")
    if int(checks["table_max"] or 0) > expected_table_max:
        raise RuntimeError("Table chunk exceeds the approved maximum")
    if int(checks["vector_source_tables"] or 0) != plan.vector_source_tables:
        raise RuntimeError("Persisted vector table coverage differs from the plan")


def _load(
    database_url: str | None,
    plan: ApprovedChunkPlan,
    *,
    batch_size: int,
    fetch_size: int,
    progress_every: int,
) -> tuple[str, Counter[str]]:
    engine = get_engine(database_url)
    run_id = chunk_run_id(plan)
    counts: Counter[str] = Counter()
    buffer: list[MaterializedChunk] = []

    with engine.connect() as read_connection, read_connection.begin():
        read_connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        _validate_source_identity(read_connection, plan)

        with engine.begin() as write_connection:
            write_connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('retrieval_chunk_load'))")
            )
            _validate_source_identity(write_connection, plan)
            _create_run(write_connection, plan, run_id)

            print("[1/2] Materializing narrative chunks...", flush=True)
            for chunk in _narrative_chunks(
                read_connection,
                plan,
                fetch_size=fetch_size,
                progress_every=progress_every,
            ):
                buffer.append(chunk)
                counts["narrative_chunks"] += 1
                counts["narrative_chars"] += len(chunk.content)
                _flush_if_full(write_connection, buffer, run_id, batch_size)
            _insert_batch(write_connection, buffer, run_id)
            buffer.clear()

            print("[2/2] Materializing approved table chunks...", flush=True)
            for _, chunks in _table_chunks(
                read_connection,
                fetch_size=fetch_size,
                progress_every=max(progress_every // 10, 1),
                max_chars=int(plan.policy["table_max_chars"]),
            ):
                counts["vector_source_tables"] += 1
                for chunk in chunks:
                    buffer.append(chunk)
                    counts["table_chunks"] += 1
                    counts["table_chars"] += len(chunk.content)
                    _flush_if_full(write_connection, buffer, run_id, batch_size)
            _insert_batch(write_connection, buffer, run_id)
            buffer.clear()

            counts["total_chunks"] = (
                counts["narrative_chunks"] + counts["table_chunks"]
            )
            _validate_source_identity(write_connection, plan)
            _validate_materialized(write_connection, plan, run_id, counts)
            _complete_run(write_connection, run_id, counts)
    return run_id, counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--fetch-size", type=int, default=5_000)
    parser.add_argument("--progress-every", type=int, default=100_000)
    args = parser.parse_args()
    if args.batch_size <= 0 or args.fetch_size <= 0:
        parser.error("--batch-size and --fetch-size must be positive")

    plan = load_approved_plan(args.plan)
    print("=== retrieval chunk load contract ===")
    print(f"plan version                    4.0.0")
    print(f"source load run                 {plan.load_run_id}")
    print(f"planned narrative chunks        {plan.narrative_chunks}")
    print(f"approved vector source tables   {plan.vector_source_tables}")
    print(f"estimated table chunks          {plan.estimated_table_chunks}")
    print("database target                 configured URL")

    run_id, counts = _load(
        args.database_url,
        plan,
        batch_size=args.batch_size,
        fetch_size=args.fetch_size,
        progress_every=args.progress_every,
    )
    print("\n=== retrieval chunk load ===")
    print(f"chunk run                       {run_id}")
    for key in (
        "narrative_chunks",
        "table_chunks",
        "total_chunks",
        "vector_source_tables",
    ):
        print(f"{key:32} {counts[key]}")
    print("status                          completed and active")


if __name__ == "__main__":
    main()
