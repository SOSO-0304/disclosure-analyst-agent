from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from disclosure_agent.retrieval.diagnostics import explain_retrieval, sanitize_plan, summarize_plan
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


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def one(self):
        return self.value


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


def test_dense_diagnostic_does_not_substitute_fake_vector():
    conn = Connection()
    with pytest.raises(ValueError, match="real query vector"):
        explain_retrieval(conn, run=RUN, query="계약", vector=None)
    assert conn.calls == []


def test_no_terms_skips_lexical_plan_without_relaxing_filters():
    report = explain_retrieval(Connection(), run=RUN, query="---", vector=None, mode="lexical")
    assert report["stages"] == {} and report["candidate_select_attempts"] == 0
