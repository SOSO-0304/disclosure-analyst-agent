#!/usr/bin/env python3
"""Verify complete pgvector coverage for the active retrieval chunk run."""

from __future__ import annotations

import argparse

from sqlalchemy import text

from disclosure_agent.storage.database import get_engine


def _status(ok: bool) -> str:
    return "OK" if ok else "MISMATCH"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        active_count = int(
            connection.execute(
                text("SELECT count(*) FROM embedding_runs WHERE is_active")
            ).scalar_one()
        )
        run = connection.execute(
            text("SELECT * FROM embedding_runs WHERE is_active")
        ).mappings().one_or_none()
        if run is None:
            raise SystemExit("No active completed embedding run")
        run_id = str(run["embedding_run_id"])
        metrics = dict(
            connection.execute(
                text(
                    """
                    SELECT
                        count(*)::bigint AS embeddings,
                        count(DISTINCT e.chunk_id)::bigint AS distinct_chunks,
                        count(*) FILTER (
                            WHERE e.chunk_content_sha256 <> c.content_sha256
                        )::bigint AS stale_content,
                        count(*) FILTER (
                            WHERE vector_dims(e.embedding) <> r.dimensions
                        )::bigint AS wrong_dimensions,
                        count(*) FILTER (
                            WHERE e.input_sha256 = ''
                        )::bigint AS missing_input_hash,
                        count(*) FILTER (
                            WHERE e.input_tokens IS NULL OR e.input_tokens <= 0
                        )::bigint AS missing_tokens,
                        coalesce(sum(e.input_tokens), 0)::bigint AS input_tokens,
                        (
                            SELECT count(*)::bigint
                            FROM retrieval_chunks
                            WHERE chunk_run_id = r.chunk_run_id
                        ) AS expected_chunks
                    FROM retrieval_embeddings e
                    JOIN retrieval_chunks c
                      ON c.chunk_run_id = e.chunk_run_id
                     AND c.chunk_id = e.chunk_id
                    JOIN embedding_runs r
                      ON r.embedding_run_id = e.embedding_run_id
                    WHERE e.embedding_run_id = :run_id
                    GROUP BY r.chunk_run_id
                    """
                ),
                {"run_id": run_id},
            ).mappings().one()
        )
        index = connection.execute(
            text(
                """
                SELECT i.indisvalid, i.indisready
                FROM pg_class c
                JOIN pg_index i ON i.indexrelid = c.oid
                WHERE c.relname = 'ix_retrieval_embeddings_hnsw_cosine'
                """
            )
        ).mappings().one_or_none()

    stored_counts = dict(run["counts"] or {})
    checks = {
        "one active run": active_count == 1,
        "run completed": run["status"] == "completed",
        "model": run["model"] == "bge-m3",
        "dimensions": int(run["dimensions"]) == 1024,
        "distance metric": run["distance_metric"] == "cosine",
        "complete coverage": int(metrics["embeddings"])
        == int(metrics["expected_chunks"]),
        "distinct chunks": int(metrics["distinct_chunks"])
        == int(metrics["expected_chunks"]),
        "stored count": int(stored_counts.get("embedded_chunks", -1))
        == int(metrics["embeddings"]),
        "content hashes": int(metrics["stale_content"]) == 0,
        "vector dimensions": int(metrics["wrong_dimensions"]) == 0,
        "input hashes": int(metrics["missing_input_hash"]) == 0,
        "token accounting": int(metrics["missing_tokens"]) == 0,
        "HNSW index": bool(index and index["indisvalid"] and index["indisready"]),
    }

    print("=== retrieval embedding database verification ===")
    print(f"embedding run                 {run_id}")
    print(f"chunk run                     {run['chunk_run_id']}")
    print(f"model                         {run['model']}")
    print(f"dimensions                    {run['dimensions']}")
    print(f"embeddings                    {metrics['embeddings']}")
    print(f"expected chunks               {metrics['expected_chunks']}")
    print(f"input tokens                  {metrics['input_tokens']}")
    print()
    for label, ok in checks.items():
        print(f"{label:30} {_status(ok)}")
    if not all(checks.values()):
        raise SystemExit(1)
    print("status                         verified")


if __name__ == "__main__":
    main()
