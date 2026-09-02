from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256

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
    vector_pool_limits,
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
            return Result(
                self.ann if "WITH vector_candidates" in sql and self.ann is not None else self.dense
            )
        return Result()


def test_retrieval_company_filter_uses_exact_and_both_candidate_lanes():
    connection = Connection(
        dense=[{"chunk_id": "a", "similarity": 0.9}],
        lexical=[{"chunk_id": "b", "lexical_score": 1}],
        details=[evidence("a", "f1"), evidence("b", "f2")],
    )
    payload = retrieve(
        connection,
        run=RUN,
        query="계약금액",
        vector="[0]",
        top_k=2,
        mode="hybrid",
        filters={"corp_code": "001"},
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
    assert payload["dense_diagnostics"]["pool_attempts"] == [
        {"pool_limit": size, "eligible_candidates": 0} for size in (200, 800, 3200)
    ]
    assert payload["dense_diagnostics"]["index_usage"] == "not_observed"
    queries = [(sql, params) for sql, params in connection.calls if "AS similarity" in sql]
    assert len(queries) == 4
    assert "WITH candidates AS MATERIALIZED" in queries[-1][0]
    where, _ = scope_sql(RUN)
    for sql, params in queries:
        assert where in sql
        assert params["run_id"] == "run-v2" and params["chunk_run_id"] == "chunks"


@pytest.mark.parametrize(
    "limit,expected", [(1, (32, 128, 512)), (100, (200, 800, 3200)), (1000, (2000, 3200))]
)
def test_vector_pool_expansion_is_bounded(limit, expected):
    assert vector_pool_limits(limit) == expected


@pytest.mark.parametrize("limit", [0, 1001])
def test_invalid_vector_candidate_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="Candidate limit"):
        vector_pool_limits(limit)


@pytest.mark.parametrize("expand", [False, True])
def test_vector_pool_stops_when_full_and_replaces_earlier_ranking(expand):
    class ExpandingConnection(Connection):
        def execute(self, statement, params=None):
            if "WITH vector_candidates" in str(statement):
                self.ann = (
                    []
                    if expand and params["pool_limit"] == 32
                    else [{"chunk_id": "a", "similarity": 0.9}]
                )
            return super().execute(statement, params)

    connection = ExpandingConnection(details=[evidence("a", "f1")])
    payload = retrieve(connection, run=RUN, query="계약", vector="[0]", top_k=1, candidate_limit=1)
    assert payload["dense_strategy"] == ("vector_first_expanded" if expand else "vector_first")
    assert len(payload["dense_diagnostics"]["pool_attempts"]) == (2 if expand else 1)
    assert [row["chunk_id"] for row in payload["results"]] == ["a"]
    assert not any("WITH candidates AS MATERIALIZED" in sql for sql, _ in connection.calls)
    settings = [sql for sql, _ in connection.calls if sql.startswith("SET")]
    assert settings == [
        "SET LOCAL hnsw.ef_search = 200",
        "SET LOCAL hnsw.iterative_scan = 'strict_order'",
    ]


@pytest.mark.parametrize("fallback", [False, True])
def test_expansion_and_fallback_replace_nonempty_previous_results(fallback):
    class ReplacingConnection(Connection):
        def execute(self, statement, params=None):
            if "WITH vector_candidates" in str(statement):
                self.ann = [{"chunk_id": "old", "similarity": 0.5}]
                if not fallback and params["pool_limit"] > 32:
                    self.ann = self.dense
            return super().execute(statement, params)

    connection = ReplacingConnection(
        dense=[{"chunk_id": "a", "similarity": 0.9}, {"chunk_id": "b", "similarity": 0.8}],
        details=[evidence("old", "old-f"), evidence("a", "f1"), evidence("b", "f2")],
    )
    payload = retrieve(connection, run=RUN, query="계약", vector="[0]", top_k=2, candidate_limit=2)
    assert [row["chunk_id"] for row in payload["results"]] == ["a", "b"]
    assert payload["candidate_counts"]["dense"] == 2
    assert payload["dense_strategy"] == ("exact_fallback" if fallback else "vector_first_expanded")


@pytest.mark.parametrize(
    "filters",
    [
        {"corp_code": "001"},
        {"date_from": date(2025, 1, 1)},
        {"document_group": "major"},
        {"chunk_type": "table"},
        {"corrections": "only"},
    ],
)
def test_narrowed_scope_never_expands_vector_pool(filters):
    connection = Connection()
    payload = retrieve(connection, run=RUN, query="계약", vector="[0]", filters=filters)
    assert payload["dense_strategy"] == "exact_filtered"
    assert payload["dense_diagnostics"]["pool_attempts"] == []
    assert not any(
        "vector_candidates" in sql or "iterative_scan" in sql for sql, _ in connection.calls
    )


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


def test_lexical_rrf_uses_candidate_midrank_not_arbitrary_tie_position():
    lexical = [
        {
            "chunk_id": name,
            "lexical_score": 3,
            "lexical_rank": Decimal("1.5"),
            "lexical_tie_count": 2,
        }
        for name in ("exchange_20230101", "exchange_20260101")
    ]
    rows = fuse_rankings([], lexical)
    assert {row["lexical_rank"] for row in rows} == {1.5}
    assert all(row["rrf_score"] == pytest.approx(1 / 61.5) for row in rows)
    assert fuse_rankings([], list(reversed(lexical))) == rows
    assert [r["chunk_id"] for r in rows] == sorted(
        [r["chunk_id"] for r in rows], key=lambda value: sha256(value.encode()).hexdigest()
    )


@pytest.mark.parametrize("rank", [0, -1, float("inf"), float("nan")])
def test_invalid_lexical_rank_is_rejected(rank):
    with pytest.raises(ValueError, match="Candidate rank"):
        fuse_rankings([], [{"chunk_id": "a", "lexical_score": 1, "lexical_rank": rank}])


def test_midrank_fusion_still_rewards_independent_agreement():
    rows = fuse_rankings(
        [{"chunk_id": "a", "similarity": 0.9}, {"chunk_id": "b", "similarity": 0.8}],
        [
            {"chunk_id": "b", "lexical_score": 2, "lexical_rank": 2.5},
            {"chunk_id": "c", "lexical_score": 2, "lexical_rank": 2.5},
        ],
    )
    assert rows[0]["chunk_id"] == "b"
    assert rows[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 62.5)


def test_timing_breakdown_includes_fetch_and_separates_exact_fallback(monkeypatch):
    import disclosure_agent.retrieval.search as search

    clock = [0.0]
    monkeypatch.setattr(search, "perf_counter", lambda: clock[0])

    class TimedResult(Result):
        def mappings(self):
            clock[0] += 0.25
            return super().mappings()

    class TimedConnection(Connection):
        def execute(self, statement, params=None):
            sql = str(statement)
            result = super().execute(statement, params)
            if sql.lstrip().startswith("SET"):
                return result
            if "WITH lexical AS" in sql:
                clock[0] += 3
            elif "c.content_sha256, c.filing_id" in sql:
                clock[0] += 4
            elif "WITH vector_candidates" in sql:
                clock[0] += 1
            elif "MATERIALIZED" in sql:
                clock[0] += 2
            else:
                clock[0] += 1
            return TimedResult(result.rows)

    connection = TimedConnection(
        ann=[],
        dense=[{"chunk_id": "a", "similarity": 0.9}],
        lexical=[
            {"chunk_id": "a", "lexical_score": 2, "lexical_rank": 5.5, "lexical_tie_count": 10}
        ],
        details=[evidence("a", "f1")],
    )
    payload = retrieve(connection, run=RUN, query="계약금액", vector="[0]", top_k=1, mode="hybrid")
    assert payload["timing_seconds"] == {
        "dense_initial": 1.25,
        "dense_expansion": 2.5,
        "dense_fallback": 2.25,
        "lexical": 3.25,
        "hydration": 4.25,
        "fusion": 0,
        "selection": 0,
        "total": 13.5,
    }
    assert payload["candidate_counts"] == {"dense": 1, "lexical": 1, "overlap": 1, "fused": 1}
    assert payload["lexical_diagnostics"]["rank_policy"] == "candidate_midrank"
    assert payload["lexical_diagnostics"]["full_scope_tie_count"] is None
    assert payload["results"][0]["lexical_tie_count"] == 10


@pytest.mark.parametrize("mode", ["lexical", "dense"])
def test_skipped_phases_have_zero_time(mode):
    payload = retrieve(Connection(), run=RUN, query="계약", vector="[0]", mode=mode)
    if mode == "lexical":
        assert payload["timing_seconds"]["dense_initial"] == 0
        assert payload["timing_seconds"]["dense_fallback"] == 0
    else:
        assert payload["timing_seconds"]["lexical"] == 0
    assert payload["timing_seconds"]["hydration"] == 0
    assert payload["lexical_diagnostics"]["candidate_limit_reached"] is False


def test_explicit_unfiltered_exact_strategy_has_correct_label():
    payload = retrieve(Connection(), run=RUN, query="계약", vector="[0]", mode="dense", exact=True)
    assert payload["dense_strategy"] == "exact"


def test_default_retrieval_never_executes_lexical_sql():
    connection = Connection(dense=[{"chunk_id": "a", "similarity": 0.9}])
    payload = retrieve(connection, run=RUN, query="계약금액", vector="[0]")
    assert payload["mode"] == "dense"
    assert payload["lexical_diagnostics"]["rank_policy"] == "not_used"
    assert payload["timing_seconds"]["lexical"] == 0
    assert not any("WITH lexical AS" in sql for sql, _ in connection.calls)


def test_full_candidate_window_does_not_claim_known_corpus_tie_count():
    connection = Connection(
        lexical=[{"chunk_id": "a", "lexical_score": 1, "lexical_rank": 1, "lexical_tie_count": 1}],
    )
    result = retrieve(
        connection, run=RUN, query="계약", vector=None, mode="lexical", top_k=1, candidate_limit=1
    )
    assert result["lexical_diagnostics"]["candidate_limit_reached"] is True
    assert result["lexical_diagnostics"]["full_scope_tie_count"] is None
