"""FastAPI application for the approved supply-contract retrieval slice."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from disclosure_agent.api.schemas import (
    ContractQueryRequest,
    ContractQueryResponse,
    ErrorResponse,
    LiveResponse,
    ReadyResponse,
    RunIdentity,
    Usage,
)
from disclosure_agent.retrieval.contract_answers import render_contract_findings
from disclosure_agent.retrieval.contract_service import (
    ContractQueryConfigurationError,
    ContractQueryInputError,
    ContractQueryOptions,
    execute_contract_query,
    readiness,
)
from disclosure_agent.retrieval.runtime import RetrievalRuntime, load_api_runtime

RuntimeLoader = Callable[[], RetrievalRuntime]
QueryExecutor = Callable[[RetrievalRuntime, ContractQueryOptions], dict[str, Any]]
ReadinessChecker = Callable[[RetrievalRuntime], dict[str, Any]]
AccessTokenLoader = Callable[[], str]


def load_access_token() -> str:
    """Read the optional client-facing bearer token without dotenv discovery."""

    return os.environ.get("DISCLOSURE_API_TOKEN", "").strip()


def _authorized(request: Request, expected_token: str) -> bool:
    if not expected_token:
        return True
    scheme, separator, supplied = request.headers.get("Authorization", "").partition(" ")
    return (
        bool(separator)
        and scheme.casefold() == "bearer"
        and secrets.compare_digest(supplied.strip(), expected_token)
    )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", uuid4().hex))


def _error(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    message: str,
    status: str = "error",
) -> JSONResponse:
    body = ErrorResponse(
        request_id=_request_id(request),
        status=status,
        error_code=error_code,
        message=message,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _citations(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in report.get("findings", []):
        citation = finding.get("citation") or {}
        key = (str(citation.get("filing_id") or ""), str(citation.get("chunk_id") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        values.append(dict(citation))
    return values


def response_from_report(request_id: str, report: Mapping[str, Any]) -> ContractQueryResponse:
    """Project the internal evidence report onto the stable public schema."""

    query_plan = dict(report.get("query_plan") or {})
    retrieval = report.get("retrieval")
    applied_filters = dict((retrieval or {}).get("filters") or query_plan.get("filters") or {})
    return ContractQueryResponse(
        request_id=request_id,
        status=str(report["status"]),
        answer=render_contract_findings(report),
        reason_code=report.get("reason_code"),
        reason=report.get("reason"),
        interpreted_query=query_plan,
        applied_filters=applied_filters,
        findings=list(report.get("findings") or []),
        citations=_citations(report),
        limitations=list(report.get("limitations") or []),
        retrieval=dict(retrieval) if retrieval is not None else None,
        run=RunIdentity(
            embedding_run_id=str(report["embedding_run_id"]),
            chunk_run_id=str(report["chunk_run_id"]),
        ),
        usage=Usage(
            query_tokens=int(report.get("query_tokens") or 0),
            embedding_provider_calls=int(report.get("query_provider_calls") or 0),
            extraction_provider_calls=int(report.get("extraction_provider_calls") or 0),
            database_writes=0,
        ),
        timing_seconds={
            str(key): float(value) for key, value in (report.get("timing_seconds") or {}).items()
        },
    )


def create_app(
    *,
    runtime_loader: RuntimeLoader = load_api_runtime,
    query_executor: QueryExecutor = execute_contract_query,
    readiness_checker: ReadinessChecker = readiness,
    access_token_loader: AccessTokenLoader = load_access_token,
) -> FastAPI:
    app = FastAPI(
        title="Disclosure Analyst Agent",
        version="0.1.0",
        description=(
            "검증된 단일판매·공급계약 검색 범위에서 계약상대방, 계약금액, "
            "시작일, 종료일과 원문 근거를 반환하는 읽기 전용 API"
        ),
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        return _error(
            request,
            status_code=422,
            error_code="invalid_request",
            message="요청 형식과 입력 범위를 확인해 주세요.",
            status="clarification_required",
        )

    @app.get("/health/live", response_model=LiveResponse, tags=["health"])
    def live() -> LiveResponse:
        return LiveResponse()

    @app.get(
        "/health/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["health"],
    )
    def ready(request: Request):
        try:
            runtime = runtime_loader()
            return ReadyResponse.model_validate(readiness_checker(runtime))
        except (ValueError, ContractQueryConfigurationError, SQLAlchemyError):
            return _error(
                request,
                status_code=503,
                error_code="service_not_ready",
                message="검색 서비스 설정 또는 데이터베이스 상태를 확인해 주세요.",
            )

    @app.post(
        "/v1/query",
        response_model=ContractQueryResponse,
        responses={
            401: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
        },
        tags=["query"],
    )
    def query(payload: ContractQueryRequest, request: Request):
        if not _authorized(request, access_token_loader()):
            response = _error(
                request,
                status_code=401,
                error_code="invalid_access_token",
                message="유효한 API 접근 토큰이 필요합니다.",
            )
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        try:
            runtime = runtime_loader()
        except ValueError:
            return _error(
                request,
                status_code=503,
                error_code="service_not_configured",
                message="검색 서비스 환경 설정을 확인해 주세요.",
            )
        try:
            report = query_executor(
                runtime,
                ContractQueryOptions(
                    question=payload.question,
                    company=payload.company,
                    corp_code=payload.corp_code,
                    date_from=payload.date_from,
                    date_to=payload.date_to,
                    corrections=payload.corrections,
                    top_k=payload.top_k,
                ),
            )
            return response_from_report(_request_id(request), report)
        except ContractQueryInputError as exc:
            return _error(
                request,
                status_code=400,
                error_code="query_needs_clarification",
                message=str(exc),
                status="clarification_required",
            )
        except ContractQueryConfigurationError:
            return _error(
                request,
                status_code=503,
                error_code="service_not_ready",
                message="승인된 검색 실행 또는 API 키 설정을 확인해 주세요.",
            )
        except httpx.TimeoutException:
            return _error(
                request,
                status_code=504,
                error_code="embedding_timeout",
                message="질문 임베딩 서비스가 제한 시간 내 응답하지 않았습니다.",
            )
        except httpx.HTTPStatusError as exc:
            code = (
                "embedding_rate_limited" if exc.response.status_code == 429 else "embedding_error"
            )
            return _error(
                request,
                status_code=503 if exc.response.status_code == 429 else 502,
                error_code=code,
                message="질문 임베딩 서비스를 일시적으로 사용할 수 없습니다.",
            )
        except (httpx.TransportError, RuntimeError):
            return _error(
                request,
                status_code=502,
                error_code="embedding_error",
                message="질문 임베딩 서비스를 일시적으로 사용할 수 없습니다.",
            )
        except SQLAlchemyError:
            return _error(
                request,
                status_code=503,
                error_code="database_unavailable",
                message="검색 데이터베이스를 일시적으로 사용할 수 없습니다.",
            )
        except (KeyError, TypeError, ValueError):
            return _error(
                request,
                status_code=503,
                error_code="retrieval_contract_error",
                message="검색 응답 계약 검증에 실패했습니다.",
            )

    return app


app = create_app()
