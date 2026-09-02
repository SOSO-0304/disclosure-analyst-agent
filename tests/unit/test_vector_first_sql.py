"""Portable SQL semantics, not a PostgreSQL planner or ANN recall benchmark.

The fixture stores precomputed distances in the embedding column; only pgvector's
distance expression and PostgreSQL's regexp operator are translated for SQLite.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from disclosure_agent.retrieval.search import dense_sql, scope_sql

RUN = {"embedding_run_id": "v2", "chunk_run_id": "chunks"}


@pytest.fixture
def database():
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "regexp", 2, lambda pattern, value: bool(re.search(pattern, value))
        )
        connection.executescript("""
            ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.retrieval_embeddings (
                chunk_id TEXT, chunk_run_id TEXT, embedding_run_id TEXT,
                chunk_content_sha256 TEXT, embedding REAL
            );
            CREATE TABLE public.retrieval_chunks (
                chunk_id TEXT, chunk_run_id TEXT, filing_id TEXT, content TEXT,
                content_sha256 TEXT, document_group TEXT, chunk_type TEXT
            );
            CREATE TABLE public.source_filings (
                filing_id TEXT, corp_code TEXT, receipt_date TEXT, is_correction BOOLEAN
            );
        """)
        yield connection


def add(
    connection,
    name,
    distance,
    *,
    content="계약금액",
    stale=False,
    run="v2",
    chunk_run="chunks",
    corp="001",
    receipt="2025-01-01",
    correction=False,
    group="exchange",
    kind="table",
):
    filing = f"{name}-{chunk_run}-{run}"
    connection.execute(
        "INSERT INTO public.retrieval_embeddings VALUES (?, ?, ?, ?, ?)",
        (name, chunk_run, run, "stale" if stale else "hash", distance),
    )
    connection.execute(
        "INSERT INTO public.retrieval_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, chunk_run, filing, content, "hash", group, kind),
    )
    connection.execute(
        "INSERT INTO public.source_filings VALUES (?, ?, ?, ?)", (filing, corp, receipt, correction)
    )


def search(connection, *, pool=200, limit=100, **filters):
    where, params = scope_sql(RUN, **filters)
    sql = dense_sql(where.replace("c.content ~", "c.content REGEXP"), exact=False)
    sql = sql.replace("e.embedding <=> CAST(:vector AS vector)", "e.embedding")
    params.update(pool_limit=pool, candidate_limit=limit)
    return [dict(row) for row in connection.execute(sql, params)]


def test_vector_cte_has_distance_limit_before_any_join_and_postvalidates_hash():
    where, _ = scope_sql(RUN)
    sql = dense_sql(where, exact=False)
    pool, validated = sql.split("FROM vector_candidates e")
    assert "JOIN" not in pool
    assert "ORDER BY e.embedding <=> CAST(:vector AS vector)" in pool
    assert "LIMIT :pool_limit" in pool
    assert "e.embedding_run_id = :run_id AND e.chunk_run_id = :chunk_run_id" in pool
    assert "e.chunk_content_sha256 = c.content_sha256" in validated
    assert "LIMIT :candidate_limit" in validated


def test_invalid_nearest_rows_do_not_leak_and_larger_pool_restores_candidates(database):
    add(database, "stale", 0.01, stale=True)
    add(database, "punctuation", 0.02, content=" - / ")
    add(database, "near", 0.03)
    add(database, "far", 0.04)
    assert search(database, pool=2) == []
    rows = search(database, pool=4)
    assert [row["chunk_id"] for row in rows] == ["near", "far"]
    assert [row["similarity"] for row in rows] == pytest.approx([0.97, 0.96])
    assert [row["chunk_id"] for row in search(database, pool=4, limit=1)] == ["near"]


def test_other_embedding_and_chunk_runs_cannot_consume_pool_or_mix_chunks(database):
    add(database, "other-v", 0.01, run="v1")
    add(database, "same", 0.02, chunk_run="old-chunks", content="---")
    add(database, "same", 0.03)
    assert search(database, pool=1) == [{"chunk_id": "same", "similarity": 0.97}]


def test_postvalidation_preserves_all_company_date_document_correction_filters(database):
    add(database, "wanted", 0.1, correction=True)
    add(database, "other-company", 0.01, corp="002", correction=True)
    add(database, "old", 0.02, receipt="2024-12-31", correction=True)
    add(database, "new", 0.03, receipt="2026-01-01", correction=True)
    add(database, "original", 0.04)
    add(database, "other-group", 0.05, group="holding", correction=True)
    add(database, "other-kind", 0.06, kind="narrative", correction=True)
    rows = search(
        database,
        corp_code="001",
        date_from="2025-01-01",
        date_to="2025-12-31",
        document_group="exchange",
        chunk_type="table",
        corrections="only",
    )
    assert [row["chunk_id"] for row in rows] == ["wanted"]
