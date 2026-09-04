from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from disclosure_agent.api.main import (
    _render_api_answer,
    _render_execution_trace,
    _render_retrieved_context,
    app,
)
from disclosure_agent.retrieval.answer_query_planner import plan_answer_query
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.retrieval.source_references import SourceReference
from disclosure_agent.services.answer_service import AnswerResult


def _item() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="semantic:1",
        source_kind="semantic_chunk",
        rank=1,
        score=1.0,
        semantic_score=1.0,
        lexical_score=1.0,
        company_name="삼성전자",
        filing_id="filing:1",
        report_name="사업보고서 (2025.12)",
        document_id="document:1",
        section_id="section:1",
        content_text="HBM 판매를 확대했습니다.",
        truncated=False,
        matched_terms=("HBM",),
        block_ids=(),
        table_ids=(),
    )


def test_render_retrieved_context_keeps_evidence_label_and_text() -> None:
    item = _item()
    pack = EvidencePack(
        query="질문",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )

    rendered = _render_retrieved_context(pack)

    assert "[E1] 삼성전자 | 사업보고서 (2025.12)" in rendered
    assert "HBM 판매를 확대했습니다." in rendered


def test_render_api_answer_appends_source_disclosure() -> None:
    item = _item()
    pack = EvidencePack(
        query="질문",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )
    result = AnswerResult(
        query=pack.query,
        plan=plan_answer_query("삼성전자의 2025년 AI 전략을 설명해줘"),
        status="ANSWERABLE",
        answer="근거 기반 답변 [E1]",
        generator="HCX-007",
        evidence_pack=pack,
        source_references=(
            SourceReference(
                filing_id="filing:1",
                report_name="사업보고서 (2025.12)",
                receipt_date=date(2026, 3, 10),
                evidence_labels=("E1",),
            ),
        ),
    )

    rendered = _render_api_answer(result)

    assert "근거 기반 답변 [E1]" in rendered
    assert "근거 공시" in rendered
    assert "사업보고서 (2025.12) | 공시일: 2026-03-10" in rendered


def test_execution_trace_exposes_high_level_route_not_private_reasoning() -> None:
    item = _item()
    pack = EvidencePack(
        query="삼성전자의 2025년 AI 전략을 설명해줘",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )
    plan = plan_answer_query(pack.query)
    result = AnswerResult(
        query=pack.query,
        plan=plan,
        status="ANSWERABLE",
        answer="답변 [E1]",
        generator="HCX-007",
        evidence_pack=pack,
        source_references=(),
    )

    trace = _render_execution_trace(result)

    assert "mode=hybrid_grounded" in trace
    assert "evidence_count=1" in trace
    assert "generator=HCX-007" in trace


def test_answer_endpoint_matches_festival_schema(monkeypatch) -> None:
    item = _item()
    pack = EvidencePack(
        query="삼성전자의 2025년 AI 전략을 설명해줘",
        retrieval_status="MATCHES_FOUND",
        items=(item,),
        total_chars=len(item.content_text),
    )
    result = AnswerResult(
        query=pack.query,
        plan=plan_answer_query(pack.query),
        status="ANSWERABLE",
        answer="근거 기반 답변 [E1]",
        generator="HCX-007",
        evidence_pack=pack,
        source_references=(
            SourceReference(
                filing_id="filing:1",
                report_name="사업보고서 (2025.12)",
                receipt_date=date(2026, 3, 10),
                evidence_labels=("E1",),
            ),
        ),
    )

    class FakeSessionScope:
        def __enter__(self):
            return Mock()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("disclosure_agent.api.main.session_scope", lambda engine: FakeSessionScope())
    monkeypatch.setattr("disclosure_agent.api.main.get_engine", lambda url: SimpleNamespace())
    monkeypatch.setattr(
        "disclosure_agent.api.main.AnswerService.answer",
        lambda self, question: result,
    )

    client = TestClient(app)
    response = client.get(
        "/answer",
        params={"question_id": "Q-001", "question": pack.query},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    assert payload["question_id"] == "Q-001"
    assert payload["question"] == pack.query
    assert "근거 기반 답변 [E1]" in payload["answer"]
    assert "근거 공시" in payload["answer"]
    assert "사업보고서 (2025.12)" in payload["answer"]
