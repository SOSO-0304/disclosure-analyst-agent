"""Execute the lexical CTE with portable in-memory SQL, not a production database.

SQLite's strpos/md5/regexp shims cover SQL ranking semantics only. These tests do
not validate PostgreSQL's query planner, pgvector, or production latency.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

import pytest

from disclosure_agent.retrieval.search import lexical_sql, scope_sql

RUN = {"embedding_run_id": "v2", "chunk_run_id": "chunks"}


@pytest.fixture
def database():
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function("strpos", 2, lambda value, term: value.find(term) + 1)
        connection.create_function(
            "md5", 1, lambda value: hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()
        )
        connection.create_function(
            "regexp", 2, lambda pattern, value: bool(re.search(pattern, value))
        )
        connection.executescript("""
            ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.retrieval_embeddings (
                chunk_id TEXT, chunk_run_id TEXT, embedding_run_id TEXT, chunk_content_sha256 TEXT
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


def add(connection, name, content, *, corp="001", receipt="2025-01-01", stale=False):
    connection.execute(
        "INSERT INTO public.retrieval_embeddings VALUES (?, 'chunks', 'v2', ?)",
        (name, "stale" if stale else "hash"),
    )
    connection.execute(
        "INSERT INTO public.retrieval_chunks "
        "VALUES (?, 'chunks', ?, ?, 'hash', 'exchange', 'table')",
        (name, name, content),
    )
    connection.execute(
        "INSERT INTO public.source_filings VALUES (?, ?, ?, false)", (name, corp, receipt)
    )


def search(connection, *, limit=100, terms=("계약금액", "계약기간"), **filters):
    where, params = scope_sql(RUN, **filters)
    # Translate only PostgreSQL's regex operator; the production lexical CTE is unchanged.
    sql = lexical_sql(where.replace("c.content ~", "c.content REGEXP"), terms)
    params.update(candidate_limit=limit, **{f"term_{i}": term for i, term in enumerate(terms)})
    return [dict(row) for row in connection.execute(sql, params)]


def test_midrank_is_bounded_to_returned_candidate_window(database):
    for i in range(6):
        add(database, f"exchange_20250{i + 1}01", "계약금액 100 계약기간 1년")
    rows = search(database, limit=2)
    assert len(rows) == 2
    assert {r["lexical_rank"] for r in rows} == {1.5}
    assert {r["lexical_tie_count"] for r in rows} == {2}


def test_547_way_corpus_tie_does_not_assign_rank_274_to_100_candidates(database):
    for i in range(547):
        add(database, f"exchange_{i:014d}", "계약금액 계약기간")
    rows = search(database, limit=100)
    assert len(rows) == 100
    assert {r["lexical_rank"] for r in rows} == {50.5}
    assert {r["lexical_tie_count"] for r in rows} == {100}
    assert "score_counts" not in lexical_sql("true", ["계약금액"])


def test_lower_coverage_follows_the_whole_higher_tie_group(database):
    for i in range(2):
        add(database, f"high{i}", "계약금액 계약기간")
    for i in range(4):
        add(database, f"low{i}", "계약금액만 공개")
    add(database, "none", "주식수 변동")
    rows = search(database)
    assert len(rows) == 6
    assert [r["lexical_score"] for r in rows] == [2, 2, 1, 1, 1, 1]
    assert [r["lexical_rank"] for r in rows] == [1.5, 1.5, 4.5, 4.5, 4.5, 4.5]


def test_scoped_ranks_ignore_other_companies_dates_and_stale_vectors(database):
    add(database, "wanted", "계약金額 계약금액")
    add(database, "other-company", "계약금액 계약기간", corp="002")
    add(database, "other-year", "계약금액 계약기간", receipt="2024-01-01")
    add(database, "stale", "계약금액 계약기간", stale=True)
    rows = search(database, corp_code="001", date_from="2025-01-01", date_to="2025-12-31")
    assert [(r["chunk_id"], r["lexical_rank"], r["lexical_tie_count"]) for r in rows] == [
        ("wanted", 1.0, 1)
    ]


def test_tie_selection_uses_hashes_not_receipt_order_and_is_repeatable(database):
    names = [f"exchange_{year}0101000001" for year in range(2020, 2040)]
    for name in reversed(names):
        add(database, name, "계약금액 계약기간")
    expected = sorted(
        names, key=lambda value: hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()
    )[:5]
    rows = search(database, limit=5)
    assert [r["chunk_id"] for r in rows] == expected
    assert expected != sorted(names)[:5]
    assert search(database, limit=5) == rows


def test_no_eligible_matches_returns_no_rank_rows(database):
    add(database, "none", "다른 내용")
    assert search(database) == []


def test_query_metacharacters_remain_literal_bound_values(database):
    add(database, "match", "계약%_금액")
    add(database, "no-match", "계약123금액")
    rows = search(database, terms=("계약%_금액",))
    assert [r["chunk_id"] for r in rows] == ["match"]
