"""Build and persist deterministic semantic chunks from the canonical source layer."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import delete, insert, or_, select
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import (
    SourceBlockRow,
    SourceCompanyRow,
    SourceDocumentRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)
from disclosure_agent.storage.retrieval_chunk_models import RetrievalChunkRow


@dataclass(frozen=True, slots=True)
class DocumentContext:
    """Metadata repeated in each chunk so embeddings retain filing context."""

    corp_code: str
    company_name: str
    filing_id: str
    report_name: str
    document_id: str
    document_title: str


@dataclass(frozen=True, slots=True)
class SourceBlockText:
    """One ordered source block converted to retrieval text."""

    block_id: str
    block_order: int
    section_id: str | None
    section_title: str
    table_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One streamed database row before document-level chunk construction."""

    corp_code: str
    company_name: str
    filing_id: str
    report_name: str
    document_id: str
    document_title: str
    block_id: str
    block_order: int
    section_id: str | None
    section_title: str
    table_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class RetrievalChunkDraft:
    """One deterministic chunk ready for persistence."""

    chunk_id: str
    corp_code: str
    filing_id: str
    document_id: str
    section_id: str | None
    chunk_index: int
    start_block_order: int
    end_block_order: int
    block_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    content_text: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ChunkBuildStats:
    """Aggregate counts returned after rebuilding retrieval chunks."""

    documents: int
    source_blocks: int
    chunks: int
    table_chunks: int
    characters: int


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _context_prefix(context: DocumentContext, section_title: str) -> str:
    lines = [
        f"회사: {context.company_name}",
        f"공시: {context.report_name}",
        f"문서: {context.document_title}",
    ]
    if section_title:
        lines.append(f"섹션: {section_title}")
    return "\n".join(lines) + "\n\n"


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            split_at = text.rfind(" ", start + max_chars // 2, end)
            if split_at > start:
                end = split_at
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return tuple(parts)


def _make_chunk(
    *,
    context: DocumentContext,
    section_id: str | None,
    section_title: str,
    chunk_index: int,
    blocks: tuple[SourceBlockText, ...],
    body: str,
) -> RetrievalChunkDraft:
    prefix = _context_prefix(context, section_title)
    content_text = prefix + body
    content_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    chunk_key = f"{context.document_id}|{chunk_index}|{content_sha256}"
    chunk_id = hashlib.sha256(chunk_key.encode("utf-8")).hexdigest()
    table_ids = tuple(block.table_id for block in blocks if block.table_id is not None)
    return RetrievalChunkDraft(
        chunk_id=chunk_id,
        corp_code=context.corp_code,
        filing_id=context.filing_id,
        document_id=context.document_id,
        section_id=section_id,
        chunk_index=chunk_index,
        start_block_order=blocks[0].block_order,
        end_block_order=blocks[-1].block_order,
        block_ids=tuple(block.block_id for block in blocks),
        table_ids=table_ids,
        content_text=content_text,
        content_sha256=content_sha256,
    )


def build_document_chunks(
    context: DocumentContext,
    blocks: tuple[SourceBlockText, ...],
    *,
    max_chars: int = 3200,
    overlap_chars: int = 240,
) -> tuple[RetrievalChunkDraft, ...]:
    """Chunk ordered blocks without crossing section boundaries."""

    if max_chars < 1000:
        raise ValueError("max_chars must be at least 1000")
    if overlap_chars < 0 or overlap_chars >= max_chars // 2:
        raise ValueError("overlap_chars must be non-negative and less than half max_chars")

    chunks: list[RetrievalChunkDraft] = []
    chunk_index = 0
    position = 0

    while position < len(blocks):
        section_id = blocks[position].section_id
        section_title = blocks[position].section_title
        section_blocks: list[SourceBlockText] = []
        while position < len(blocks) and blocks[position].section_id == section_id:
            if blocks[position].text:
                section_blocks.append(blocks[position])
            position += 1
        if not section_blocks:
            continue

        prefix = _context_prefix(context, section_title)
        body_budget = max_chars - len(prefix)
        if body_budget < 500:
            raise ValueError("chunk context leaves less than 500 characters for source content")

        pending: list[SourceBlockText] = []
        pending_parts: list[str] = []
        pending_chars = 0

        def flush_pending(
            current_section_id: str | None,
            current_section_title: str,
        ) -> None:
            nonlocal chunk_index, pending, pending_parts, pending_chars
            if not pending:
                return
            body = "\n\n".join(pending_parts)
            chunks.append(
                _make_chunk(
                    context=context,
                    section_id=current_section_id,
                    section_title=current_section_title,
                    chunk_index=chunk_index,
                    blocks=tuple(pending),
                    body=body,
                )
            )
            chunk_index += 1
            pending = []
            pending_parts = []
            pending_chars = 0

        for block in section_blocks:
            text = block.text
            if len(text) > body_budget:
                flush_pending(section_id, section_title)
                for part in _split_long_text(text, body_budget, overlap_chars):
                    chunks.append(
                        _make_chunk(
                            context=context,
                            section_id=section_id,
                            section_title=section_title,
                            chunk_index=chunk_index,
                            blocks=(block,),
                            body=part,
                        )
                    )
                    chunk_index += 1
                continue

            separator = 2 if pending_parts else 0
            if pending and pending_chars + separator + len(text) > body_budget:
                flush_pending(section_id, section_title)
            pending.append(block)
            pending_parts.append(text)
            pending_chars += (2 if len(pending_parts) > 1 else 0) + len(text)

        flush_pending(section_id, section_title)

    return tuple(chunks)


class RetrievalChunkRepository:
    """Stream source blocks, build chunks, and replace the selected retrieval slice."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def rebuild_chunks(
        self,
        *,
        companies: tuple[str, ...] = (),
        max_chars: int = 3200,
        overlap_chars: int = 240,
        batch_size: int = 500,
    ) -> ChunkBuildStats:
        """Rebuild chunks for all companies or the requested company subset."""

        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._delete_existing(companies)

        documents = 0
        source_blocks = 0
        chunks_count = 0
        table_chunks = 0
        characters = 0
        pending_rows: list[dict[str, object]] = []
        current_context: DocumentContext | None = None
        current_blocks: list[SourceBlockText] = []

        def flush_document() -> None:
            nonlocal documents, chunks_count, table_chunks, characters, pending_rows
            if current_context is None or not current_blocks:
                return
            drafts = build_document_chunks(
                current_context,
                tuple(current_blocks),
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
            documents += 1
            for draft in drafts:
                pending_rows.append(self._row_dict(draft))
                chunks_count += 1
                characters += len(draft.content_text)
                if draft.table_ids:
                    table_chunks += 1
                if len(pending_rows) >= batch_size:
                    self.session.execute(insert(RetrievalChunkRow), pending_rows)
                    pending_rows = []

        for row in self._source_rows(companies):
            context = DocumentContext(
                corp_code=row.corp_code,
                company_name=row.company_name,
                filing_id=row.filing_id,
                report_name=row.report_name,
                document_id=row.document_id,
                document_title=row.document_title,
            )
            if current_context is not None and context.document_id != current_context.document_id:
                flush_document()
                current_blocks = []
            current_context = context
            current_blocks.append(
                SourceBlockText(
                    block_id=row.block_id,
                    block_order=row.block_order,
                    section_id=row.section_id,
                    section_title=row.section_title,
                    table_id=row.table_id,
                    text=row.text,
                )
            )
            source_blocks += 1

        flush_document()
        if pending_rows:
            self.session.execute(insert(RetrievalChunkRow), pending_rows)

        return ChunkBuildStats(
            documents=documents,
            source_blocks=source_blocks,
            chunks=chunks_count,
            table_chunks=table_chunks,
            characters=characters,
        )

    def _delete_existing(self, companies: tuple[str, ...]) -> None:
        statement = delete(RetrievalChunkRow)
        if companies:
            corp_codes = select(SourceCompanyRow.corp_code).where(
                or_(
                    SourceCompanyRow.listed_name.in_(companies),
                    SourceCompanyRow.corp_name.in_(companies),
                )
            )
            statement = statement.where(RetrievalChunkRow.corp_code.in_(corp_codes))
        self.session.execute(statement)

    def _source_rows(self, companies: tuple[str, ...]) -> Iterator[SourceRow]:
        statement = (
            select(
                SourceCompanyRow.corp_code.label("corp_code"),
                SourceCompanyRow.listed_name.label("company_name"),
                SourceFilingRow.filing_id.label("filing_id"),
                SourceFilingRow.report_name.label("report_name"),
                SourceDocumentRow.document_id.label("document_id"),
                SourceDocumentRow.title_normalized.label("document_title_normalized"),
                SourceDocumentRow.title_raw.label("document_title_raw"),
                SourceBlockRow.block_id.label("block_id"),
                SourceBlockRow.block_order.label("block_order"),
                SourceBlockRow.section_id.label("section_id"),
                SourceSectionRow.title_normalized.label("section_title_normalized"),
                SourceSectionRow.title_raw.label("section_title_raw"),
                SourceBlockRow.table_id.label("table_id"),
                SourceBlockRow.text_normalized.label("block_text"),
                SourceBlockRow.text_raw.label("block_text_raw"),
                SourceTableRow.normalized_text.label("table_text"),
            )
            .join(SourceDocumentRow, SourceDocumentRow.document_id == SourceBlockRow.document_id)
            .join(SourceFilingRow, SourceFilingRow.filing_id == SourceBlockRow.filing_id)
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
            .outerjoin(SourceTableRow, SourceTableRow.table_id == SourceBlockRow.table_id)
            .outerjoin(SourceSectionRow, SourceSectionRow.section_id == SourceBlockRow.section_id)
            .order_by(SourceBlockRow.document_id, SourceBlockRow.block_order)
        )
        if companies:
            statement = statement.where(
                or_(
                    SourceCompanyRow.listed_name.in_(companies),
                    SourceCompanyRow.corp_name.in_(companies),
                )
            )

        result = self.session.execute(
            statement.execution_options(stream_results=True, yield_per=5000)
        )
        for row in result:
            text = row.table_text if row.table_id is not None else row.block_text
            if not text:
                text = row.block_text_raw or ""
            yield SourceRow(
                corp_code=row.corp_code,
                company_name=row.company_name,
                filing_id=row.filing_id,
                report_name=row.report_name,
                document_id=row.document_id,
                document_title=_normalize_text(
                    row.document_title_normalized or row.document_title_raw
                ),
                block_id=row.block_id,
                block_order=row.block_order,
                section_id=row.section_id,
                section_title=_normalize_text(
                    row.section_title_normalized or row.section_title_raw
                ),
                table_id=row.table_id,
                text=_normalize_text(text),
            )

    @staticmethod
    def _row_dict(draft: RetrievalChunkDraft) -> dict[str, object]:
        return {
            "chunk_id": draft.chunk_id,
            "corp_code": draft.corp_code,
            "filing_id": draft.filing_id,
            "document_id": draft.document_id,
            "section_id": draft.section_id,
            "chunk_index": draft.chunk_index,
            "start_block_order": draft.start_block_order,
            "end_block_order": draft.end_block_order,
            "block_ids": list(draft.block_ids),
            "table_ids": list(draft.table_ids),
            "content_text": draft.content_text,
            "content_sha256": draft.content_sha256,
            "char_count": len(draft.content_text),
        }
