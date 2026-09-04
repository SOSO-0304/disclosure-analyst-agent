from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from scripts.verify_local_api_preflight import (
    APPROVED_CHUNK_RUN_ID,
    APPROVED_EMBEDDING_RUN_ID,
    PreflightFailure,
    _percentile_95,
    _validate_answer,
)


def response(payload, status_code=200):
    value = httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "http://127.0.0.1:18000/v1/query"),
    )
    value._elapsed = timedelta(seconds=0.25)
    return value


def valid_payload():
    return {
        "status": "completed",
        "run": {
            "embedding_run_id": APPROVED_EMBEDDING_RUN_ID,
            "chunk_run_id": APPROVED_CHUNK_RUN_ID,
        },
        "usage": {"database_writes": 0, "embedding_provider_calls": 1},
        "citations": [{"filing_id": "exchange_1", "chunk_id": "chunk_1"}],
    }


def test_validate_answer_accepts_only_approved_evidence_contract():
    result = _validate_answer(response(valid_payload()))
    assert result.status == "completed"
    assert result.citations == 1 and result.provider_calls == 1
    assert result.elapsed_seconds == 0.25


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["run"].update(embedding_run_id="wrong"),
        lambda value: value["usage"].update(database_writes=1),
        lambda value: value.update(citations=[]),
        lambda value: value["usage"].update(embedding_provider_calls=2),
    ],
)
def test_validate_answer_blocks_contract_regressions(mutation):
    payload = valid_payload()
    mutation(payload)
    with pytest.raises(PreflightFailure):
        _validate_answer(response(payload))


def test_percentile_95_uses_observed_upper_tail():
    assert _percentile_95([float(value) for value in range(1, 21)]) == 20.0
