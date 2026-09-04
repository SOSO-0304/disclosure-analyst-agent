from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.retrieval import contract_service as service
from disclosure_agent.retrieval.contract_service import (
    ContractQueryConfigurationError,
    ContractQueryOptions,
)
from disclosure_agent.retrieval.embeddings import EmbeddingResult
from disclosure_agent.retrieval.runtime import RetrievalRuntime

PERF = "postgresql+psycopg://user:secret@localhost:55432/disclosure_perf"
RUN = {
    "embedding_run_id": "approved-v2",
    "chunk_run_id": "chunks",
    "provider": "clova-studio",
    "model": "bge-m3",
    "dimensions": 1024,
    "distance_metric": "cosine",
    "endpoint": "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2",
    "input_version": "retrieval-embedding-v2",
}


class Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Connection(Context):
    def __init__(self):
        self.statements: list[str] = []

    def begin(self):
        return Context()

    def execute(self, statement):
        self.statements.append(str(statement))
        return []


class Engine:
    def __init__(self, connection: Connection):
        self.connection = connection

    def connect(self):
        return self.connection


class EmbeddingClient(Context):
    calls: list[str] = []

    def __init__(self, key, config, *, rate_limiter=None):
        assert key == "api-key"
        assert config.input_version == "retrieval-embedding-v2"
        assert rate_limiter is service.QUERY_EMBEDDING_RATE_LIMITER

    def embed(self, question):
        self.calls.append(question)
        return EmbeddingResult(tuple([0.1] * 1024), 7, "provider-request")


@pytest.fixture
def setup(monkeypatch):
    connection = Connection()
    engine = Engine(connection)
    runtime = RetrievalRuntime(PERF, "api-key", Path(".env.perf"))
    EmbeddingClient.calls = []
    monkeypatch.setattr(service, "completed_run", lambda *_: dict(RUN))
    monkeypatch.setattr(
        service,
        "company_catalog",
        lambda *_: [
            {
                "corp_code": "00126478",
                "stock_code": "010140",
                "corp_name": "삼성중공업",
                "listed_name": "삼성중공업",
            }
        ],
    )
    monkeypatch.setattr(service, "ClovaStudioEmbeddingClient", EmbeddingClient)
    return runtime, engine, connection


def test_supported_query_is_read_only_and_embeds_once(setup, monkeypatch):
    runtime, engine, connection = setup
    captured = {}

    def retrieve(_connection, **kwargs):
        captured.update(kwargs)
        return {
            "results": [],
            "dense_strategy": "exact_filtered",
            "candidate_counts": {"dense": 0, "lexical": 0, "overlap": 0, "fused": 0},
            "dense_diagnostics": {"pool_attempts": [], "index_usage": "not_applicable"},
            "timing_seconds": {"total": 0.01},
            "warnings": [],
            "quantity_diagnostics": {"probes": [], "candidate_count": 0},
        }

    monkeypatch.setattr(service, "retrieve", retrieve)
    monkeypatch.setattr(
        service,
        "contract_findings",
        lambda *_args, **_kwargs: {
            "embedding_run_id": "approved-v2",
            "chunk_run_id": "chunks",
            "status": "no_results",
            "database_writes": 0,
            "extraction_provider_calls": 0,
            "findings": [],
            "limitations": [],
        },
    )
    report = service.execute_contract_query(
        runtime,
        ContractQueryOptions(
            "삼성중공업의 2025년 공급계약 금액",
            date_from=date(2025, 1, 1),
            date_to=date(2025, 12, 31),
        ),
        engine=engine,
    )

    assert EmbeddingClient.calls == ["삼성중공업의 2025년 공급계약 금액"]
    assert captured["mode"] == "dense" and captured["candidate_limit"] == 100
    assert captured["filters"]["corp_code"] == "00126478"
    assert captured["filters"]["document_subtype"] == "단일판매공급계약체결"
    assert report["query_provider_calls"] == 1 and report["query_tokens"] == 7
    assert connection.statements.count("SET TRANSACTION READ ONLY") == 2
    assert all("secret" not in statement for statement in connection.statements)


def test_unsupported_query_stops_before_provider_and_search(setup, monkeypatch):
    runtime, engine, connection = setup
    monkeypatch.setattr(service, "retrieve", lambda *_a, **_k: pytest.fail("no search"))
    report = service.execute_contract_query(
        runtime,
        ContractQueryOptions("삼성중공업의 계약금액을 모두 합산해줘"),
        engine=engine,
    )
    assert report["status"] == "unsupported"
    assert report["reason_code"] == "aggregation"
    assert report["query_provider_calls"] == 0 and not EmbeddingClient.calls
    assert connection.statements.count("SET TRANSACTION READ ONLY") == 1


@pytest.mark.parametrize(
    "override",
    [
        {"input_version": "retrieval-embedding-v3"},
        {"provider": "other"},
        {"endpoint": "https://example.invalid/embedding"},
    ],
)
def test_api_rejects_nonapproved_active_embedding_contract(setup, monkeypatch, override):
    runtime, engine, _ = setup
    monkeypatch.setattr(
        service,
        "completed_run",
        lambda *_: {**RUN, **override},
    )
    with pytest.raises(ContractQueryConfigurationError):
        service.execute_contract_query(
            runtime,
            ContractQueryOptions("삼성중공업의 공급계약 금액"),
            engine=engine,
        )
    assert not EmbeddingClient.calls


def test_readiness_checks_database_and_key_without_provider(setup):
    runtime, engine, connection = setup
    report = service.readiness(runtime, engine=engine)
    assert report["status"] == "ready"
    assert report["embedding_run_id"] == "approved-v2"
    assert report["provider_checked"] is False and report["database_writes"] == 0
    assert "SELECT 1" in connection.statements
    assert not EmbeddingClient.calls


def test_readiness_requires_key_before_database(setup):
    _, engine, connection = setup
    runtime = RetrievalRuntime(PERF, "", Path(".env.perf"))
    with pytest.raises(ContractQueryConfigurationError):
        service.readiness(runtime, engine=engine)
    assert connection.statements == []


@pytest.mark.parametrize("top_k", [0, 11])
def test_public_options_are_bounded(top_k):
    with pytest.raises(service.ContractQueryInputError):
        ContractQueryOptions("공급계약", top_k=top_k)
