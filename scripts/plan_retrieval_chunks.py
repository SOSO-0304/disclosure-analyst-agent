#!/usr/bin/env python3
"""Dry-run retrieval chunk planning against the promoted Source Layer."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import Connection, text

from disclosure_agent.retrieval.chunk_planner import (
    ChunkPolicy,
    NarrativeChunkPlanner,
    PlannedNarrativeChunk,
    SourceBlock,
)
from disclosure_agent.storage.database import get_engine

DEFAULT_OUTPUT = Path("data/quality/retrieval-chunk-plan.json")

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

BLOCK_COUNTS_SQL = """
    SELECT block_type, count(*)::bigint AS count
    FROM public.source_blocks
    GROUP BY block_type
    ORDER BY block_type
"""

TABLE_BUCKETS_SQL = """
    WITH classified AS (
        SELECT
            f.document_group,
            char_length(t.normalized_text) AS text_length,
            t.row_count::bigint * t.column_count::bigint AS cell_slots,
            CASE
                WHEN t.normalized_text = '' THEN 'exclude_empty'
                WHEN f.document_group = 'exchange'
                     AND char_length(t.normalized_text) > :table_max_chars
                    THEN 'exchange_row_window'
                WHEN f.document_group = 'exchange' THEN 'exchange_direct'
                WHEN t.parent_table_id IS NOT NULL THEN 'nested_review'
                WHEN f.document_group = 'periodic'
                     AND t.row_count >= 2
                     AND t.column_count >= 2
                     AND char_length(t.normalized_text) >= 100
                     AND t.normalized_text ~ '[0-9]'
                    THEN 'periodic_numeric_structured'
                WHEN f.document_group = 'periodic'
                    THEN 'periodic_lexical'
                WHEN char_length(t.normalized_text) > :table_max_chars
                    THEN 'row_window_candidate'
                WHEN char_length(t.normalized_text) <= 50
                     AND t.row_count::bigint * t.column_count::bigint <= 4
                    THEN 'small_layout_review'
                ELSE 'direct_candidate'
            END AS decision_bucket
        FROM public.source_tables t
        JOIN public.source_filings f USING (filing_id)
    )
    SELECT
        document_group,
        decision_bucket,
        count(*)::bigint AS tables,
        sum(text_length)::bigint AS total_chars,
        round(avg(text_length))::bigint AS avg_chars,
        max(text_length)::bigint AS max_chars,
        sum(
            CASE
                WHEN decision_bucket IN ('exchange_row_window', 'row_window_candidate')
                    THEN greatest(
                        1,
                        ceil(text_length::numeric / :table_max_chars)::bigint
                    )
                WHEN decision_bucket IN (
                    'exchange_direct',
                    'direct_candidate'
                ) THEN 1
                ELSE 0
            END
        )::bigint AS initial_chunk_estimate
    FROM classified
    GROUP BY document_group, decision_bucket
    ORDER BY document_group, decision_bucket
"""


def _length_bucket(length: int) -> str:
    if length <= 200:
        return "1_200"
    if length <= 500:
        return "201_500"
    if length <= 1_000:
        return "501_1000"
    if length <= 1_200:
        return "1001_1200"
    return "over_1200"


def _rows(connection: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def _record_chunk(
    chunk: PlannedNarrativeChunk,
    *,
    by_group: Counter[str],
    length_buckets: Counter[str],
    totals: Counter[str],
) -> None:
    length = len(chunk.text)
    totals["narrative_chunks"] += 1
    totals["narrative_chars"] += length
    totals["narrative_block_references"] += len(chunk.block_ids)
    totals["narrative_max_chars"] = max(totals["narrative_max_chars"], length)
    by_group[chunk.document_group] += 1
    length_buckets[_length_bucket(length)] += 1


def _narrative_summary(
    *,
    totals: Counter[str],
    by_group: Counter[str],
    length_buckets: Counter[str],
) -> dict[str, Any]:
    chunks = totals["narrative_chunks"]
    return {
        "chunks": chunks,
        "chars": totals["narrative_chars"],
        "avg_chars": round(totals["narrative_chars"] / chunks) if chunks else 0,
        "max_chars": totals["narrative_max_chars"],
        "block_references": totals["narrative_block_references"],
        "paragraph_blocks_seen": totals["paragraph_blocks_seen"],
        "paragraph_blocks_with_text": totals["paragraph_blocks_with_text"],
        "headings_used_as_metadata": totals["headings_used_as_metadata"],
        "unknown_blocks_deferred": totals["unknown_blocks_deferred"],
        "by_document_group": dict(sorted(by_group.items())),
        "length_buckets": dict(sorted(length_buckets.items())),
    }


def _reuse_narrative(path: Path, load: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    previous = orjson.loads(path.read_bytes())
    previous_load = previous.get("load", {})
    for key in ("load_run_id", "manifest_sha256"):
        if previous_load.get(key) != load.get(key):
            raise ValueError(
                f"Narrative plan {key} mismatch: "
                f"current={load.get(key)!r}, previous={previous_load.get(key)!r}"
            )
    narrative = previous.get("narrative")
    if not isinstance(narrative, dict) or not narrative.get("chunks"):
        raise ValueError("Narrative plan does not contain an approved narrative summary")
    streamed = previous.get("streamed_block_counts", {})
    if not isinstance(streamed, dict):
        streamed = {}
    return narrative, {str(key): int(value) for key, value in streamed.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-chars", type=int, default=1_000)
    parser.add_argument("--min-chars", type=int, default=400)
    parser.add_argument("--max-chars", type=int, default=1_200)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument("--table-max-chars", type=int, default=2_000)
    parser.add_argument("--fetch-size", type=int, default=5_000)
    parser.add_argument("--progress-every", type=int, default=100_000)
    parser.add_argument("--tables-only", action="store_true")
    parser.add_argument("--reuse-narrative-plan", type=Path)
    args = parser.parse_args()

    policy = ChunkPolicy(
        min_chars=args.min_chars,
        target_chars=args.target_chars,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )
    if args.table_max_chars <= 0:
        parser.error("--table-max-chars must be positive")
    if args.fetch_size <= 0:
        parser.error("--fetch-size must be positive")
    if args.tables_only and args.reuse_narrative_plan is None:
        parser.error("--tables-only requires --reuse-narrative-plan")

    engine = get_engine(args.database_url)
    planner = NarrativeChunkPlanner(policy)
    totals: Counter[str] = Counter()
    by_group: Counter[str] = Counter()
    length_buckets: Counter[str] = Counter()
    source_types: Counter[str] = Counter()

    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        print("[1/3] Reading accepted load metadata...", flush=True)
        load = dict(
            connection.execute(
                text(
                    """
                    SELECT load_run_id, manifest_sha256, counts
                    FROM public.load_runs
                    WHERE status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """
                )
            )
            .mappings()
            .one()
        )
        block_counts = _rows(connection, BLOCK_COUNTS_SQL)
        print("[2/3] Classifying table candidates...", flush=True)
        table_buckets = _rows(
            connection,
            TABLE_BUCKETS_SQL,
            table_max_chars=args.table_max_chars,
        )

        if args.tables_only:
            print("[3/3] Reusing approved narrative plan...", flush=True)
            narrative, reused_source_types = _reuse_narrative(args.reuse_narrative_plan, load)
            source_types.update(reused_source_types)
            narrative_source = str(args.reuse_narrative_plan)
        else:
            print("[3/3] Streaming ordered blocks for narrative planning...", flush=True)
            result = (
                connection.execution_options(stream_results=True)
                .execute(text(BLOCK_STREAM_SQL))
                .mappings()
                .yield_per(args.fetch_size)
            )
            for index, row in enumerate(result, 1):
                block_type = str(row["block_type"])
                source_types[block_type] += 1
                if block_type == "paragraph":
                    totals["paragraph_blocks_seen"] += 1
                    if (row["text_normalized"] or "").strip():
                        totals["paragraph_blocks_with_text"] += 1
                elif block_type == "heading":
                    totals["headings_used_as_metadata"] += 1
                elif block_type == "unknown":
                    totals["unknown_blocks_deferred"] += 1
                elif block_type == "table":
                    totals["table_boundaries_seen"] += 1

                source = SourceBlock(
                    document_group=str(row["document_group"]),
                    filing_id=str(row["filing_id"]),
                    document_id=str(row["document_id"]),
                    section_id=str(row["section_id"]) if row["section_id"] else None,
                    section_title=row["section_title"],
                    block_id=str(row["block_id"]),
                    block_order=int(row["block_order"]),
                    block_type=block_type,
                    text=row["text_normalized"],
                    heading_level=row["heading_level"],
                )
                for chunk in planner.push(source):
                    _record_chunk(
                        chunk,
                        by_group=by_group,
                        length_buckets=length_buckets,
                        totals=totals,
                    )
                if args.progress_every and index % args.progress_every == 0:
                    print(
                        f"[{index}] narrative_chunks={totals['narrative_chunks']}",
                        flush=True,
                    )
            for chunk in planner.finish():
                _record_chunk(
                    chunk,
                    by_group=by_group,
                    length_buckets=length_buckets,
                    totals=totals,
                )
            narrative = _narrative_summary(
                totals=totals,
                by_group=by_group,
                length_buckets=length_buckets,
            )
            narrative_source = "computed"

    table_chunk_estimate = sum(int(row["initial_chunk_estimate"] or 0) for row in table_buckets)
    plan = {
        "plan_version": "4.0.0",
        "mode": "read_only_table_dry_run" if args.tables_only else "read_only_dry_run",
        "load": load,
        "policy": {
            "narrative_target_chars": policy.target_chars,
            "narrative_min_chars": policy.min_chars,
            "narrative_max_chars": policy.max_chars,
            "narrative_overlap_chars": policy.overlap_chars,
            "table_max_chars": args.table_max_chars,
            "headings": "adaptive_context_with_minimum_chunk_boundary",
            "page_breaks": "excluded",
            "unknown": "deferred_for_review",
            "tables": "vector_structured_lexical_lanes_not_persisted",
            "narrative_source": narrative_source,
        },
        "source_block_counts": block_counts,
        "streamed_block_counts": dict(sorted(source_types.items())),
        "narrative": narrative,
        "tables": {
            "buckets": table_buckets,
            "initial_chunk_estimate_excluding_review_buckets": table_chunk_estimate,
            "warning": (
                "Only Exchange, holding, and major vector buckets contribute to "
                "the vector estimate. Periodic numeric tables use the structured "
                "lane; other periodic tables use the lexical lane. All source "
                "tables remain preserved."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        orjson.dumps(plan, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )

    print("\n=== retrieval chunk dry run ===")
    print(f"narrative chunks              {narrative['chunks']}")
    print(f"narrative average chars       {plan['narrative']['avg_chars']}")
    print(f"narrative maximum chars       {narrative['max_chars']}")
    print(f"table initial estimate        {table_chunk_estimate}")
    print("database writes               0")
    print(f"PLAN                          {args.output}")


if __name__ == "__main__":
    main()
