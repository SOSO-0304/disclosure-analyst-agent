#!/usr/bin/env python3
"""Verify the active retrieval chunk run against its approved v4 plan."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sqlalchemy import text

from disclosure_agent.retrieval.chunk_ingestion import load_approved_plan
from disclosure_agent.storage.database import get_engine


def _status(ok: bool) -> str:
    return "OK" if ok else "MISMATCH"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()

    plan = load_approved_plan(args.plan)
    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        active_runs = connection.execute(
            text("SELECT count(*) FROM retrieval_chunk_runs WHERE is_active")
        ).scalar_one()
        run = connection.execute(
            text(
                """
                SELECT *
                FROM retrieval_chunk_runs
                WHERE is_active
                """
            )
        ).mappings().one_or_none()
        if run is None:
            raise SystemExit("No active retrieval chunk run")
        run_id = str(run["chunk_run_id"])
        metrics = dict(
            connection.execute(
                text(
                    """
                    SELECT
                        count(*)::bigint AS total_chunks,
                        count(*) FILTER (
                            WHERE chunk_type = 'narrative'
                        )::bigint AS narrative_chunks,
                        count(*) FILTER (
                            WHERE chunk_type = 'table'
                        )::bigint AS table_chunks,
                        count(DISTINCT source_table_id) FILTER (
                            WHERE chunk_type = 'table'
                        )::bigint AS vector_source_tables,
                        count(*) FILTER (
                            WHERE content = '' OR char_count <> char_length(content)
                        )::bigint AS invalid_content,
                        count(*) FILTER (
                            WHERE jsonb_array_length(source_block_ids) = 0
                        )::bigint AS missing_block_provenance,
                        count(*) FILTER (
                            WHERE chunk_type = 'table' AND source_table_id IS NULL
                        )::bigint AS missing_table_provenance,
                        max(char_count) FILTER (
                            WHERE chunk_type = 'narrative'
                        )::bigint AS narrative_max_chars,
                        max(char_count) FILTER (
                            WHERE chunk_type = 'table'
                        )::bigint AS table_max_chars
                    FROM retrieval_chunks
                    WHERE chunk_run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            ).mappings().one()
        )

    stored_counts: dict[str, Any] = dict(run["counts"] or {})
    checks = {
        "one active run": int(active_runs) == 1,
        "run completed": run["status"] == "completed",
        "source load identity": str(run["source_load_run_id"]) == plan.load_run_id,
        "plan sha256": str(run["plan_sha256"]) == plan.sha256,
        "narrative count": int(metrics["narrative_chunks"]) == plan.narrative_chunks,
        "vector table coverage": int(metrics["vector_source_tables"])
        == plan.vector_source_tables,
        "stored total count": int(metrics["total_chunks"])
        == int(stored_counts.get("total_chunks", -1)),
        "content integrity": int(metrics["invalid_content"]) == 0,
        "block provenance": int(metrics["missing_block_provenance"]) == 0,
        "table provenance": int(metrics["missing_table_provenance"]) == 0,
        "narrative maximum": int(metrics["narrative_max_chars"] or 0)
        <= int(plan.policy["narrative_max_chars"]),
        "table maximum": int(metrics["table_max_chars"] or 0)
        <= int(plan.policy["table_max_chars"]),
    }

    print("=== retrieval chunk database verification ===")
    print(f"chunk run                     {run_id}")
    print(f"narrative chunks              {metrics['narrative_chunks']}")
    print(f"table chunks                  {metrics['table_chunks']}")
    print(f"total chunks                  {metrics['total_chunks']}")
    print(f"vector source tables          {metrics['vector_source_tables']}")
    print(f"narrative maximum chars       {metrics['narrative_max_chars']}")
    print(f"table maximum chars           {metrics['table_max_chars']}")
    print()
    for label, ok in checks.items():
        print(f"{label:30} {_status(ok)}")
    if not all(checks.values()):
        raise SystemExit(1)
    print("status                         verified")


if __name__ == "__main__":
    main()
