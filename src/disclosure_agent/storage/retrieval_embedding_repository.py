"""Persistence and vector search for retrieval chunk embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.retrieval_chunk_models import RetrievalChunkRow


@dataclass(frozen=True, slots=True)
class PendingEmbeddingChunk:
    """One persisted chunk waiting for an embedding."""

    chunk_id: str
    content_text: str


@dataclass(frozen=True, slots=True)
class SemanticSearchHit:
    """One nearest retrieval chunk with source metadata."""

    chunk_id: str
    company_name: str
    filing_id: str
    report_name: str
    document_id: str
    section_id: str | None
    similarity: float
    content_text: str
    block_ids: tuple[str, ...]
    table_ids: tuple[str, ...]


class RetrievalEmbeddingRepository:
    """Read pending chunks, persist vectors, and perform cosine search."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def pending_chunks(
        self,
        *,
        company_name: str | None,
        limit: int,
    ) -> tuple[PendingEmbeddingChunk, ...]:
        """Return a deterministic batch of chunks without embeddings."""

        if limit < 1:
            raise ValueError("limit must be at least 1")

        statement = (
            select(RetrievalChunkRow.chunk_id, RetrievalChunkRow.content_text)
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == RetrievalChunkRow.corp_code)
            .where(RetrievalChunkRow.embedding.is_(None))
            .order_by(RetrievalChunkRow.chunk_id)
            .limit(limit)
        )
        if company_name is not None:
            statement = statement.where(
                or_(
                    SourceCompanyRow.listed_name == company_name,
                    SourceCompanyRow.corp_name == company_name,
                )
            )

        return tuple(
            PendingEmbeddingChunk(chunk_id=row.chunk_id, content_text=row.content_text)
            for row in self.session.execute(statement)
        )

    def save_embedding(
        self,
        *,
        chunk_id: str,
        vector: tuple[float, ...],
        model: str,
        input_tokens: int,
    ) -> None:
        """Persist one validated embedding and its provenance."""

        self.session.execute(
            update(RetrievalChunkRow)
            .where(RetrievalChunkRow.chunk_id == chunk_id)
            .values(
                embedding=list(vector),
                embedding_model=model,
                embedding_input_tokens=input_tokens,
                embedded_at=datetime.now(UTC),
            )
        )

    def search(
        self,
        *,
        query_vector: tuple[float, ...],
        company_name: str | None = None,
        filing_id: str | None = None,
        report_name: str | None = None,
        year: int | None = None,
        top_k: int = 5,
    ) -> tuple[SemanticSearchHit, ...]:
        """Return nearest embedded chunks using pgvector cosine distance."""

        if top_k < 1 or top_k > 50:
            raise ValueError("top_k must be between 1 and 50")

        distance = RetrievalChunkRow.embedding.cosine_distance(list(query_vector)).label("distance")
        statement = (
            select(
                RetrievalChunkRow.chunk_id,
                SourceCompanyRow.listed_name.label("company_name"),
                RetrievalChunkRow.filing_id,
                SourceFilingRow.report_name,
                RetrievalChunkRow.document_id,
                RetrievalChunkRow.section_id,
                RetrievalChunkRow.content_text,
                RetrievalChunkRow.block_ids,
                RetrievalChunkRow.table_ids,
                distance,
            )
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == RetrievalChunkRow.corp_code)
            .join(SourceFilingRow, SourceFilingRow.filing_id == RetrievalChunkRow.filing_id)
            .where(RetrievalChunkRow.embedding.is_not(None))
        )
        if company_name is not None:
            statement = statement.where(
                or_(
                    SourceCompanyRow.listed_name == company_name,
                    SourceCompanyRow.corp_name == company_name,
                )
            )
        if filing_id is not None:
            statement = statement.where(RetrievalChunkRow.filing_id == filing_id)
        if report_name is not None:
            statement = statement.where(SourceFilingRow.report_name == report_name)
        if year is not None:
            statement = statement.where(SourceFilingRow.report_name.ilike(f"%{year}%"))

        statement = statement.order_by(distance).limit(top_k)
        hits = []
        for row in self.session.execute(statement):
            hits.append(
                SemanticSearchHit(
                    chunk_id=row.chunk_id,
                    company_name=row.company_name,
                    filing_id=row.filing_id,
                    report_name=row.report_name,
                    document_id=row.document_id,
                    section_id=row.section_id,
                    similarity=1.0 - float(row.distance),
                    content_text=row.content_text,
                    block_ids=tuple(row.block_ids),
                    table_ids=tuple(row.table_ids),
                )
            )
        return tuple(hits)
