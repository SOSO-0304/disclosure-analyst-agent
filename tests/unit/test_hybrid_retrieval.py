from __future__ import annotations

from datetime import date

import pytest

from disclosure_agent.retrieval.hybrid import (
    citation,
    fuse_rankings,
    lexical_terms,
    resolve_company,
    select_evidence,
    validate_dates,
)
from disclosure_agent.retrieval.search import (
    completed_run,
    dense_sql,
    lexical_sql,
    retrieve,
    scope_sql,
)

COMPANIES = [
    {
        "corp_code": "001",
        "stock_code": "111111",
        "corp_name": "삼성생명 주식회사",
        "listed_name": "삼성생명",
    },
    {
        "corp_code": "002",
        "stock_code": "222222",
        "corp_name": "삼성화재",
        "listed_name": "삼성화재",
    },
    {"corp_code": "003", "stock_code": "333333", "corp_name": "심텍", "listed_name": "심텍"},
    {
        "corp_code": "004",
        "stock_code": "444444",
        "corp_name": "심텍홀딩스",
        "listed_name": "심텍홀딩스",
    },
]
RUN = {"embedding_run_id": "run-v2", "chunk_run_id": "chunks"}


def test_company_resolution_with_particle_and_whitespace():
    assert resolve_company(COMPANIES, "삼성 생명의 매출")["corp_code"] == "001"
    assert resolve_company(COMPANIES, "심텍홀딩스의 매출")["corp_code"] == "004"
    assert resolve_company(COMPANIES, "보험업의 매출") is None
    assert resolve_company(COMPANIES, "삼성생명공학 매출") is None


def test_explicit_company_name_code_and_conflicts():
    assert resolve_company(COMPANIES, "매출", company="주식회사 삼성생명")["corp_code"] == "001"
    assert resolve_company(COMPANIES, "매출", company="222222")["corp_code"] == "002"
    with pytest.raises(ValueError, match="not found"):
        resolve_company(COMPANIES, "매출", company="없는회사")
    with pytest.raises(ValueError, match="different"):
        resolve_company(COMPANIES, "매출", company="삼성생명", corp_code="002")
    with pytest.raises(ValueError, match="not found"):
        resolve_company(COMPANIES, "매출", corp_code="999")


def test_multiple_companies_never_silently_pick_one():
    with pytest.raises(ValueError, match="Multiple companies"):
        resolve_company(COMPANIES, "삼성생명과 삼성화재 비교")
    assert resolve_company(COMPANIES, "삼성생명과 삼성화재 비교", auto=False) is None


def test_dates_are_inclusive_and_validated():
    assert validate_dates("2025-01-01", "2025-12-31") == (date(2025, 1, 1), date(2025, 12, 31))
    for values in [("2025-02-30", None), ("20250101", None), ("2026-01-01", "2025-01-01")]:
        with pytest.raises(ValueError):
            validate_dates(*values)


def test_keywords_strip_particles_and_are_bounded():
    assert lexical_terms("계약상대방과 계약금액, 계약기간을 알려줘") == [
        "계약상대",
        "계약금액",
        "계약기간",
    ]
    assert len(lexical_terms(" ".join(f"term{i}" for i in range(30)))) == 12
    assert lexical_terms("---") == []


def test_rrf_unions_independent_candidates_and_handles_duplicates():
    result = fuse_rankings(
        [{"chunk_id": "a", "similarity": 0.9}, {"chunk_id": "b", "similarity": 0.8}],
        [
            {"chunk_id": "b", "lexical_score": 3},
            {"chunk_id": "c", "lexical_score": 2},
            {"chunk_id": "b", "lexical_score": 3},
        ],
    )
    assert result[0]["chunk_id"] == "b"
    assert result[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert {r["chunk_id"] for r in result} == {"a", "b", "c"}


def evidence(chunk, filing, corp="001", table=None):
    return {
        "chunk_id": chunk,
        "filing_id": filing,
        "corp_code": corp,
        "source_table_id": table,
        "content_sha256": chunk,
        "document_id": "doc",
        "is_correction": False,
        "receipt_number": "20250707800058",
        "source_block_ids": ["block"],
        "content": "계약금액 100원",
    }


def test_selection_preserves_distinct_filings_and_soft_diversity():
    rows = [
        evidence("a", "f1"),
        evidence("b", "f1"),
        evidence("c", "f2"),
        evidence("d", "f3"),
        evidence("e", "f4", "002"),
    ]
    result = select_evidence(rows, top_k=4, company_cap=2)
    assert [r["chunk_id"] for r in result] == ["a", "c", "e", "d"]
    assert len({r["filing_id"] for r in result}) == 4


def test_correction_and_original_with_same_content_are_not_merged():
    original = evidence("a", "f1")
    correction = {**evidence("b", "f2"), "is_correction": True, "content_sha256": "a"}
    result = select_evidence([original, correction], top_k=2)
    assert len(result) == 2
    assert citation(correction)["lineage_status"] == "not_resolved"
    assert citation(correction)["correction_status"] == "correction"


def test_same_source_table_fragments_dedup_but_different_receipts_preserved():
    rows = [
        evidence("a", "f1", table="t1"),
        evidence("b", "f1", table="t1"),
        evidence("c", "f2", table="t2"),
    ]
    assert len(select_evidence(rows, top_k=3, max_per_filing=3)) == 2


def test_citations_validate_receipt_number_and_include_provenance():
    value = citation(evidence("a", "f1"))
    assert value["url"].endswith("rcpNo=20250707800058")
    assert value["source_block_ids"] == ["block"]
    assert citation({**evidence("a", "f1"), "receipt_number": "bad&url"})["url"] is None


def test_filters_are_shared_bound_parameters_not_sql_interpolation():
    where, params = scope_sql(
        RUN,
        corp_code="x' OR true --",
        date_from=date(2025, 1, 1),
        date_to=date(2025, 12, 31),
        corrections="only",
    )
    assert "OR true" not in where
    assert params["corp_code"] == "x' OR true --"
    assert "f.receipt_date >= :date_from" in where and "f.receipt_date <= :date_to" in where
    assert params["is_correction"] is True
    assert where in dense_sql(where, exact=True)
    assert where in lexical_sql(where, ["x' OR true --"])
    assert "x' OR true" not in lexical_sql(where, ["x' OR true --"])


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self.rows


class Connection:
    def __init__(self, dense=(), lexical=(), details=(), ann=None):
        self.dense, self.lexical, self.details, self.ann = dense, lexical, details, ann
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "c.content_sha256, c.filing_id" in sql:
            return Result(self.details)
        if "WITH lexical AS" in sql:
            return Result(self.lexical)
        if "AS similarity" in sql:
            return Result(self.dense if "MATERIALIZED" in sql or self.ann is None else self.ann)
        return Result()


def test_retrieval_company_filter_uses_exact_and_both_candidate_lanes():
    connection = Connection(
        dense=[{"chunk_id": "a", "similarity": 0.9}],
        lexical=[{"chunk_id": "b", "lexical_score": 1}],
        details=[evidence("a", "f1"), evidence("b", "f2")],
    )
    payload = retrieve(
        connection, run=RUN, query="계약금액", vector="[0]", top_k=2, filters={"corp_code": "001"}
    )
    assert payload["dense_strategy"] == "exact_filtered"
    assert len(payload["results"]) == 2
    for sql, params in connection.calls:
        if sql.lstrip().startswith("SET"):
            continue
        assert params["run_id"] == "run-v2" and params["corp_code"] == "001"
        assert "f.corp_code = :corp_code" in sql
    assert all(r["citation"]["url"] for r in payload["results"])


def test_ann_underfill_falls_back_without_relaxing_scope():
    connection = Connection(
        ann=[], dense=[{"chunk_id": "a", "similarity": 0.9}], details=[evidence("a", "f1")]
    )
    payload = retrieve(connection, run=RUN, query="계약", vector="[0]", mode="dense", top_k=1)
    assert payload["dense_strategy"] == "exact_fallback"
    assert any("MATERIALIZED" in sql for sql, _ in connection.calls)


def test_lexical_mode_needs_no_vector_and_never_runs_dense():
    connection = Connection(
        lexical=[{"chunk_id": "a", "lexical_score": 1}], details=[evidence("a", "f1")]
    )
    payload = retrieve(connection, run=RUN, query="계약", vector=None, mode="lexical", top_k=1)
    assert payload["dense_strategy"] == "not_used"
    assert not any("AS similarity" in sql for sql, _ in connection.calls)


def test_empty_results_are_not_replaced_with_unfiltered_hits():
    connection = Connection()
    payload = retrieve(
        connection, run=RUN, query="계약", vector=None, mode="lexical", filters={"corp_code": "001"}
    )
    assert payload["results"] == []
    assert any("no filters were relaxed" in warning for warning in payload["warnings"])


def test_run_lookup_only_accepts_completed_active_chunk_snapshot():
    connection = Connection()
    with pytest.raises(ValueError, match="completed embedding run"):
        completed_run(connection, "partial-run")
    sql, params = connection.calls[0]
    assert "e.status = 'completed'" in sql and "c.is_active" in sql
    assert params["run_id"] == "partial-run"
