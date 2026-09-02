from __future__ import annotations

import copy
import importlib.util
import io
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from disclosure_agent.retrieval.embeddings import EmbeddingResult, EmbeddingTelemetry
from disclosure_agent.retrieval.qa_benchmark import (
    load_benchmark,
    score_case,
    summarize,
    verify_gold_sources,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "contract_qa_cli", ROOT / "scripts/evaluate_contract_qa.py"
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


@pytest.fixture
def benchmark():
    return load_benchmark(ROOT / "data/benchmarks/supply-contract-qa-v1.json")[0]


def fixture_observed(benchmark, case):
    receipt = case["expected"]["targets"][0]
    record = benchmark["records"][receipt]
    filing = f"exchange_{receipt}"
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
    finding = {
        "filing_id": filing,
        "table_id": "table",
        "document_id": "doc",
        "status": "extracted",
        "citation": {"receipt_number": receipt, "url": url},
        "fields": {},
    }
    for name, gold in record["fields"].items():
        finding["fields"][name] = {
            "status": gold["status"],
            "value": gold["value"],
            "unit": gold["unit"],
            "evidence": [
                {
                    "value_locator": {
                        "xpath": gold["xpath"],
                        "source_file_id": filing + ":source:1",
                    },
                    "raw_value": gold["raw_text"],
                    "citation_url": url,
                    "filing_id": filing,
                    "table_id": "table",
                    "document_id": "doc",
                }
            ],
        }
    observed = {
        "kind": "fields",
        "answer": {"findings": [finding]},
        "filters": {"corp_code": "test-corp"},
        "hits": [
            {
                "receipt_number": receipt,
                "corp_code": "test-corp",
                "receipt_date": record["receipt_date"],
            }
        ],
    }
    return observed, {receipt: {"corp_code": "test-corp"}}


def test_frozen_manual_gold_matches_12_raw_sources(benchmark):
    verification = verify_gold_sources(benchmark, ROOT)
    assert verification["status"] == "verified", verification
    assert verification["checked_fields"] == 48
    assert len(benchmark["cases"]) == 40


@pytest.mark.parametrize(
    "change", ["value", "unit", "status", "raw_text", "source_normalized_sha256"]
)
def test_source_validator_rejects_changed_gold(benchmark, change):
    record = next(iter(benchmark["records"].values()))
    if change == "source_normalized_sha256":
        record[change] = "changed"
    else:
        record["fields"]["contract_amount"][change] = "changed"
    assert verify_gold_sources(benchmark, ROOT)["status"] == "blocked"


def test_no_raw_source_is_not_silently_skipped(benchmark, tmp_path):
    result = verify_gold_sources(benchmark, tmp_path)
    assert result["status"] == "blocked" and len(result["errors"]) == 12


@pytest.mark.parametrize("change", ["count", "duplicate", "request", "fields"])
def test_invalid_question_contract_rejected(benchmark, change, tmp_path):
    if change == "count":
        benchmark["cases"].pop()
    elif change == "duplicate":
        benchmark["cases"][1]["id"] = benchmark["cases"][0]["id"]
    elif change == "request":
        benchmark["cases"][0]["request"]["oracle_receipt"] = "injected"
    else:
        benchmark["cases"][0]["expected"]["fields"] = ["gross_profit"]
    path = tmp_path / "invalid.json"
    path.write_bytes(orjson.dumps(benchmark))
    with pytest.raises(ValueError):
        load_benchmark(path)


def test_correct_values_with_original_evidence_pass(benchmark):
    case = benchmark["cases"][0]
    observed, metadata = fixture_observed(benchmark, case)
    result = score_case(case, observed, benchmark["records"], metadata)
    assert result["passed"] and result["retrieval_hit_at_5"]
    assert len(result["field_checks"]) == 4


@pytest.mark.parametrize(
    "change,category",
    [
        ("value", "field_value_status_or_unit_error"),
        ("unit", "field_value_status_or_unit_error"),
        ("status", "field_value_status_or_unit_error"),
        ("xpath", "evidence_error"),
        ("source_file_id", "evidence_error"),
        ("citation_url", "evidence_error"),
        ("raw_value", "evidence_error"),
        ("document_id", "evidence_error"),
        ("corp_code", "wrong_company_result"),
    ],
)
def test_bad_answers_are_not_credited(benchmark, change, category):
    case = benchmark["cases"][0]
    observed, metadata = fixture_observed(benchmark, case)
    field = observed["answer"]["findings"][0]["fields"]["contract_amount"]
    if change in {"value", "unit", "status"}:
        field[change] = "bad"
    elif change in {"xpath", "source_file_id"}:
        field["evidence"][0]["value_locator"][change] = "bad"
    elif change == "corp_code":
        observed["hits"].append({**observed["hits"][0], "corp_code": "other"})
    else:
        field["evidence"][0][change] = "bad"
    result = score_case(case, observed, benchmark["records"], metadata)
    assert not result["passed"] and category in result["failures"]


def test_bad_duplicate_is_not_hidden_by_good_finding(benchmark):
    case = benchmark["cases"][0]
    observed, metadata = fixture_observed(benchmark, case)
    duplicate = copy.deepcopy(observed["answer"]["findings"][0])
    duplicate["fields"]["contract_amount"]["value"] = "999"
    observed["answer"]["findings"].append(duplicate)
    assert not score_case(case, observed, benchmark["records"], metadata)["passed"]


def test_missing_party_requires_null_not_guessed_company(benchmark):
    case = next(c for c in benchmark["cases"] if c["id"] == "M1")
    observed, metadata = fixture_observed(benchmark, case)
    assert score_case(case, observed, benchmark["records"], metadata)["passed"]
    observed["answer"]["findings"][0]["fields"]["counterparty"]["value"] = "Invented customer"
    assert not score_case(case, observed, benchmark["records"], metadata)["passed"]


def test_natural_language_date_cannot_be_filled_from_gold(benchmark):
    case = next(c for c in benchmark["cases"] if c["id"] == "S5")
    observed, metadata = fixture_observed(benchmark, case)
    result = score_case(case, observed, benchmark["records"], metadata)
    assert result["retrieval_hit_at_5"] and "date_scope_not_applied" in result["failures"]
    observed["filters"].update(date_from="2025-07-07", date_to="2025-07-07")
    assert score_case(case, observed, benchmark["records"], metadata)["passed"]


def test_generic_limitations_are_not_an_unsupported_intent_response(benchmark):
    case = next(c for c in benchmark["cases"] if c["group"] == "unsupported")
    observed = {"kind": "fields", "answer": {"findings": [], "limitations": ["No totals"]}}
    assert not score_case(case, observed, benchmark["records"], {})["passed"]
    assert score_case(case, {"kind": "unsupported", "reason": "Aggregation not supported"}, {}, {})[
        "passed"
    ]
    assert not score_case(case, {"kind": "unsupported"}, {}, {})["passed"]


def test_miss_not_hidden_by_conditional_field_accuracy(benchmark):
    case = benchmark["cases"][0]
    _, metadata = fixture_observed(benchmark, case)
    result = score_case(case, {"kind": "fields", "hits": []}, benchmark["records"], metadata)
    summary = summarize([result])
    assert summary["retrieval_hit_at_5"] == 0
    assert summary["field_accuracy_conditional"] is None
    assert summary["not_run"] == 39 and not summary["all_development_checks_passed"]


def test_no_result_scope_rejects_any_hit(benchmark):
    case = next(c for c in benchmark["cases"] if c["id"] == "M5")
    observed = {"kind": "fields", "filters": {"date_from": "1900-01-01", "date_to": "1900-12-31"}}
    assert score_case(case, observed, {}, {})["passed"]
    observed["hits"] = [{"receipt_date": "2025-07-07"}]
    assert not score_case(case, observed, {}, {})["passed"]


class Context:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Connection(Context):
    def __init__(self, rows=(), nonempty=False):
        self.statements = []
        self.rows = rows
        self.nonempty = nonempty

    def begin(self):
        return Context()

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        return SimpleNamespace(mappings=lambda: self.rows, scalar_one=lambda: self.nonempty)


COMPANIES = [{"corp_code": "test-corp", "corp_name": "삼성중공업", "listed_name": "삼성중공업"}]


@pytest.mark.parametrize("fault", [None, "missing", "stale", "date", "negative_scope"])
def test_preflight_checks_frozen_corpus_and_negative_scope(benchmark, monkeypatch, fault):
    run = {
        "embedding_run_id": benchmark["embedding_run_id"],
        "chunk_run_id": benchmark["chunk_run_id"],
    }
    rows = [
        {
            "receipt_number": receipt,
            "corp_code": "test-corp",
            "filing_id": f"exchange_{receipt}",
            "receipt_date": date.fromisoformat(record["receipt_date"]),
            "is_correction": record["is_correction"],
            "has_embedding": True,
        }
        for receipt, record in benchmark["records"].items()
    ]
    if fault == "missing":
        rows.pop()
    elif fault == "stale":
        rows[0]["has_embedding"] = False
    elif fault == "date":
        rows[0]["receipt_date"] = date(1900, 1, 1)
    conn = Connection(rows, nonempty=fault == "negative_scope")
    monkeypatch.setattr(cli, "completed_run", lambda *a: run)
    monkeypatch.setattr(
        cli,
        "company_catalog",
        lambda *a: COMPANIES + [{"corp_code": "hmm", "corp_name": "HMM", "listed_name": "HMM"}],
    )
    _, _, metadata, errors = cli.preflight(conn, benchmark)
    assert bool(errors) == (fault is not None)
    assert len(metadata) == len(rows)
    assert all(statement.lstrip().startswith("SELECT") for statement in conn.statements)
    assert "e.chunk_content_sha256 = c.content_sha256" in conn.statements[0]
    assert "cr.source_load_run_id = f.load_run_id" in conn.statements[0]


def test_an_alternative_accepted_receipt_can_pass_without_specific_chunk(benchmark):
    case = copy.deepcopy(benchmark["cases"][0])
    other = benchmark["cases"][1]
    observed, metadata = fixture_observed(benchmark, other)
    first = case["expected"]["targets"][0]
    metadata[first] = {"corp_code": "test-corp"}
    case["expected"]["targets"] += other["expected"]["targets"]
    assert score_case(case, observed, benchmark["records"], metadata)["passed"]


def test_retrieved_target_without_fields_is_an_extraction_failure(benchmark):
    case = benchmark["cases"][0]
    observed, metadata = fixture_observed(benchmark, case)
    observed["answer"]["findings"] = []
    result = score_case(case, observed, benchmark["records"], metadata)
    assert result["retrieval_hit_at_5"] and result["failures"] == ["extraction_missing"]


def test_request_adapter_applies_only_explicit_inputs():
    request = {"query": "삼성중공업이 2025-07-07 공시한 계약은?"}
    result = cli.request_filters(request, COMPANIES)
    assert result["corp_code"] == "test-corp" and result["date_from"] is None
    request["date_from"] = "2025-07-07"
    assert cli.request_filters(request, COMPANIES)["date_from"] == date(2025, 7, 7)


def test_observer_calls_existing_pipeline_without_oracle(monkeypatch):
    conn = Connection()
    engine = SimpleNamespace(connect=lambda: conn)
    calls = []
    client = SimpleNamespace(
        embed=lambda query: calls.append(query) or EmbeddingResult(tuple([0.1] * 1024), 5, "r")
    )
    captured = {}

    def retrieve(connection, **kwargs):
        captured.update(kwargs)
        return {"results": [], "dense_strategy": "exact_filtered", "timing_seconds": {}}

    monkeypatch.setattr(cli, "retrieve", retrieve)
    monkeypatch.setattr(
        cli,
        "contract_findings",
        lambda *args, **kwargs: {"findings": [], "limitations": ["Not totals"]},
    )
    request = {"query": "삼성중공업 2025-07-07 공시한 계약금액은?"}
    observed = cli.observe_request(engine, {}, client, COMPANIES, request)
    assert calls == [request["query"]]
    assert captured["top_k"] == 5 and captured["filters"]["date_from"] == date(2025, 7, 7)
    assert captured["filters"]["document_subtype"] == "단일판매공급계약체결"
    assert observed["kind"] == "fields"
    assert conn.statements == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '20s'"]


def test_dry_run_does_not_read_env_or_connect(monkeypatch, capsys):
    monkeypatch.setattr(cli, "runtime_from_args", lambda args: pytest.fail("dry run read runtime"))
    monkeypatch.setattr(cli, "get_engine", lambda *args: pytest.fail("dry run connected"))
    monkeypatch.setattr(sys, "argv", ["evaluate_contract_qa.py", "--dry-run"])
    cli.main()
    assert "local_gold_verified (not retrieval-tested)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "query,status",
    [
        ("삼성중공업 공급계약 합산해줘", "unsupported"),
        ("삼성중공업 2031년 2월 29일 공급계약", "clarification_required"),
    ],
)
def test_eval_uses_real_planner_refusal_without_provider_or_db(monkeypatch, query, status):
    client = SimpleNamespace(embed=lambda *a: pytest.fail("refusal must not call provider"))
    engine = SimpleNamespace(connect=lambda: pytest.fail("refusal must not query database"))
    result = cli.observe_request(
        engine, {"embedding_run_id": "v2", "chunk_run_id": "c"}, client, COMPANIES, {"query": query}
    )
    assert result["kind"] == status and result["reason"]
    assert result["hits"] == [] and result["query_provider_calls"] == 0


def test_existing_report_is_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_bytes(b"precious report")
    monkeypatch.setattr(sys, "argv", ["evaluate_contract_qa.py", "--report", str(path)])
    with pytest.raises(SystemExit):
        cli.main()
    assert path.read_bytes() == b"precious report"


def test_checkpoint_preserves_results_and_redacts_errors():
    report = {"results": []}
    stream = io.BytesIO()
    cli.checkpoint(stream, report, EmbeddingTelemetry())
    assert orjson.loads(stream.getvalue())["summary"]["not_run"] == 40
    error = cli.safe_error(RuntimeError("postgresql://secret:key@host Bearer private-key"))
    assert error == {"kind": "error", "error_type": "RuntimeError"}


def test_full_eval_loop_uses_34_queries_and_separates_changed_question(
    benchmark, tmp_path, monkeypatch
):
    companies = [
        {"corp_name": name, "listed_name": name, "corp_code": str(i)}
        for i, name in enumerate(sorted({r["company"] for r in benchmark["records"].values()}))
    ]
    metadata = {
        receipt: {
            "corp_code": next(c["corp_code"] for c in companies if c["corp_name"] == r["company"])
        }
        for receipt, r in benchmark["records"].items()
    }
    run = {
        "embedding_run_id": "v2",
        "chunk_run_id": "chunks",
        "provider": "clova-studio",
        "model": "bge-m3",
        "dimensions": 1024,
        "distance_metric": "cosine",
        "endpoint": "https://example.invalid",
        "input_version": "retrieval-embedding-v2",
    }
    monkeypatch.setattr(
        cli, "runtime_from_args", lambda *a: SimpleNamespace(database_url="unused", api_key="test")
    )
    monkeypatch.setattr(cli, "get_engine", lambda *a: SimpleNamespace(connect=lambda: Connection()))
    monkeypatch.setattr(cli, "preflight", lambda *a: (run, companies, metadata, []))
    monkeypatch.setattr(
        cli,
        "retrieve",
        lambda *a, **kw: {"results": [], "dense_strategy": "exact_filtered", "timing_seconds": {}},
    )
    monkeypatch.setattr(
        cli, "contract_findings", lambda *a, **kw: {"findings": [], "limitations": []}
    )
    queries = []

    class Client(Context):
        def __init__(self, *a, telemetry, **kw):
            self.telemetry = telemetry

        def embed(self, query):
            queries.append(query)
            self.telemetry.record_response(200, retry=False)
            return EmbeddingResult(tuple([0.1] * 1024), 5, "mock")

    monkeypatch.setattr(cli, "ClovaStudioEmbeddingClient", Client)
    path = tmp_path / "result.json"
    monkeypatch.setattr(
        sys, "argv", ["evaluate_contract_qa.py", "--benchmark-version", "v2", "--report", str(path)]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1  # empty mocked retrieval must not get credited
    result = orjson.loads(path.read_bytes())
    assert result["status"] == "completed" and result["summary"]["tested"] == 40
    assert result["summary"]["passed"] == 8  # 6 genuine refusals and 2 empty scopes only
    assert result["provider"]["http_requests"] == len(queries) == 34
    assert result["comparison"]["unchanged_questions"]["tested"] == 39
    assert result["comparison"]["excluded_changed_question_ids"] == ["P3"]


@pytest.mark.parametrize("mode", ["partial", "interrupt", "failure"])
def test_live_loop_checkpoints_without_claiming_full_pass(mode, tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        cli,
        "runtime_from_args",
        lambda args: SimpleNamespace(database_url="hidden", api_key="secret"),
    )
    engine = SimpleNamespace(connect=lambda: Connection())
    monkeypatch.setattr(cli, "get_engine", lambda url: engine)
    config = {
        "provider": "clova-studio",
        "model": "bge-m3",
        "dimensions": 1024,
        "distance_metric": "cosine",
        "endpoint": "https://example.invalid",
        "input_version": "retrieval-embedding-v2",
    }
    monkeypatch.setattr(cli, "preflight", lambda *a: (config, [], {}, []))
    monkeypatch.setattr(cli, "ClovaStudioEmbeddingClient", lambda *a, **kw: Context())
    calls = []

    def observe(*args):
        calls.append(1)
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "failure":
            raise RuntimeError("SECRET")
        return {"kind": "error", "error_type": "test"}

    monkeypatch.setattr(cli, "observe_request", observe)
    if mode == "partial":
        monkeypatch.setattr(
            cli,
            "score_case",
            lambda case, *a: {
                "id": case["id"],
                "group": case["group"],
                "passed": True,
                "failures": [],
                "retrieval_hit_at_5": True,
                "field_checks": [],
                "observed": {"kind": "fields"},
            },
        )
        monkeypatch.setattr(cli, "observe_request", lambda *a: {"kind": "fields"})
    monkeypatch.setattr(
        sys, "argv", ["evaluate_contract_qa.py", "--limit", "1", "--report", str(report_path)]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    report = orjson.loads(report_path.read_bytes())
    assert not report["summary"]["all_development_checks_passed"]
    assert "SECRET" not in report_path.read_text()
    assert (
        report["status"]
        == {"partial": "partial", "interrupt": "interrupted", "failure": "failed"}[mode]
    )
    assert len(report["results"]) == (0 if mode == "interrupt" else 1)
