from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from disclosure_agent.retrieval.diagnostics import (
    explain_retrieval,
    sanitize_plan,
    summarize_plan,
    vector_index_check,
)
from disclosure_agent.retrieval.search import dense_sql, lexical_sql, scope_sql

RUN = {
    "embedding_run_id": "v2",
    "chunk_run_id": "chunks",
    "input_version": "retrieval-embedding-v2",
}
RAW = [
    {
        "Plan": {
            "Node Type": "Limit",
            "Actual Rows": 100,
            "Actual Loops": 1,
            "Shared Read Blocks": 10,
            "Temp Written Blocks": 20,
            "Output": ["PRIVATE_QUERY_VECTOR"],
            "Filter": "PRIVATE_QUERY_TEXT",
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Index Name": "hnsw_cosine",
                    "Relation Name": "retrieval_embeddings",
                    "Plan Rows": 100,
                    "Actual Rows": 100,
                    "Actual Loops": 1,
                    "Shared Read Blocks": 10,
                    "Order By": "PRIVATE_QUERY_VECTOR",
                    "Index Cond": "PRIVATE_LITERAL",
                },
                {
                    "Node Type": "Sort",
                    "Sort Method": "external merge",
                    "Sort Space Type": "Disk",
                    "Sort Space Used": 512,
                    "Sort Key": ["PRIVATE_LITERAL"],
                },
            ],
            "Workers": [{"Worker Number": 0, "Actual Rows": 50, "Output": ["PRIVATE_LITERAL"]}],
        },
        "Execution Time": 1500,
        "Planning Time": 2,
        "Settings": {"unsafe": "PRIVATE_PASSWORD"},
        "JIT": {"Functions": 10, "Timing": {"Total": 4, "Output": "PRIVATE_LITERAL"}},
    }
]
INDEXES = [
    {"index_name": "hnsw_cosine", "access_method": "hnsw", "is_valid": True, "is_ready": True}
]


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def one(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


class Connection:
    def __init__(self, *, fail_dense=False):
        self.calls = []
        self.rollbacks = 0
        self.fail_dense = fail_dense

    @contextmanager
    def begin_nested(self):
        try:
            yield
        except Exception:
            self.rollbacks += 1
            raise

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "current_setting('server_version')" in sql:
            return Result({"postgres_version": "16", "work_mem": "4MB"})
        if "FROM pg_index ix" in sql:
            return Result(INDEXES)
        if sql.startswith("EXPLAIN"):
            if self.fail_dense and "AS similarity" in sql:

                class Timeout(Exception):
                    sqlstate = "57014"

                raise DBAPIError(
                    "PRIVATE_SQL", {"vector": "PRIVATE_VECTOR"}, Timeout("PRIVATE_KEY")
                )
            return Result(RAW)
        return Result(None)


def test_plan_sanitizer_omits_expressions_credentials_and_unknown_fields():
    clean = sanitize_plan(RAW[0])
    assert "PRIVATE" not in json.dumps(clean)
    assert clean["Plan"]["Plans"][0]["Index Name"] == "hnsw_cosine"
    assert clean["JIT"]["Timing"]["Total"] == 4
    assert clean["Plan"]["Workers"][0]["Actual Rows"] == 50


def test_buffer_summary_does_not_double_count_parent_and_child():
    result = summarize_plan(sanitize_plan(RAW[0]))
    assert result["root_buffers"]["Shared Read Blocks"] == 10
    assert result["indexes"] == ["hnsw_cosine"]
    assert result["disk_sorts"][0]["Sort Space Used"] == 512
    assert result["execution_ms"] == 1500


def test_plan_only_uses_real_bound_queries_without_analyze_or_normal_retrieval():
    conn = Connection()
    report = explain_retrieval(
        conn,
        run=RUN,
        query="계약금액",
        vector="PRIVATE_VECTOR",
        mode="hybrid",
        filters={"corp_code": "001"},
    )
    assert report["status"] == "planned"
    assert report["candidate_select_attempts"] == 0
    assert report["dense_strategy"] == "exact_filtered"
    assert conn.calls[0][0] == "SET TRANSACTION READ ONLY"
    assert any("statement_timeout = '60s'" in sql for sql, _ in conn.calls)
    plans = [(sql, params) for sql, params in conn.calls if sql.startswith("EXPLAIN")]
    assert len(plans) == 2
    where, _ = scope_sql(RUN, corp_code="001")
    assert plans[0][0] == "EXPLAIN (FORMAT JSON) " + dense_sql(where, exact=True)
    assert plans[1][0] == "EXPLAIN (FORMAT JSON) " + lexical_sql(where, ["계약금액"])
    assert all(params["corp_code"] == "001" for _, params in plans)
    assert all("PRIVATE_VECTOR" not in sql for sql, _ in plans)
    assert "PRIVATE" not in json.dumps(report)


def test_analyze_executes_each_selected_query_only_once_and_disables_node_timing():
    conn = Connection()
    report = explain_retrieval(
        conn, run=RUN, query="계약금액", vector="[0]", mode="hybrid", analyze=True
    )
    plans = [sql for sql, _ in conn.calls if sql.startswith("EXPLAIN")]
    assert len(plans) == 2 and report["candidate_select_attempts"] == 2
    assert all("ANALYZE TRUE, BUFFERS TRUE, TIMING FALSE" in sql for sql in plans)
    assert set(report["stages"]) == {"dense_initial", "lexical"}
    assert report["database_writes"] == 0 and report["status"] == "analyzed"


def test_timeout_preserves_other_stage_and_never_exports_raw_error():
    conn = Connection(fail_dense=True)
    report = explain_retrieval(
        conn, run=RUN, query="계약금액", vector="[0]", mode="hybrid", analyze=True
    )
    assert report["status"] == "partial" and conn.rollbacks == 1
    assert report["stages"]["dense_initial"]["sqlstate"] == "57014"
    assert report["stages"]["lexical"]["status"] == "analyzed"
    assert "PRIVATE" not in json.dumps(report)


def test_lexical_only_diagnostic_requires_no_query_vector():
    report = explain_retrieval(Connection(), run=RUN, query="계약금액", vector=None, mode="lexical")
    assert set(report["stages"]) == {"lexical"}
    assert report["dense_strategy"] == "not_used"
    assert report["initial_vector_pool_limit"] is None


def test_vector_first_diagnostic_explains_only_shared_initial_query():
    conn = Connection()
    report = explain_retrieval(conn, run=RUN, query="계약금액", vector="[0]", analyze=True)
    assert report["schema_version"] == "retrieval-explain-v2"
    assert report["dense_strategy"] == "vector_first"
    assert report["initial_vector_pool_limit"] == 200
    assert report["candidate_select_attempts"] == 1
    plans = [(sql, params) for sql, params in conn.calls if sql.startswith("EXPLAIN")]
    where, _ = scope_sql(RUN)
    assert len(plans) == 1
    assert plans[0][0].endswith(dense_sql(where, exact=False))
    assert plans[0][1]["pool_limit"] == 200
    assert any(sql == "SET LOCAL hnsw.iterative_scan = 'strict_order'" for sql, _ in conn.calls)
    assert report["stages"]["dense_initial"]["summary"]["vector_index_check"] == {
        "status": "hnsw_used",
        "index_names": ["hnsw_cosine"],
    }


@pytest.mark.parametrize("analyze,status", [(False, "hnsw_planned"), (True, "hnsw_used")])
def test_hnsw_evidence_distinguishes_plan_from_execution(analyze, status):
    assert vector_index_check(sanitize_plan(RAW[0]), INDEXES, analyze=analyze)["status"] == status


@pytest.mark.parametrize(
    "catalog",
    [
        [],
        [{**INDEXES[0], "access_method": "btree"}],
        [{**INDEXES[0], "is_valid": False}],
        [{**INDEXES[0], "is_ready": False}],
    ],
)
def test_hnsw_name_alone_or_invalid_index_is_not_evidence(catalog):
    assert (
        vector_index_check(sanitize_plan(RAW[0]), catalog, analyze=True)["status"]
        == "hnsw_not_used"
    )


def test_unexecuted_index_scan_does_not_count_as_hnsw_used():
    plan = {"Plan": {"Index Name": "hnsw_cosine", "Actual Loops": 0}}
    assert vector_index_check(plan, INDEXES, analyze=True)["status"] == "hnsw_not_used"
    assert vector_index_check(plan, INDEXES, analyze=False)["status"] == "hnsw_planned"


def test_sequential_scan_and_missing_catalog_are_explicit():
    plan = {"Plan": {"Node Type": "Seq Scan", "Actual Loops": 1}}
    assert vector_index_check(plan, INDEXES, analyze=True)["status"] == "hnsw_not_used"
    assert vector_index_check(plan, None, analyze=True)["status"] == "unknown_catalog"


def test_missing_index_catalog_is_partial_without_discarding_plan():
    class MissingCatalog(Connection):
        def execute(self, statement, params=None):
            if "FROM pg_index ix" in str(statement):
                raise DBAPIError("PRIVATE_SQL", {}, Exception("PRIVATE_KEY"))
            return super().execute(statement, params)

    conn = MissingCatalog()
    report = explain_retrieval(conn, run=RUN, query="계약금액", vector="[0]", analyze=True)
    assert report["status"] == "partial"
    assert report["stages"]["dense_initial"]["status"] == "analyzed"
    assert (
        report["stages"]["dense_initial"]["summary"]["vector_index_check"]["status"]
        == "unknown_catalog"
    )
    assert conn.rollbacks == 1
    assert "PRIVATE" not in json.dumps(report)


def test_dense_diagnostic_does_not_substitute_fake_vector():
    conn = Connection()
    with pytest.raises(ValueError, match="real query vector"):
        explain_retrieval(conn, run=RUN, query="계약", vector=None)
    assert conn.calls == []


def test_no_terms_skips_lexical_plan_without_relaxing_filters():
    report = explain_retrieval(Connection(), run=RUN, query="---", vector=None, mode="lexical")
    assert report["stages"] == {} and report["candidate_select_attempts"] == 0
