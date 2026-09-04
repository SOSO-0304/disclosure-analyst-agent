from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from disclosure_agent.api.main import create_app
from disclosure_agent.retrieval.contract_service import ContractQueryInputError
from disclosure_agent.retrieval.runtime import RetrievalRuntime

PERF = "postgresql+psycopg://user:secret@localhost:55432/disclosure_perf"
RUNTIME = RetrievalRuntime(PERF, "api-key", Path(".env.perf"))


def report(status="completed"):
    citation = {
        "receipt_number": "20250707800058",
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250707800058",
        "url_status": "constructed_from_receipt",
        "filing_id": "exchange_20250707800058",
        "document_id": "exchange_20250707800058:primary_report",
        "section_id": "section-1",
        "chunk_id": "chunk-1",
        "source_table_id": "table-1",
        "source_block_ids": ["block-1"],
        "correction_status": "non_correction",
        "lineage_status": "not_resolved",
    }
    return {
        "schema_version": "retrieval-contract-fields-v2",
        "embedding_run_id": "approved-v2",
        "chunk_run_id": "chunks",
        "status": status,
        "reason_code": None,
        "reason": None,
        "query_plan": {
            "status": "ready",
            "notes": [],
            "filters": {"corp_code": "00126478", "corrections": "all"},
        },
        "findings": [
            {
                "company_name": "삼성중공업",
                "report_name": "단일판매ㆍ공급계약체결",
                "is_correction": False,
                "status": "extracted",
                "fields": {},
                "table_id": "table-1",
                "citation": citation,
                "warnings": [],
            }
        ],
        "limitations": ["정정·해지 연결은 적용하지 않습니다."],
        "retrieval": {
            "mode": "dense",
            "filters": {"corp_code": "00126478", "corrections": "all"},
            "candidate_counts": {"dense": 100},
        },
        "database_writes": 0,
        "extraction_provider_calls": 0,
        "query_provider_calls": 1,
        "query_tokens": 11,
        "timing_seconds": {"query_api": 0.1, "search": 0.2, "total": 0.3},
    }


def client(
    *,
    executor=lambda *_: report(),
    loader=lambda: RUNTIME,
    checker=None,
    access_token_loader=lambda: "",
):
    checker = checker or (
        lambda _runtime: {
            "status": "ready",
            "embedding_run_id": "approved-v2",
            "chunk_run_id": "chunks",
            "model": "bge-m3",
            "input_version": "retrieval-embedding-v2",
            "provider_checked": False,
            "database_writes": 0,
        }
    )
    return TestClient(
        create_app(
            runtime_loader=loader,
            query_executor=executor,
            readiness_checker=checker,
            access_token_loader=access_token_loader,
        )
    )


def test_liveness_has_request_id_and_does_not_load_configuration():
    response = client(loader=lambda: (_ for _ in ()).throw(AssertionError("no config"))).get(
        "/health/live"
    )
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


def test_readiness_returns_only_sanitized_run_identity():
    response = client().get("/health/ready")
    assert response.status_code == 200
    assert response.json()["embedding_run_id"] == "approved-v2"
    assert response.json()["provider_checked"] is False
    assert "api-key" not in response.text and "secret" not in response.text


def test_query_returns_stable_evidence_contract_and_ignores_no_fields():
    captured = {}

    def executor(runtime, options):
        captured.update(runtime=runtime, options=options)
        return report()

    response = client(executor=executor).post(
        "/v1/query",
        json={
            "question": " 삼성중공업의 2025년 공급계약 금액 ",
            "company": "삼성중공업",
            "date_from": "2025-01-01",
            "date_to": "2025-12-31",
            "top_k": 5,
        },
    )
    body = response.json()
    assert response.status_code == 200 and body["status"] == "completed"
    assert body["schema_version"] == "contract-query-api-v1"
    assert body["run"] == {"embedding_run_id": "approved-v2", "chunk_run_id": "chunks"}
    assert body["usage"]["database_writes"] == 0
    assert len(body["citations"]) == 1 and body["citations"][0]["chunk_id"] == "chunk-1"
    assert captured["options"].question == "삼성중공업의 2025년 공급계약 금액"
    assert captured["options"].date_from.isoformat() == "2025-01-01"
    assert "api-key" not in response.text and "secret" not in response.text


def test_invalid_body_is_generic_and_does_not_call_runtime_or_executor():
    response = client(
        loader=lambda: (_ for _ in ()).throw(AssertionError("no runtime")),
        executor=lambda *_: (_ for _ in ()).throw(AssertionError("no query")),
    ).post(
        "/v1/query",
        json={"question": " ", "top_k": 999, "unexpected": "do not echo this"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_request"
    assert "do not echo this" not in response.text


def test_unknown_company_becomes_clarification_without_secret_leak():
    def executor(*_args):
        raise ContractQueryInputError("Company not found; use an exact company name")

    response = client(executor=executor).post(
        "/v1/query", json={"question": "모르는회사의 공급계약"}
    )
    assert response.status_code == 400
    assert response.json()["status"] == "clarification_required"
    assert response.json()["error_code"] == "query_needs_clarification"


def test_missing_configuration_is_sanitized():
    response = client(
        loader=lambda: (_ for _ in ()).throw(ValueError("postgresql://user:secret@remote/db"))
    ).post("/v1/query", json={"question": "삼성중공업의 공급계약"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "service_not_configured"
    assert "secret" not in response.text and "remote" not in response.text


def test_extra_retrieval_controls_are_rejected():
    response = client().post(
        "/v1/query",
        json={
            "question": "삼성중공업의 공급계약",
            "embedding_run_id": "unapproved",
            "mode": "lexical",
        },
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_request"


def test_configured_access_token_is_required_and_never_echoed():
    guarded = client(
        executor=lambda *_: report(),
        access_token_loader=lambda: "server-secret-token",
    )

    missing = guarded.post("/v1/query", json={"question": "삼성중공업의 공급계약"})
    wrong = guarded.post(
        "/v1/query",
        json={"question": "삼성중공업의 공급계약"},
        headers={"Authorization": "Bearer wrong-client-token"},
    )
    allowed = guarded.post(
        "/v1/query",
        json={"question": "삼성중공업의 공급계약"},
        headers={"Authorization": "Bearer server-secret-token"},
    )

    assert missing.status_code == 401 and wrong.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert missing.json()["error_code"] == "invalid_access_token"
    assert allowed.status_code == 200
    assert "server-secret-token" not in missing.text + wrong.text + allowed.text


def test_health_endpoints_do_not_require_access_token():
    guarded = client(access_token_loader=lambda: "server-secret-token")
    assert guarded.get("/health/live").status_code == 200
    assert guarded.get("/health/ready").status_code == 200
