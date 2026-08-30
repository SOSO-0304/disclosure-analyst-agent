import pytest

from disclosure_agent.llm.hcx_client import parse_hcx_payload


def test_parse_hcx_payload_accepts_documented_shape() -> None:
    payload = {
        "status": {"code": "20000", "message": "OK"},
        "result": {
            "message": {"role": "assistant", "content": "매출액은 100원입니다. [E1]"},
            "finishReason": "stop",
            "usage": {
                "promptTokens": 100,
                "completionTokens": 20,
                "totalTokens": 120,
            },
        },
    }

    result = parse_hcx_payload(payload)

    assert result.content == "매출액은 100원입니다. [E1]"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.total_tokens == 120


def test_parse_hcx_payload_rejects_missing_content() -> None:
    payload = {
        "status": {"code": "20000", "message": "OK"},
        "result": {"message": {"role": "assistant", "content": ""}},
    }

    with pytest.raises(RuntimeError, match="missing answer content"):
        parse_hcx_payload(payload)


def test_parse_hcx_payload_rejects_failed_status() -> None:
    payload = {
        "status": {"code": "40000", "message": "Bad request"},
        "result": None,
    }

    with pytest.raises(RuntimeError, match="HCX request failed"):
        parse_hcx_payload(payload)
