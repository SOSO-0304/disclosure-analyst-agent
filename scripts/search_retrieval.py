#!/usr/bin/env python3
"""Embed one query and run a provenance-rich pgvector cosine search."""

from __future__ import annotations

import argparse
import os
from typing import Any

import orjson
from sqlalchemy import text

from disclosure_agent.retrieval.embeddings import (
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    vector_literal,
)
from disclosure_agent.storage.database import get_engine


def _embedding_run(connection: Any, requested: str | None) -> dict[str, Any]:
    if requested:
        row = connection.execute(
            text("SELECT * FROM embedding_runs WHERE embedding_run_id = :run_id"),
            {"run_id": requested},
        ).mappings().one_or_none()
    else:
        row = connection.execute(
            text("SELECT * FROM embedding_runs WHERE is_active AND status = 'completed'")
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("Requested or active embedding run was not found")
    return dict(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument("--embedding-run-id")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--corp-code")
    parser.add_argument(
        "--document-group",
        choices=("exchange", "holding", "major", "periodic"),
    )
    parser.add_argument("--chunk-type", choices=("narrative", "table"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.top_k <= 100:
        parser.error("--top-k must be between 1 and 100")

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"Environment variable {args.api_key_env} is missing")
    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        run = _embedding_run(connection, args.embedding_run_id)

    config = EmbeddingConfig(
        provider=str(run["provider"]),
        model=str(run["model"]),
        dimensions=int(run["dimensions"]),
        distance_metric=str(run["distance_metric"]),
        endpoint=str(run["endpoint"]),
        input_version=str(run["input_version"]),
    )
    with ClovaStudioEmbeddingClient(api_key, config) as client:
        query_embedding = client.embed(args.query)
    query_vector = vector_literal(query_embedding.vector)

    conditions = ["e.embedding_run_id = :embedding_run_id"]
    params: dict[str, Any] = {
        "embedding_run_id": run["embedding_run_id"],
        "query_vector": query_vector,
        "top_k": args.top_k,
    }
    if args.corp_code:
        conditions.append("f.corp_code = :corp_code")
        params["corp_code"] = args.corp_code
    if args.document_group:
        conditions.append("c.document_group = :document_group")
        params["document_group"] = args.document_group
    if args.chunk_type:
        conditions.append("c.chunk_type = :chunk_type")
        params["chunk_type"] = args.chunk_type

    sql = f"""
        SELECT
            c.chunk_id,
            c.chunk_type,
            c.filing_id,
            c.document_id,
            c.section_id,
            c.heading_path,
            c.source_block_ids,
            c.source_table_id,
            c.content,
            f.corp_code,
            f.report_name,
            f.receipt_number,
            1 - (e.embedding <=> CAST(:query_vector AS vector)) AS similarity
        FROM retrieval_embeddings e
        JOIN retrieval_chunks c
          ON c.chunk_run_id = e.chunk_run_id
         AND c.chunk_id = e.chunk_id
        JOIN source_filings f ON f.filing_id = c.filing_id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.embedding <=> CAST(:query_vector AS vector)
        LIMIT :top_k
    """
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL hnsw.ef_search = 100"))
        rows = [dict(row) for row in connection.execute(text(sql), params).mappings()]

    if args.json:
        print(orjson.dumps(rows, option=orjson.OPT_INDENT_2).decode())
        return
    print("=== semantic retrieval smoke test ===")
    print(f"query tokens                  {query_embedding.input_tokens}")
    print(f"embedding run                {run['embedding_run_id']}")
    print(f"results                      {len(rows)}")
    for index, row in enumerate(rows, 1):
        print()
        print(
            f"{index}. similarity={float(row['similarity']):.4f} "
            f"corp={row['corp_code']} type={row['chunk_type']}"
        )
        print(f"   report   : {row['report_name']} ({row['receipt_number']})")
        print(f"   chunk    : {row['chunk_id']}")
        print(f"   section  : {row['section_id']}")
        print(f"   table    : {row['source_table_id']}")
        preview = str(row["content"]).replace("\n", " ")[:300]
        print(f"   content  : {preview}")


if __name__ == "__main__":
    main()
