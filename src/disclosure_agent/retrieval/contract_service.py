"""Application service for the validated four-field supply-contract query path.

The service deliberately exposes only the retrieval contract that passed the v2
development benchmark.  It never mutates the Source Layer, chunks, or embeddings.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, text

from disclosure_agent.retrieval.contract_answers import contract_findings
from disclosure_agent.retrieval.contract_query import plan_contract_query, stopped_contract_answer
from disclosure_agent.retrieval.embeddings import (
    DEFAULT_ENDPOINT,
    DEFAULT_PROVIDER,
    DEFAULT_STARTUP_QPM,
    DEFAULT_TARGET_QPM,
    EMBEDDING_INPUT_VERSION_V2,
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    GlobalRateLimiter,
    vector_literal,
)
from disclosure_agent.retrieval.hybrid import resolve_company
from disclosure_agent.retrieval.runtime import RetrievalRuntime
from disclosure_agent.retrieval.search import company_catalog, completed_run, retrieve
from disclosure_agent.storage.database import get_engine

API_RETRIEVAL_MODE = "dense"
API_CANDIDATE_LIMIT = 100
API_STATEMENT_TIMEOUT_SECONDS = 60

# The deployment contract runs exactly one API process. All request threads share
# this limiter, keeping aggregate query embedding traffic below the approved 540 QPM
# service quota. Do not scale processes/replicas without an external distributed limiter.
QUERY_EMBEDDING_RATE_LIMITER = GlobalRateLimiter(
    target_qpm=DEFAULT_TARGET_QPM,
    startup_qpm=DEFAULT_STARTUP_QPM,
)


class ContractQueryInputError(ValueError):
    """A user-correctable company or scope error."""


class ContractQueryConfigurationError(RuntimeError):
    """The selected database or embedding run cannot serve this API contract."""


@dataclass(frozen=True, slots=True)
class ContractQueryOptions:
    """Bounded public options; retrieval internals are intentionally not exposed."""

    question: str
    company: str | None = None
    corp_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    corrections: str = "all"
    top_k: int = 5

    def __post_init__(self) -> None:
        if not self.question.strip() or len(self.question) > 10_000:
            raise ContractQueryInputError("질문은 1~10,000자로 입력해 주세요.")
        if not 1 <= self.top_k <= 10:
            raise ContractQueryInputError("top_k는 1~10 범위여야 합니다.")
        if self.corrections not in {"all", "only", "exclude"}:
            raise ContractQueryInputError("정정공시 조건을 확인해 주세요.")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ContractQueryInputError("접수일 시작이 종료보다 늦습니다.")


@contextmanager
def readonly_connection(
    engine: Engine,
    *,
    timeout_seconds: int = API_STATEMENT_TIMEOUT_SECONDS,
):
    """Open a transaction that PostgreSQL itself enforces as read-only."""

    if not 1 <= timeout_seconds <= API_STATEMENT_TIMEOUT_SECONDS:
        raise ValueError("Read-only statement timeout is outside the allowed range")
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text(f"SET LOCAL statement_timeout = '{timeout_seconds}s'"))
        yield connection


def _validated_run(connection: Connection) -> dict[str, Any]:
    run = completed_run(connection, None)
    if (
        str(run.get("provider")) != DEFAULT_PROVIDER
        or str(run.get("endpoint", "")).rstrip("/") != DEFAULT_ENDPOINT.rstrip("/")
        or str(run.get("input_version")) != EMBEDDING_INPUT_VERSION_V2
    ):
        raise ContractQueryConfigurationError(
            "The active embedding run does not match the approved API contract"
        )
    return run


def readiness(runtime: RetrievalRuntime, *, engine: Engine | None = None) -> dict[str, Any]:
    """Check local configuration and the approved active run without provider calls."""

    if not runtime.api_key:
        raise ContractQueryConfigurationError("CLOVA Studio API key is not configured")
    target = engine or get_engine(runtime.database_url)
    with readonly_connection(target, timeout_seconds=5) as connection:
        run = _validated_run(connection)
        connection.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "embedding_run_id": str(run["embedding_run_id"]),
        "chunk_run_id": str(run["chunk_run_id"]),
        "model": str(run["model"]),
        "input_version": str(run["input_version"]),
        "provider_checked": False,
        "database_writes": 0,
    }


def execute_contract_query(
    runtime: RetrievalRuntime,
    options: ContractQueryOptions,
    *,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Plan, embed, retrieve, and extract one bounded contract question."""

    started = time.monotonic()
    target = engine or get_engine(runtime.database_url)
    with readonly_connection(target) as connection:
        run = _validated_run(connection)
        try:
            company = resolve_company(
                company_catalog(connection),
                options.question,
                company=options.company,
                corp_code=options.corp_code,
                auto=True,
            )
        except ValueError as exc:
            raise ContractQueryInputError(str(exc)) from None

    filters = {
        "corp_code": str(company["corp_code"]) if company else None,
        "date_from": options.date_from,
        "date_to": options.date_to,
        "document_group": None,
        "chunk_type": None,
        "corrections": options.corrections,
    }
    plan = plan_contract_query(options.question, filters)
    if plan["status"] != "ready":
        answer = stopped_contract_answer(plan, run)
        answer["timing_seconds"] = {
            "query_api": 0.0,
            "search": 0.0,
            "extraction": 0.0,
            "total": time.monotonic() - started,
        }
        return answer

    if not runtime.api_key:
        raise ContractQueryConfigurationError("CLOVA Studio API key is not configured")

    filters = plan["filters"]
    config = EmbeddingConfig(
        provider=str(run["provider"]),
        model=str(run["model"]),
        dimensions=int(run["dimensions"]),
        distance_metric=str(run["distance_metric"]),
        endpoint=str(run["endpoint"]),
        input_version=str(run["input_version"]),
    )
    api_started = time.monotonic()
    with ClovaStudioEmbeddingClient(
        runtime.api_key,
        config,
        rate_limiter=QUERY_EMBEDDING_RATE_LIMITER,
    ) as client:
        embedding = client.embed(options.question)
    api_seconds = time.monotonic() - api_started

    search_started = time.monotonic()
    with readonly_connection(target) as connection:
        payload = retrieve(
            connection,
            run=run,
            query=options.question,
            vector=vector_literal(embedding.vector),
            mode=API_RETRIEVAL_MODE,
            top_k=options.top_k,
            candidate_limit=API_CANDIDATE_LIMIT,
            exact=False,
            max_per_filing=1,
            company_cap=2,
            filters=filters,
            quantity_probes=plan["quantity_probes"],
        )
        extraction_started = time.monotonic()
        answer = contract_findings(
            connection,
            run=run,
            hits=payload["results"],
            filters=filters,
        )
        extraction_seconds = time.monotonic() - extraction_started
    search_seconds = time.monotonic() - search_started - extraction_seconds

    answer.update(
        schema_version="retrieval-contract-fields-v2",
        query_plan=plan,
        query_provider_calls=1,
        query_tokens=embedding.input_tokens or 0,
        retrieval={
            "mode": API_RETRIEVAL_MODE,
            "dense_strategy": payload["dense_strategy"],
            "filters": filters,
            "top_k": options.top_k,
            "candidate_counts": payload["candidate_counts"],
            "dense_diagnostics": payload["dense_diagnostics"],
            "timing_seconds": payload["timing_seconds"],
            "warnings": payload["warnings"],
            "quantity_diagnostics": payload.get("quantity_diagnostics", {}),
        },
        timing_seconds={
            "query_api": api_seconds,
            "search": max(0.0, search_seconds),
            "extraction": extraction_seconds,
            "total": time.monotonic() - started,
        },
    )
    return answer
