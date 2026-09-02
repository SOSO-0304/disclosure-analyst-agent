from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.retrieval.contract_answers import render_contract_findings, source_table_sql
from disclosure_agent.retrieval.contract_query import (
    CONTRACT_SUBTYPE,
    plan_contract_query,
    quantity_pattern,
    quantity_probes,
    stopped_contract_answer,
)
from disclosure_agent.retrieval.qa_benchmark import load_benchmark
from disclosure_agent.retrieval.search import promote_quantity_candidates, quantity_sql, scope_sql

RUN = {"embedding_run_id": "v2", "chunk_run_id": "chunks"}


def plan(query, **overrides):
    return plan_contract_query(
        query,
        {
            "corp_code": "corp",
            "date_from": None,
            "date_to": None,
            "document_group": None,
            "chunk_type": None,
            "corrections": "all",
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("2032-02-29에 공시한 계약", "2032-02-29", "2032-02-29"),
        ("2032년 2월 공급계약", "2032-02-01", "2032-02-29"),
        ("2031년 2월 공급계약", "2031-02-01", "2031-02-28"),
        ("2029년 공급계약", "2029-01-01", "2029-12-31"),
        ("2029년 4월 3일 발표한 계약", "2029-04-03", "2029-04-03"),
        ("접수일 2029/04/03 계약", "2029-04-03", "2029-04-03"),
        ("2029.04.03 공시 계약", "2029-04-03", "2029-04-03"),
        ("2029년 2월부터 2029년 3월 공시 계약", "2029-02-01", "2029-03-31"),
        ("2029-02-01 ~ 2029-03-04 공시 계약", "2029-02-01", "2029-03-04"),
    ],
)
def test_receipt_date_grammar_not_tied_to_benchmark_dates(text, start, end):
    result = plan(text)
    assert result["status"] == "ready"
    assert result["filters"]["date_from"] == date.fromisoformat(start)
    assert result["filters"]["date_to"] == date.fromisoformat(end)
    assert result["notes"]  # interpreted date domain must be visible to the user


@pytest.mark.parametrize(
    "text,reason",
    [
        ("2029년 2월 29일 공시 계약", "invalid_date"),
        ("2029-13-01 공시 계약", "invalid_date"),
        ("2029년 13월 공시 계약", "invalid_date"),
        ("2029-05-03~2029-04-01 공시 계약", "reversed_dates"),
        ("2029-04-01 또는 2029-05-01 계약", "multiple_dates"),
        ("2029-04-01~2029-05-01~2029-06-01 계약", "multiple_dates"),
        ("2029-01-01에 시작하는 계약", "contract_date_not_receipt"),
        ("계약 종료일이 2029-01-01인 계약", "contract_date_not_receipt"),
        ("2029년 계약이 종료되는 공급계약", "contract_date_not_receipt"),
        ("2029년 1월에 체결한 계약", "contract_date_not_receipt"),
        ("계약기간 2029-01-01~2029-12-31 계약", "contract_date_not_receipt"),
        ("2029년부터 공시한 계약", "open_date_range"),
        ("올해 공시한 공급계약", "relative_date"),
        ("29년 공급계약", "unsupported_date_format"),
        ("3월 공시 계약", "unsupported_date_format"),
        ("2029-04 계약", "unsupported_date_format"),
        ("2029년 3분기 공시 계약", "unsupported_date_format"),
    ],
)
def test_ambiguous_or_invalid_date_does_not_silently_become_another_scope(text, reason):
    result = plan(text)
    assert result["status"] == "clarification_required"
    assert result["reason_code"] == reason
    assert result["filters"]["date_from"] is None


def test_explicit_dates_are_intersected_never_relaxed():
    result = plan("2032년 2월 계약", date_from="2032-02-10", date_to="2032-02-20")
    assert result["filters"]["date_from"] == date(2032, 2, 10)
    assert result["filters"]["date_to"] == date(2032, 2, 20)
    result = plan("2032년 2월 계약", date_from="2033-02-10")
    assert result["status"] == "clarification_required" and result["reason_code"] == "date_conflict"
    result = plan("계약", date_from="2033-03-10", date_to="2033-02-10")
    assert result["reason_code"] == "reversed_dates"


@pytest.mark.parametrize(
    "query,reason",
    [
        ("공급계약 금액 평균을 알려줘", "aggregation"),
        ("모든 수주액을 더해줘", "aggregation"),
        ("정정과 해지를 반영한 현재 계약금액", "contract_lifecycle"),
        ("최신 계약의 금액", "contract_lifecycle"),
        ("계약금액을 유로로 환산", "currency_conversion"),
        ("계약금액과 영업이익을 알려줘", "financial_analysis"),
        ("이 계약의 매출총이익", "financial_analysis"),
        ("대표이사가 누구야", "outside_contract_fields"),
    ],
)
def test_unsupported_and_mixed_intents_are_explicit(query, reason):
    result = plan(query)
    assert result["status"] == "unsupported" and result["reason_code"] == reason
    answer = stopped_contract_answer(result, RUN)
    assert answer["findings"] == [] and answer["query_provider_calls"] == 0
    assert answer["reason"] in render_contract_findings(answer)
    assert "계약이 없다는 뜻" not in render_contract_findings(answer)


@pytest.mark.parametrize(
    "query",
    ["계약금액과 상대방, 기간", "정정공시의 계약금액", "예비 작업 계약의 시작일", "총 계약금액은?"],
)
def test_supported_fields_are_not_misclassified(query):
    assert plan(query)["status"] == "ready"


def test_subtype_and_explicit_correction_scope_preserved():
    result = plan("정정공시 제외 계약금액")
    assert result["filters"]["corrections"] == "exclude"
    assert result["filters"]["document_subtype"] == CONTRACT_SUBTYPE
    assert result["filters"]["document_group"] == "exchange"
    assert result["filters"]["chunk_type"] == "table"
    assert (
        plan("정정공시의 계약금액", corrections="exclude")["reason_code"] == "correction_conflict"
    )
    assert plan("계약", document_group="periodic")["reason_code"] == "incompatible_scope"
    assert plan("계약", chunk_type="narrative")["reason_code"] == "incompatible_scope"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("배전변압기 3,500대의 공급계약", [("3500", "대")]),
        ("변압기 3500 대 계약", [("3500", "대")]),
        ("LNG 12척은 얼마야", [("12", "척")]),
        ("변압기 380KV 18대", [("18", "대")]),
        ("1000000000대 계약", []),
        ("계약금액 3500원 2025년", []),
        ("1.5대 계약", []),
        ("1,25대 계약", []),
        ("3500세대 계약", []),
    ],
)
def test_quantity_extraction(query, expected):
    assert quantity_probes(query) == expected


def test_quantity_matching_and_bounded_promotion():
    pattern = quantity_pattern(("3500", "대"))
    assert re.search(pattern, re.sub(r"[\s,]", "", "변압기 3,500 대"))
    assert not re.search(pattern, "변압기13500대")
    assert not re.search(pattern, "변압기1.3500대")
    dense = [{"chunk_id": "near", "rrf_score": 0.02}, {"chunk_id": "far", "rrf_score": 0.01}]
    promoted = promote_quantity_candidates(
        dense, [{"chunk_id": "literal", "quantity_matches": 1, "similarity": 0.5}]
    )
    assert [r["chunk_id"] for r in promoted] == ["literal", "near", "far"]
    assert dense == [
        {"chunk_id": "near", "rrf_score": 0.02},
        {"chunk_id": "far", "rrf_score": 0.01},
    ]
    assert [r["chunk_id"] for r in promote_quantity_candidates(dense, [])] == ["near", "far"]
    with pytest.raises(ValueError):
        quantity_pattern((".*", "대"))


def test_quantity_sql_and_source_expansion_preserve_subtype_and_scope():
    where, params = scope_sql(RUN, **plan("3500대 계약")["filters"])
    assert params["document_subtype"] == CONTRACT_SUBTYPE
    for sql in (quantity_sql(where, [("3500", "대")], has_vector=True), source_table_sql(where)):
        for clause in (
            "e.embedding_run_id = :run_id",
            "e.chunk_run_id = :chunk_run_id",
            "e.chunk_content_sha256 = c.content_sha256",
            "f.corp_code = :corp_code",
            "f.document_subtype = :document_subtype",
            "c.chunk_type = :chunk_type",
        ):
            assert clause in sql
    assert "LIMIT :candidate_limit" in quantity_sql(where, [("3500", "대")], has_vector=False)


def test_quantity_sql_portable_semantics_no_other_scope_or_stale_leak():
    # Runs relational predicates in SQLite with only PostgreSQL regex/function syntax
    # translated. This is not a PostgreSQL planner/latency test.
    with sqlite3.connect(":memory:") as conn:
        conn.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
        conn.create_function("regexp_replace", 4, lambda value, *_: re.sub(r"[\s,]", "", value))
        conn.create_function("md5", 1, lambda value: value)
        conn.executescript("""
            ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.retrieval_embeddings (
                chunk_id TEXT, chunk_run_id TEXT, embedding_run_id TEXT, chunk_content_sha256 TEXT);
            CREATE TABLE public.retrieval_chunks (
                chunk_id TEXT, chunk_run_id TEXT, filing_id TEXT, content_sha256 TEXT,
                content TEXT, document_group TEXT, chunk_type TEXT);
            CREATE TABLE public.source_filings (
                filing_id TEXT, corp_code TEXT, document_subtype TEXT,
                receipt_date TEXT, is_correction BOOL);
        """)
        for name, corp, subtype, content, h in [
            ("wanted", "corp", CONTRACT_SUBTYPE, "3,500 대", "h"),
            ("wrong-corp", "other", CONTRACT_SUBTYPE, "3500대", "h"),
            ("wrong-type", "corp", "투자판단", "3500대", "h"),
            ("substring", "corp", CONTRACT_SUBTYPE, "13500대", "h"),
            ("stale", "corp", CONTRACT_SUBTYPE, "3500대", "old"),
        ]:
            conn.execute(
                "INSERT INTO public.retrieval_embeddings VALUES (?, 'chunks', 'v2', ?)", (name, h)
            )
            conn.execute(
                "INSERT INTO public.retrieval_chunks "
                "VALUES (?, 'chunks', ?, 'h', ?, 'exchange', 'table')",
                (name, name, content),
            )
            conn.execute(
                "INSERT INTO public.source_filings VALUES (?, ?, ?, '2032-02-10', 0)",
                (name, corp, subtype),
            )
        where, params = scope_sql(RUN, **plan("3500대 계약")["filters"])
        sql = quantity_sql(where, [("3500", "대")], has_vector=False).replace(" ~ ", " REGEXP ")
        rows = conn.execute(
            sql, {**params, "quantity_0": quantity_pattern(("3500", "대")), "candidate_limit": 100}
        ).fetchall()
        assert [row[0] for row in rows] == ["wanted"]


def test_v2_changes_only_ambiguous_p3_not_gold_or_grading():
    root = Path(__file__).resolve().parents[2]
    old, _ = load_benchmark(root / "data/benchmarks/supply-contract-qa-v1.json")
    new, _ = load_benchmark(root / "data/benchmarks/supply-contract-qa-v2.json")
    assert old["records"] == new["records"]
    assert [a["id"] for a, b in zip(old["cases"], new["cases"], strict=True) if a != b] == ["P3"]
    for a, b in zip(old["cases"], new["cases"], strict=True):
        assert a["expected"] == b["expected"]
    assert new["changes"][0]["gold_unchanged"]


def test_all_40_v2_intents_only_read_requests_no_gold():
    root = Path(__file__).resolve().parents[2]
    benchmark, _ = load_benchmark(root / "data/benchmarks/supply-contract-qa-v2.json")
    for case in benchmark["cases"]:
        request = case["request"]
        result = plan_contract_query(
            request["query"], {k: v for k, v in request.items() if k != "query"}
        )
        assert result["status"] == ("unsupported" if case["group"] == "unsupported" else "ready"), (
            case["id"],
            result,
        )


def test_company_particle_does_not_look_like_stock_price():
    assert plan("한국항공우주가 공시한 계약금액은?")["status"] == "ready"
    assert plan("한국항공우주의 주가와 계약금액은?")["status"] == "unsupported"
