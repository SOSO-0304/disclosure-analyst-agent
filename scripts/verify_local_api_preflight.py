#!/usr/bin/env python
"""Exercise the containerized API against the verified local perf database."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text

from disclosure_agent.retrieval.runtime import assert_perf_database

APPROVED_EMBEDDING_RUN_ID = "1220f708cc241e9a00d0b40cbddea7da"
APPROVED_CHUNK_RUN_ID = "c700b5a3a6cfa055be147bc71dd478ca"
APPROVED_INPUT_VERSION = "retrieval-embedding-v2"

QUESTIONS = (
    {
        "question": "삼성중공업의 단일판매 공급계약 상대방과 계약금액, 계약기간",
        "company": "삼성중공업",
        "top_k": 5,
    },
    {
        "question": "HD현대일렉트릭의 배전변압기 3500대 계약 상대방과 금액, 기간은?",
        "company": "HD현대일렉트릭",
        "top_k": 5,
    },
    {
        "question": "HMM이 2023년 3월 17일 공시한 장기대선계약의 금액과 계약기간은?",
        "company": "HMM",
        "top_k": 5,
    },
    {
        "question": "한국항공우주의 말레이시아 FA-50 계약 상대방과 계약금액, 기간은?",
        "company": "한국항공우주",
        "top_k": 5,
    },
)


class PreflightFailure(RuntimeError):
    """A named local-deployment gate did not pass."""


@dataclass(frozen=True, slots=True)
class CallResult:
    elapsed_seconds: float
    status: str
    citations: int
    provider_calls: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreflightFailure(message)


def _database_snapshot(database_url: str) -> dict[str, Any]:
    assert_perf_database(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                connection.execute(
                    text(
                        """
                        SELECT
                            (SELECT count(*) FROM retrieval_chunks
                             WHERE chunk_run_id = :chunk_run_id) AS chunks,
                            (SELECT count(*) FROM retrieval_embeddings
                             WHERE embedding_run_id = :embedding_run_id) AS embeddings,
                            (SELECT count(*) FROM embedding_runs WHERE is_active) AS active_runs,
                            (SELECT status FROM embedding_runs
                             WHERE embedding_run_id = :embedding_run_id) AS run_status
                        """
                    ),
                    {
                        "chunk_run_id": APPROVED_CHUNK_RUN_ID,
                        "embedding_run_id": APPROVED_EMBEDDING_RUN_ID,
                    },
                )
                .mappings()
                .one()
            )
        return {
            "chunks": int(row["chunks"]),
            "embeddings": int(row["embeddings"]),
            "active_runs": int(row["active_runs"]),
            "run_status": str(row["run_status"]),
        }
    finally:
        engine.dispose()


def _validate_answer(response: httpx.Response) -> CallResult:
    _require(response.status_code == 200, f"supported query returned HTTP {response.status_code}")
    payload = response.json()
    _require(payload.get("status") in {"completed", "partial"}, "supported query did not answer")
    _require(
        payload.get("run", {}).get("embedding_run_id") == APPROVED_EMBEDDING_RUN_ID,
        "wrong embedding run",
    )
    _require(payload.get("run", {}).get("chunk_run_id") == APPROVED_CHUNK_RUN_ID, "wrong chunk run")
    _require(payload.get("usage", {}).get("database_writes") == 0, "API reported a database write")
    _require(
        payload.get("usage", {}).get("embedding_provider_calls") == 1,
        "query embedding call count is not one",
    )
    citations = payload.get("citations") or []
    _require(bool(citations), "supported answer has no citation")
    _require(
        all(citation.get("filing_id") and citation.get("chunk_id") for citation in citations),
        "citation provenance is incomplete",
    )
    return CallResult(
        elapsed_seconds=response.elapsed.total_seconds(),
        status=str(payload["status"]),
        citations=len(citations),
        provider_calls=int(payload["usage"]["embedding_provider_calls"]),
    )


def _query(client: httpx.Client, payload: dict[str, Any], token: str) -> CallResult:
    return _validate_answer(
        client.post(
            "/v1/query",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    )


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95 + 0.999999)))
    return ordered[index]


def run_preflight(
    *,
    base_url: str,
    database_url: str,
    token: str,
    load_requests: int,
    concurrency: int,
) -> dict[str, Any]:
    _require(bool(token), "client access token is empty")
    before = _database_snapshot(database_url)
    _require(before["chunks"] == 178_822, "approved chunk coverage is not 178822")
    _require(before["embeddings"] == 178_822, "approved embedding coverage is not 178822")
    _require(
        before["active_runs"] == 1 and before["run_status"] == "completed", "active run is invalid"
    )

    checks: dict[str, Any] = {}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=75.0) as client:
        live = client.get("/health/live")
        _require(live.status_code == 200 and live.json() == {"status": "ok"}, "liveness failed")
        checks["liveness"] = "passed"

        ready = client.get("/health/ready")
        ready_payload = ready.json()
        _require(
            ready.status_code == 200 and ready_payload.get("status") == "ready", "readiness failed"
        )
        _require(
            ready_payload.get("embedding_run_id") == APPROVED_EMBEDDING_RUN_ID,
            "readiness selected the wrong embedding run",
        )
        _require(
            ready_payload.get("chunk_run_id") == APPROVED_CHUNK_RUN_ID,
            "readiness selected the wrong chunk run",
        )
        _require(
            ready_payload.get("input_version") == APPROVED_INPUT_VERSION,
            "readiness selected the wrong input version",
        )
        checks["readiness"] = "passed"

        unauthorized = client.post("/v1/query", json=QUESTIONS[0])
        _require(unauthorized.status_code == 401, "missing bearer token was not rejected")
        _require(
            unauthorized.json().get("error_code") == "invalid_access_token",
            "wrong authentication error contract",
        )
        checks["authentication"] = "passed"

        invalid = client.post(
            "/v1/query",
            json={"question": " ", "top_k": 999},
            headers={"Authorization": f"Bearer {token}"},
        )
        _require(invalid.status_code == 422, "invalid request was not rejected")
        checks["input_validation"] = "passed"

        unsupported = client.post(
            "/v1/query",
            json={"question": "삼성중공업의 모든 공급계약 금액을 합산해줘"},
            headers={"Authorization": f"Bearer {token}"},
        )
        _require(unsupported.status_code == 200, "unsupported request did not return safely")
        unsupported_payload = unsupported.json()
        _require(unsupported_payload.get("status") == "unsupported", "aggregation was not rejected")
        _require(
            unsupported_payload.get("usage", {}).get("embedding_provider_calls") == 0,
            "unsupported request called provider",
        )
        checks["unsupported_short_circuit"] = "passed"

        smoke = _query(client, QUESTIONS[0], token)
        checks["real_query"] = {
            "status": smoke.status,
            "citations": smoke.citations,
            "elapsed_seconds": round(smoke.elapsed_seconds, 6),
        }

    load_results: list[CallResult] = []
    if load_requests:
        started = time.monotonic()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=75.0) as client:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(_query, client, QUESTIONS[index % len(QUESTIONS)], token)
                    for index in range(load_requests)
                ]
                for future in as_completed(futures):
                    load_results.append(future.result())
        wall_seconds = time.monotonic() - started
        latencies = [result.elapsed_seconds for result in load_results]
        _require(len(load_results) == load_requests, "concurrent request count mismatch")
        _require(max(latencies) < 75.0, "a concurrent request exceeded the 75 second gate")
        checks["concurrency"] = {
            "requests": load_requests,
            "workers": concurrency,
            "failures": 0,
            "wall_seconds": round(wall_seconds, 6),
            "requests_per_second": round(load_requests / wall_seconds, 6),
            "latency_median_seconds": round(statistics.median(latencies), 6),
            "latency_p95_seconds": round(_percentile_95(latencies), 6),
        }
    else:
        checks["concurrency"] = {"requests": 0, "skipped": True}

    after = _database_snapshot(database_url)
    _require(after == before, "database contract changed during API preflight")
    checks["database_unchanged"] = "passed"
    return {
        "schema_version": "local-api-preflight-v1",
        "status": "verified",
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "run": {
            "embedding_run_id": APPROVED_EMBEDDING_RUN_ID,
            "chunk_run_id": APPROVED_CHUNK_RUN_ID,
            "input_version": APPROVED_INPUT_VERSION,
        },
        "database_before": before,
        "database_after": after,
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--token-env", default="DISCLOSURE_API_TOKEN")
    parser.add_argument("--load-requests", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.load_requests < 0 or args.concurrency <= 0:
        parser.error("load-requests must be non-negative and concurrency must be positive")
    token = os.environ.get(args.token_env, "")
    try:
        report = run_preflight(
            base_url=args.base_url,
            database_url=args.database_url,
            token=token,
            load_requests=args.load_requests,
            concurrency=args.concurrency,
        )
    except (PreflightFailure, httpx.HTTPError, OSError, ValueError) as exc:
        print("status                         blocked")
        print(f"failure type                   {type(exc).__name__}")
        if isinstance(exc, PreflightFailure):
            print(f"failure                        {exc}")
        return 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print("=== local API deployment preflight ===")
    print(f"embedding run                  {APPROVED_EMBEDDING_RUN_ID}")
    print(f"chunk run                      {APPROVED_CHUNK_RUN_ID}")
    print(f"embeddings                     {report['database_after']['embeddings']}")
    print(f"concurrent requests            {report['checks']['concurrency']['requests']}")
    print(f"concurrent failures            {report['checks']['concurrency'].get('failures', 0)}")
    if args.report:
        print(f"report                         {args.report}")
    print("database unchanged             OK")
    print("status                         verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
