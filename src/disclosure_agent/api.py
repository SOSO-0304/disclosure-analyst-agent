"""FastAPI application for the festival evaluation endpoint."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from disclosure_agent.config import get_settings
from disclosure_agent.retrieval.evidence_pack import EvidencePack
from disclosure_agent.services.answer_service import AnswerResult, AnswerService
from disclosure_agent.storage.database import get_engine, session_scope

app = FastAPI(
    title="Disclosure Analyst Agent",
    version="0.1.0",
    description="Grounded disclosure analysis API for the 2026 Mirae Asset AI Festival.",
)


class AnswerResponse(BaseModel):
    """Evaluation response schema required by the festival."""

    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


class HealthResponse(BaseModel):
    status: str
    database: str
    hcx_configured: bool


def _render_retrieved_context(pack: EvidencePack) -> str:
    if not pack.items:
        return "검색된 근거 문서 없음"

    blocks: list[str] = []
    for item in pack.items:
        blocks.append(
            "\n".join(
                (
                    f"[E{item.rank}] {item.company_name} | {item.report_name}",
                    item.content_text,
                )
            )
        )
    return "\n\n".join(blocks)


def _render_execution_trace(result: AnswerResult) -> str:
    """Return an auditable high-level execution summary, not hidden model reasoning."""

    rails = ",".join(rail.value for rail in result.plan.route.rails) or "none"
    metadata = dict(result.metadata)
    fields = [
        f"mode={result.plan.mode.value}",
        f"rails={rails}",
        f"retrieval_status={result.evidence_pack.retrieval_status}",
        f"evidence_count={len(result.evidence_pack.items)}",
        f"generator={result.generator}",
        f"status={result.status}",
    ]
    if "fallback" in metadata:
        fields.append(f"fallback={metadata['fallback']}")
    return "; ".join(fields)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    engine = get_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health endpoint must return a stable HTTP error
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    return HealthResponse(
        status="ok",
        database="ok",
        hcx_configured=bool(settings.clova_studio_api_key or settings.hcx_api_key),
    )


@app.get("/answer", response_model=AnswerResponse)
def answer(
    question_id: str = Query(..., min_length=1),
    question: str = Query(..., min_length=1),
) -> AnswerResponse:
    settings = get_settings()
    engine = get_engine(settings.database_url)
    api_key = settings.clova_studio_api_key or settings.hcx_api_key

    try:
        with session_scope(engine) as session:
            result = AnswerService(session, api_key=api_key).answer(question)
    except Exception as exc:  # noqa: BLE001 - keep evaluation API response stable
        raise HTTPException(status_code=500, detail="answer generation failed") from exc

    return AnswerResponse(
        question_id=question_id,
        question=question,
        retrieved_context=_render_retrieved_context(result.evidence_pack),
        think_trace=_render_execution_trace(result),
        answer=result.answer,
    )
