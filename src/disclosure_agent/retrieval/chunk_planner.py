"""Deterministic, provenance-preserving planning for narrative retrieval chunks."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ChunkPolicy:
    """Character-based boundaries used before model-specific tokenization is chosen."""

    min_chars: int = 400
    target_chars: int = 1_000
    max_chars: int = 1_200
    overlap_chars: int = 120

    def __post_init__(self) -> None:
        if self.min_chars <= 0:
            raise ValueError("min_chars must be positive")
        if self.target_chars <= 0:
            raise ValueError("target_chars must be positive")
        if self.min_chars > self.target_chars:
            raise ValueError("min_chars must not exceed target_chars")
        if self.max_chars < self.target_chars:
            raise ValueError("max_chars must be greater than or equal to target_chars")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ValueError("overlap_chars must be between zero and max_chars")


@dataclass(frozen=True, slots=True)
class SourceBlock:
    """Minimal Source Layer block needed by the dry-run planner."""

    document_group: str
    filing_id: str
    document_id: str
    section_id: str | None
    section_title: str | None
    block_id: str
    block_order: int
    block_type: str
    text: str | None = None
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class PlannedNarrativeChunk:
    """A dry-run narrative chunk with canonical block provenance."""

    chunk_key: str
    document_group: str
    filing_id: str
    document_id: str
    section_id: str | None
    heading_path: tuple[str, ...]
    text: str
    block_ids: tuple[str, ...]
    start_block_order: int
    end_block_order: int


@dataclass(slots=True)
class NarrativeChunkPlanner:
    """Consume ordered Source Layer blocks without materializing the corpus."""

    policy: ChunkPolicy = field(default_factory=ChunkPolicy)
    _document_id: str | None = None
    _document_group: str = ""
    _filing_id: str = ""
    _section_id: str | None = None
    _section_title: str | None = None
    _headings: dict[int, str] = field(default_factory=dict)
    _texts: list[str] = field(default_factory=list)
    _block_ids: list[str] = field(default_factory=list)
    _chunk_headings: list[str] = field(default_factory=list)
    _start_order: int | None = None
    _end_order: int | None = None
    _chunk_ordinal: int = 0

    def push(self, block: SourceBlock) -> list[PlannedNarrativeChunk]:
        """Consume one block and return chunks completed by that boundary."""

        emitted: list[PlannedNarrativeChunk] = []
        if self._document_id != block.document_id:
            emitted.extend(self.finish())
            self._start_document(block)
        elif self._section_id != block.section_id:
            emitted.extend(self._flush())
            self._start_section(block)

        if block.block_type == "paragraph":
            emitted.extend(self._push_paragraph(block))
        elif block.block_type == "heading":
            if self._texts and self._current_length() >= self.policy.min_chars:
                emitted.extend(self._flush())
            self._set_heading(block)
            if self._texts:
                emitted.extend(self._append_heading_marker(block))
        elif block.block_type in {"page_break", "table"}:
            pass
        else:
            # Unknown content remains a conservative boundary until representative
            # samples have been reviewed. Tables are deliberately transparent:
            # DART uses many of them for layout, and table retrieval is planned
            # independently without fragmenting surrounding narrative.
            emitted.extend(self._flush())
        return emitted

    def finish(self) -> list[PlannedNarrativeChunk]:
        """Flush the current document and reset document-local state."""

        emitted = self._flush()
        self._document_id = None
        self._document_group = ""
        self._filing_id = ""
        self._section_id = None
        self._section_title = None
        self._headings.clear()
        self._chunk_headings.clear()
        self._chunk_ordinal = 0
        return emitted

    def _start_document(self, block: SourceBlock) -> None:
        self._document_id = block.document_id
        self._document_group = block.document_group
        self._filing_id = block.filing_id
        self._chunk_ordinal = 0
        self._start_section(block)

    def _start_section(self, block: SourceBlock) -> None:
        self._section_id = block.section_id
        self._section_title = _clean(block.section_title)
        self._headings.clear()

    def _set_heading(self, block: SourceBlock) -> None:
        value = _clean(block.text)
        if not value:
            return
        level = max(block.heading_level or 1, 1)
        self._headings = {
            current_level: heading
            for current_level, heading in self._headings.items()
            if current_level < level
        }
        self._headings[level] = value

    def _push_paragraph(self, block: SourceBlock) -> list[PlannedNarrativeChunk]:
        value = _clean(block.text)
        if not value:
            return []

        emitted: list[PlannedNarrativeChunk] = []
        if len(value) > self.policy.max_chars:
            emitted.extend(self._flush())
            for part in _split_long_text(value, self.policy):
                self._texts = [part]
                self._block_ids = [block.block_id]
                self._start_order = block.block_order
                self._end_order = block.block_order
                emitted.extend(self._flush())
            return emitted

        proposed_length = len(value)
        if self._texts:
            proposed_length += sum(len(text) for text in self._texts)
            proposed_length += 2 * len(self._texts)
        if self._texts and proposed_length > self.policy.max_chars:
            emitted.extend(self._flush())

        self._texts.append(value)
        self._block_ids.append(block.block_id)
        self._record_current_headings()
        if self._start_order is None:
            self._start_order = block.block_order
        self._end_order = block.block_order

        # Target is a soft boundary. Waiting until the next paragraph preserves
        # coherent blocks while the hard maximum prevents unbounded chunks.
        if sum(len(text) for text in self._texts) >= self.policy.target_chars:
            emitted.extend(self._flush())
        return emitted

    def _append_heading_marker(self, block: SourceBlock) -> list[PlannedNarrativeChunk]:
        value = _clean(block.text)
        if not value:
            return []
        marker = f"[소제목] {value}"
        emitted: list[PlannedNarrativeChunk] = []
        if self._current_length(extra=marker) > self.policy.max_chars:
            emitted.extend(self._flush())
            return emitted
        self._texts.append(marker)
        self._block_ids.append(block.block_id)
        self._record_current_headings()
        if self._start_order is None:
            self._start_order = block.block_order
        self._end_order = block.block_order
        return emitted

    def _flush(self) -> list[PlannedNarrativeChunk]:
        if not self._texts:
            return []
        assert self._document_id is not None
        assert self._start_order is not None
        assert self._end_order is not None

        self._chunk_ordinal += 1
        chunk = PlannedNarrativeChunk(
            chunk_key=f"{self._document_id}:narrative:{self._chunk_ordinal}",
            document_group=self._document_group,
            filing_id=self._filing_id,
            document_id=self._document_id,
            section_id=self._section_id,
            heading_path=self._heading_path(),
            text="\n\n".join(self._texts),
            block_ids=tuple(self._block_ids),
            start_block_order=self._start_order,
            end_block_order=self._end_order,
        )
        self._texts.clear()
        self._block_ids.clear()
        self._chunk_headings.clear()
        self._start_order = None
        self._end_order = None
        return [chunk]

    def _heading_path(self) -> tuple[str, ...]:
        if self._chunk_headings:
            return tuple(self._chunk_headings)
        values: list[str] = []
        if self._section_title:
            values.append(self._section_title)
        for level in sorted(self._headings):
            heading = self._headings[level]
            if not values or values[-1] != heading:
                values.append(heading)
        return tuple(values)

    def _record_current_headings(self) -> None:
        for value in self._current_heading_path():
            if value not in self._chunk_headings:
                self._chunk_headings.append(value)

    def _current_heading_path(self) -> tuple[str, ...]:
        values: list[str] = []
        if self._section_title:
            values.append(self._section_title)
        for level in sorted(self._headings):
            heading = self._headings[level]
            if not values or values[-1] != heading:
                values.append(heading)
        return tuple(values)

    def _current_length(self, *, extra: str | None = None) -> int:
        values = [*self._texts]
        if extra:
            values.append(extra)
        return len("\n\n".join(values))


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def _split_long_text(value: str, policy: ChunkPolicy) -> list[str]:
    """Split oversized text deterministically while retaining a small overlap."""

    parts: list[str] = []
    start = 0
    while start < len(value):
        hard_end = min(start + policy.max_chars, len(value))
        end = hard_end
        if hard_end < len(value):
            lower_bound = start + policy.target_chars
            boundary = max(
                value.rfind("\n", lower_bound, hard_end),
                value.rfind(". ", lower_bound, hard_end),
                value.rfind("다. ", lower_bound, hard_end),
            )
            if boundary >= lower_bound:
                end = boundary + (1 if value[boundary] == "\n" else 2)
        part = value[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(value):
            break
        start = max(end - policy.overlap_chars, start + 1)
    return parts
