"""Evidence-pack construction for grounded answer generation."""

from __future__ import annotations

from dataclasses import dataclass

from disclosure_agent.retrieval.reranker import RerankedSemanticHit


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One retrieval result normalized for downstream answer generation."""

    evidence_id: str
    source_kind: str
    rank: int
    score: float
    semantic_score: float
    lexical_score: float
    company_name: str
    filing_id: str
    report_name: str
    document_id: str
    section_id: str | None
    content_text: str
    truncated: bool
    matched_terms: tuple[str, ...]
    block_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    fact_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Bounded evidence context that can be passed to the answer model."""

    query: str
    retrieval_status: str
    items: tuple[EvidenceItem, ...]
    total_chars: int


def build_semantic_evidence_pack(
    query: str,
    hits: tuple[RerankedSemanticHit, ...],
    *,
    max_items: int = 5,
    max_chars_per_item: int = 3200,
    max_total_chars: int = 12000,
) -> EvidencePack:
    """Convert reranked semantic hits into bounded, lineage-preserving evidence."""

    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    if max_chars_per_item < 1:
        raise ValueError("max_chars_per_item must be at least 1")
    if max_total_chars < 1:
        raise ValueError("max_total_chars must be at least 1")

    items: list[EvidenceItem] = []
    remaining_chars = max_total_chars

    for rank, reranked in enumerate(hits[:max_items], start=1):
        if remaining_chars <= 0:
            break

        hit = reranked.hit
        allowed_chars = min(max_chars_per_item, remaining_chars)
        content_text = hit.content_text[:allowed_chars]
        truncated = len(content_text) < len(hit.content_text)
        if not content_text:
            continue

        items.append(
            EvidenceItem(
                evidence_id=f"semantic:{hit.chunk_id}",
                source_kind="semantic_chunk",
                rank=rank,
                score=reranked.final_score,
                semantic_score=hit.similarity,
                lexical_score=reranked.lexical_score,
                company_name=hit.company_name,
                filing_id=hit.filing_id,
                report_name=hit.report_name,
                document_id=hit.document_id,
                section_id=hit.section_id,
                content_text=content_text,
                truncated=truncated,
                matched_terms=reranked.matched_terms,
                block_ids=hit.block_ids,
                table_ids=hit.table_ids,
            )
        )
        remaining_chars -= len(content_text)

    packed_items = tuple(items)
    retrieval_status = "MATCHES_FOUND" if packed_items else "NO_MATCH"
    return EvidencePack(
        query=query,
        retrieval_status=retrieval_status,
        items=packed_items,
        total_chars=sum(len(item.content_text) for item in packed_items),
    )


def render_evidence_pack(pack: EvidencePack) -> str:
    """Render a deterministic text representation for an LLM prompt."""

    lines = [
        "=== EVIDENCE PACK ===",
        f"query: {pack.query}",
        f"retrieval_status: {pack.retrieval_status}",
        f"evidence_count: {len(pack.items)}",
        f"total_chars: {pack.total_chars}",
    ]

    for item in pack.items:
        lines.extend(
            [
                "",
                f"[E{item.rank}] kind={item.source_kind} score={item.score:.6f}",
                f"company={item.company_name} report={item.report_name}",
                f"filing_id={item.filing_id}",
                f"document_id={item.document_id}",
                f"section_id={item.section_id or '-'}",
                f"block_ids={','.join(item.block_ids) if item.block_ids else '-'}",
                f"table_ids={','.join(item.table_ids) if item.table_ids else '-'}",
                f"matched_terms={','.join(item.matched_terms) if item.matched_terms else '-'}",
                f"truncated={str(item.truncated).lower()}",
                "text:",
                item.content_text,
            ]
        )

    return "\n".join(lines)
