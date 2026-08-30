#!/usr/bin/env python3
"""Profile promoted source content before defining retrieval chunks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import Connection, text

from disclosure_agent.storage.database import get_engine

DEFAULT_OUTPUT = Path("data/quality/source-content-profile.json")

BLOCK_TYPES_SQL = """
    SELECT block_type, count(*)::bigint AS count
    FROM public.source_blocks
    GROUP BY block_type
    ORDER BY count DESC, block_type
"""

BLOCK_TYPES_BY_GROUP_SQL = """
    SELECT
        f.document_group,
        b.block_type,
        count(*)::bigint AS count
    FROM public.source_blocks b
    JOIN public.source_filings f USING (filing_id)
    GROUP BY f.document_group, b.block_type
    ORDER BY f.document_group, count DESC, b.block_type
"""

TEXT_LENGTHS_SQL = """
    WITH content_lengths AS (
        SELECT
            block_type AS content_type,
            char_length(COALESCE(text_normalized, '')) AS text_length
        FROM public.source_blocks
        WHERE block_type IN ('paragraph', 'heading', 'unknown')

        UNION ALL

        SELECT
            'table' AS content_type,
            char_length(normalized_text) AS text_length
        FROM public.source_tables
    )
    SELECT
        content_type,
        count(*)::bigint AS total,
        count(*) FILTER (WHERE text_length = 0)::bigint AS empty,
        count(*) FILTER (WHERE text_length BETWEEN 1 AND 50)::bigint AS chars_1_50,
        count(*) FILTER (WHERE text_length BETWEEN 51 AND 200)::bigint AS chars_51_200,
        count(*) FILTER (WHERE text_length BETWEEN 201 AND 500)::bigint AS chars_201_500,
        count(*) FILTER (WHERE text_length BETWEEN 501 AND 1000)::bigint AS chars_501_1000,
        count(*) FILTER (WHERE text_length BETWEEN 1001 AND 2000)::bigint AS chars_1001_2000,
        count(*) FILTER (WHERE text_length > 2000)::bigint AS chars_over_2000,
        round(avg(text_length))::bigint AS avg_chars,
        max(text_length)::bigint AS max_chars
    FROM content_lengths
    GROUP BY content_type
    ORDER BY content_type
"""

TABLE_SHAPES_SQL = """
    WITH table_shapes AS (
        SELECT
            row_count,
            column_count,
            row_count::bigint * column_count::bigint AS cell_slots,
            parent_table_id,
            normalized_text
        FROM public.source_tables
    )
    SELECT
        count(*)::bigint AS total,
        count(*) FILTER (
            WHERE row_count = 0 OR column_count = 0
        )::bigint AS empty_dimensions,
        count(*) FILTER (WHERE normalized_text = '')::bigint AS empty_text,
        count(*) FILTER (WHERE parent_table_id IS NOT NULL)::bigint AS nested,
        count(*) FILTER (WHERE cell_slots BETWEEN 1 AND 4)::bigint AS cells_1_4,
        count(*) FILTER (WHERE cell_slots BETWEEN 5 AND 20)::bigint AS cells_5_20,
        count(*) FILTER (WHERE cell_slots BETWEEN 21 AND 100)::bigint AS cells_21_100,
        count(*) FILTER (WHERE cell_slots BETWEEN 101 AND 500)::bigint AS cells_101_500,
        count(*) FILTER (WHERE cell_slots > 500)::bigint AS cells_over_500,
        round(avg(row_count))::bigint AS avg_rows,
        round(avg(column_count))::bigint AS avg_columns,
        max(row_count)::bigint AS max_rows,
        max(column_count)::bigint AS max_columns,
        max(cell_slots)::bigint AS max_cell_slots
    FROM table_shapes
"""

SECTION_COVERAGE_SQL = """
    SELECT
        count(*) FILTER (WHERE block_type = 'paragraph')::bigint AS paragraphs,
        count(*) FILTER (
            WHERE block_type = 'paragraph' AND section_id IS NULL
        )::bigint AS paragraphs_without_section,
        count(DISTINCT section_id) FILTER (
            WHERE block_type = 'paragraph'
        )::bigint AS sections_with_paragraphs,
        count(DISTINCT section_id) FILTER (
            WHERE block_type = 'table'
        )::bigint AS sections_with_tables
    FROM public.source_blocks
"""


def _rows(connection: Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql)).mappings()]


def _one(connection: Connection, sql: str) -> dict[str, Any]:
    return dict(connection.execute(text(sql)).mappings().one())


def _print_rows(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    for row in rows:
        print(orjson.dumps(row, option=orjson.OPT_SORT_KEYS).decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))

        print("[1/5] Reading accepted load metadata...", flush=True)
        load = _one(
            connection,
            """
            SELECT load_run_id, manifest_sha256, counts
            FROM public.load_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
        )
        database = _one(
            connection,
            """
            SELECT
                pg_database_size(current_database())::bigint AS size_bytes,
                pg_size_pretty(pg_database_size(current_database())) AS size_pretty
            """,
        )

        print("[2/5] Counting block types...", flush=True)
        block_types = _rows(connection, BLOCK_TYPES_SQL)
        block_types_by_group = _rows(connection, BLOCK_TYPES_BY_GROUP_SQL)

        print("[3/5] Bucketing normalized text lengths...", flush=True)
        text_lengths = _rows(connection, TEXT_LENGTHS_SQL)

        print("[4/5] Profiling table shapes...", flush=True)
        table_shapes = _one(connection, TABLE_SHAPES_SQL)

        print("[5/5] Measuring section coverage...", flush=True)
        section_coverage = _one(connection, SECTION_COVERAGE_SQL)

    profile = {
        "profile_version": "1.0.0",
        "load": load,
        "database": database,
        "block_types": block_types,
        "block_types_by_group": block_types_by_group,
        "text_lengths": text_lengths,
        "table_shapes": table_shapes,
        "section_coverage": section_coverage,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        orjson.dumps(profile, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )

    _print_rows("BLOCK TYPES", block_types)
    _print_rows("BLOCK TYPES BY GROUP", block_types_by_group)
    _print_rows("TEXT LENGTHS", text_lengths)
    _print_rows("TABLE SHAPES", [table_shapes])
    _print_rows("SECTION COVERAGE", [section_coverage])
    print(f"\nPROFILE: {args.output}")


if __name__ == "__main__":
    main()
