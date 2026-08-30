"""Materialize approved retrieval chunks from the immutable Source Layer."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator

import orjson

from disclosure_agent.retrieval.chunk_planner import PlannedNarrativeChunk

PLAN_VERSION = "4.0.0"
TABLE_MAX_CHARS = 2_000
TABLE_OVERLAP_CHARS = 120
VECTOR_TABLE_BUCKETS = {
    "exchange_direct",
    "exchange_row_window",
    "direct_candidate",
    "row_window_candidate",
}


@dataclass(frozen=True, slots=True)
class ApprovedChunkPlan:
    """Validated subset of the dry-run plan used as an ingestion contract."""

    path: Path
    sha256: str
    load_run_id: str
    manifest_sha256: str
    policy: dict[str, Any]
    narrative_chunks: int
    vector_source_tables: int
    estimated_table_chunks: int


@dataclass(frozen=True, slots=True)
class SourceTable:
    """Minimal Source Layer table representation used by table chunking."""

    table_id: str
    document_group: str
    filing_id: str
    document_id: str
    section_id: str | None
    section_title: str | None
    block_id: str
    block_order: int
    caption: str | None
    normalized_text: str
    grid: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MaterializedChunk:
    """Database-ready retrieval chunk with source provenance."""

    chunk_id: str
    filing_id: str
    document_id: str
    section_id: str | None
    document_group: str
    chunk_type: str
    content: str
    heading_path: tuple[str, ...]
    source_block_ids: tuple[str, ...]
    source_table_id: str | None
    start_block_order: int | None
    end_block_order: int | None
    table_row_start: int | None
    table_row_end: int | None
    metadata: dict[str, Any]

    @property
    def content_sha256(self) -> str:
        return sha256(self.content.encode("utf-8")).hexdigest()

    def as_row(self, chunk_run_id: str) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_run_id": chunk_run_id,
            "filing_id": self.filing_id,
            "document_id": self.document_id,
            "section_id": self.section_id,
            "document_group": self.document_group,
            "chunk_type": self.chunk_type,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "char_count": len(self.content),
            "heading_path": list(self.heading_path),
            "source_block_ids": list(self.source_block_ids),
            "source_table_id": self.source_table_id,
            "start_block_order": self.start_block_order,
            "end_block_order": self.end_block_order,
            "table_row_start": self.table_row_start,
            "table_row_end": self.table_row_end,
            "metadata": self.metadata,
        }


def load_approved_plan(path: Path) -> ApprovedChunkPlan:
    """Read and validate the v4 dry-run plan before any database write."""

    raw = path.read_bytes()
    payload = orjson.loads(raw)
    if payload.get("plan_version") != PLAN_VERSION:
        raise ValueError(
            f"Expected retrieval plan {PLAN_VERSION}, got "
            f"{payload.get('plan_version')!r}"
        )
    if payload.get("mode") not in {
        "read_only_dry_run",
        "read_only_table_dry_run",
    }:
        raise ValueError("Retrieval plan was not produced by a read-only dry run")

    load = payload.get("load") or {}
    policy = payload.get("policy") or {}
    narrative = payload.get("narrative") or {}
    tables = payload.get("tables") or {}
    buckets = tables.get("buckets") or []

    required = {
        "load_run_id": load.get("load_run_id"),
        "manifest_sha256": load.get("manifest_sha256"),
        "narrative_chunks": narrative.get("chunks"),
    }
    missing = [key for key, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError(f"Retrieval plan is missing: {', '.join(missing)}")

    vector_source_tables = sum(
        int(row.get("tables") or 0)
        for row in buckets
        if row.get("decision_bucket") in VECTOR_TABLE_BUCKETS
    )
    estimated_table_chunks = int(
        tables.get("initial_chunk_estimate_excluding_review_buckets") or 0
    )
    if vector_source_tables <= 0 or estimated_table_chunks < vector_source_tables:
        raise ValueError("Retrieval plan has an invalid vector table estimate")
    if policy.get("tables") != "vector_structured_lexical_lanes_not_persisted":
        raise ValueError("Retrieval plan does not contain the approved table policy")

    return ApprovedChunkPlan(
        path=path,
        sha256=sha256(raw).hexdigest(),
        load_run_id=str(required["load_run_id"]),
        manifest_sha256=str(required["manifest_sha256"]),
        policy=dict(policy),
        narrative_chunks=int(required["narrative_chunks"]),
        vector_source_tables=vector_source_tables,
        estimated_table_chunks=estimated_table_chunks,
    )


def chunk_run_id(plan: ApprovedChunkPlan) -> str:
    """Return a stable identity for an exact Source Layer and plan pair."""

    identity = f"{plan.load_run_id}:{plan.sha256}".encode()
    return sha256(identity).hexdigest()[:32]


def narrative_chunk(chunk: PlannedNarrativeChunk) -> MaterializedChunk:
    """Convert a planner result without weakening its provenance."""

    return MaterializedChunk(
        chunk_id=chunk.chunk_key,
        filing_id=chunk.filing_id,
        document_id=chunk.document_id,
        section_id=chunk.section_id,
        document_group=chunk.document_group,
        chunk_type="narrative",
        content=chunk.text,
        heading_path=chunk.heading_path,
        source_block_ids=chunk.block_ids,
        source_table_id=None,
        start_block_order=chunk.start_block_order,
        end_block_order=chunk.end_block_order,
        table_row_start=None,
        table_row_end=None,
        metadata={},
    )


def table_chunks(
    table: SourceTable,
    *,
    max_chars: int = TABLE_MAX_CHARS,
    overlap_chars: int = TABLE_OVERLAP_CHARS,
) -> list[MaterializedChunk]:
    """Create direct or row-aware chunks for one approved vector table."""

    text = table.normalized_text.strip()
    if not text:
        return []
    heading_path = tuple(
        value for value in (table.section_title,) if value and value.strip()
    )
    metadata = {"caption": table.caption} if table.caption else {}
    if len(text) <= max_chars:
        return [
            _table_chunk(
                table,
                ordinal=1,
                content=text,
                heading_path=heading_path,
                row_start=None,
                row_end=None,
                metadata=metadata,
            )
        ]

    rows = _table_rows(table.grid)
    if not rows:
        parts = _split_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
        return [
            _table_chunk(
                table,
                ordinal=index,
                content=part,
                heading_path=heading_path,
                row_start=None,
                row_end=None,
                metadata={**metadata, "split": "character_fallback"},
            )
            for index, part in enumerate(parts, 1)
        ]

    header_indices = {
        int(value) for value in table.grid.get("header_row_indices", [])
    }
    header_lines = [line for index, line in rows if index in header_indices]
    prefix_lines = []
    if table.caption and table.caption.strip():
        prefix_lines.append(table.caption.strip())
    prefix_lines.extend(header_lines)
    prefix = "\n".join(dict.fromkeys(prefix_lines))
    data_rows = [row for row in rows if row[0] not in header_indices]
    if not data_rows:
        data_rows = rows
        prefix = ""

    windows: list[tuple[str, int, int]] = []
    current: list[tuple[int, str]] = []
    for row_index, line in data_rows:
        candidate = _render_table_window(prefix, [*current, (row_index, line)])
        if current and len(candidate) > max_chars:
            windows.extend(
                _bounded_table_window(
                    prefix,
                    current,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
            current = []
        current.append((row_index, line))
    if current:
        windows.extend(
            _bounded_table_window(
                prefix,
                current,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )

    return [
        _table_chunk(
            table,
            ordinal=index,
            content=content,
            heading_path=heading_path,
            row_start=row_start,
            row_end=row_end,
            metadata={**metadata, "split": "row_window"},
        )
        for index, (content, row_start, row_end) in enumerate(windows, 1)
    ]


def _table_rows(grid: dict[str, Any]) -> list[tuple[int, str]]:
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in grid.get("cells", []):
        value = str(cell.get("text_normalized") or "").strip()
        if not value:
            continue
        row_index = int(cell.get("row_index") or 0)
        column_index = int(cell.get("column_index") or 0)
        rows.setdefault(row_index, []).append((column_index, value))
    return [
        (row_index, " | ".join(value for _, value in sorted(cells)))
        for row_index, cells in sorted(rows.items())
    ]


def _render_table_window(prefix: str, rows: Iterable[tuple[int, str]]) -> str:
    lines = [line for _, line in rows]
    if prefix:
        lines.insert(0, prefix)
    return "\n".join(lines).strip()


def _bounded_table_window(
    prefix: str,
    rows: list[tuple[int, str]],
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[tuple[str, int, int]]:
    content = _render_table_window(prefix, rows)
    row_start = rows[0][0]
    row_end = rows[-1][0]
    if len(content) <= max_chars:
        return [(content, row_start, row_end)]
    return [
        (part, row_start, row_end)
        for part in _split_text(
            content,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    ]


def _split_text(value: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if max_chars <= 0 or not 0 <= overlap_chars < max_chars:
        raise ValueError("Invalid split boundaries")
    parts: list[str] = []
    start = 0
    while start < len(value):
        hard_end = min(start + max_chars, len(value))
        end = hard_end
        if hard_end < len(value):
            boundary = value.rfind("\n", start + max_chars // 2, hard_end)
            if boundary > start:
                end = boundary + 1
        part = value[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(value):
            break
        start = max(start + 1, end - overlap_chars)
    return parts


def _table_chunk(
    table: SourceTable,
    *,
    ordinal: int,
    content: str,
    heading_path: tuple[str, ...],
    row_start: int | None,
    row_end: int | None,
    metadata: dict[str, Any],
) -> MaterializedChunk:
    return MaterializedChunk(
        chunk_id=f"{table.table_id}:retrieval:table:{ordinal}",
        filing_id=table.filing_id,
        document_id=table.document_id,
        section_id=table.section_id,
        document_group=table.document_group,
        chunk_type="table",
        content=content,
        heading_path=heading_path,
        source_block_ids=(table.block_id,),
        source_table_id=table.table_id,
        start_block_order=table.block_order,
        end_block_order=table.block_order,
        table_row_start=row_start,
        table_row_end=row_end,
        metadata=metadata,
    )


def batches(rows: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    """Yield bounded insertion batches without materializing the corpus."""

    if size <= 0:
        raise ValueError("Batch size must be positive")
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
