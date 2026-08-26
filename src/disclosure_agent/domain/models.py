"""Canonical domain models for the disclosure ingestion pipeline.

The models in this module are intentionally independent from parsers, storage,
and rendering.  They define the loss-minimising contract produced by parsing
and consumed by later database, retrieval, and Markdown layers.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "2.1.0"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def normalize_nfc(value: str) -> str:
    """Normalize Korean and other Unicode text to NFC."""

    return unicodedata.normalize("NFC", value)


class CanonicalModel(BaseModel):
    """Strict base model shared by all canonical records."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
    )


class DocumentGroup(StrEnum):
    """Top-level disclosure groups used by the supplied corpus."""

    PERIODIC = "periodic"
    MAJOR = "major"
    EXCHANGE = "exchange"
    HOLDING = "holding"


class ManifestFileFormat(StrEnum):
    """File-package labels as represented in corpus/manifest.jsonl."""

    XML = "xml"
    PDF_HTML = "pdf+html"


class ContentFormat(StrEnum):
    """Detected content format, which may differ from the file extension."""

    DART_XML = "dart_xml"
    HTML = "html"
    PDF = "pdf"
    UNKNOWN = "unknown"


class ParserProfile(StrEnum):
    """Parser selected after inspecting the actual content."""

    DART_DOCUMENT_XML = "dart_document_xml"
    XFORMS_HTML = "xforms_html"
    PDF_TEXT = "pdf_text"
    COMPANION_HTML = "companion_html"
    UNKNOWN = "unknown"


class SourceRole(StrEnum):
    """Role of one physical file inside a filing package."""

    PRIMARY_REPORT = "primary_report"
    SEPARATE_AUDIT_REPORT = "separate_audit_report"
    CONSOLIDATED_AUDIT_REPORT = "consolidated_audit_report"
    COMPANION_VIEWER = "companion_viewer"
    ATTACHMENT = "attachment"
    UNKNOWN = "unknown"


class BlockType(StrEnum):
    """Semantic block types preserved by the canonical representation."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    NOTE = "note"
    IMAGE = "image"
    PAGE_BREAK = "page_break"
    UNKNOWN = "unknown"


class ParseStatus(StrEnum):
    """Outcome of parsing a source file or semantic document."""

    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class IssueSeverity(StrEnum):
    """Severity assigned to a parse or quality issue."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CorrectionType(StrEnum):
    """Correction classification without assuming lineage is already known."""

    NONE = "none"
    FILING_CORRECTION = "filing_correction"
    ATTACHMENT_ADDITION = "attachment_addition"
    OTHER = "other"


class AmendmentRelationType(StrEnum):
    """Directed relationship between filing packages."""

    CORRECTS = "corrects"
    CORRECTED_BY = "corrected_by"
    FOLLOWS_UP = "follows_up"
    SUPERSEDES = "supersedes"


class CorpusManifestEntry(CanonicalModel):
    """Validated representation of one row in corpus/manifest.jsonl."""

    doc_id: str = Field(min_length=1)
    corp_code: str = Field(pattern=r"^\d{8}$")
    corp_name: str = Field(min_length=1)
    listed_name: str = Field(min_length=1)
    stock_code: str = Field(pattern=r"^\d{6}$")
    industry: str = Field(min_length=1)
    sector: str = Field(min_length=1)
    doc_group: DocumentGroup
    doc_subtype: str | None = None
    report_nm: str = Field(min_length=1)
    is_correction: bool
    rcept_no: str = Field(pattern=r"^\d{14}$")
    rcept_dt: str = Field(pattern=r"^\d{8}$")
    flr_nm: str = Field(min_length=1)
    base_year: int | None = Field(default=None, ge=1900, le=2200)
    base_month: int | None = Field(default=None, ge=1, le=12)
    file_path: str = Field(min_length=1)
    file_format: ManifestFileFormat
    n_files: int = Field(ge=1)

    @field_validator(
        "doc_id",
        "corp_name",
        "listed_name",
        "industry",
        "sector",
        "doc_subtype",
        "report_nm",
        "flr_nm",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_nfc(value).strip()
        return value

    @model_validator(mode="after")
    def validate_reporting_period(self) -> Self:
        has_year = self.base_year is not None
        has_month = self.base_month is not None
        if has_year != has_month:
            raise ValueError("base_year and base_month must be provided together")
        if self.doc_group is DocumentGroup.PERIODIC and not has_year:
            raise ValueError("periodic manifest entries require base_year and base_month")
        if self.doc_group is not DocumentGroup.PERIODIC and has_year:
            raise ValueError("base_year and base_month are only valid for periodic entries")
        return self

    @property
    def receipt_date(self) -> date:
        """Receipt date parsed from the DART YYYYMMDD representation."""

        return date(
            int(self.rcept_dt[:4]),
            int(self.rcept_dt[4:6]),
            int(self.rcept_dt[6:]),
        )

    @property
    def normalized_file_path(self) -> str:
        """NFC-normalized POSIX path used for ZIP entry matching."""

        return normalize_nfc(self.file_path.replace("\\", "/"))


class CompanyIdentity(CanonicalModel):
    """Stable identity and classification for a listed company."""

    corp_code: str = Field(pattern=r"^\d{8}$")
    stock_code: str = Field(pattern=r"^\d{6}$")
    corp_name: str = Field(min_length=1)
    listed_name: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    sector: str = Field(min_length=1)

    @field_validator("corp_name", "listed_name", "industry", "sector", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_nfc(value).strip()
        return value


class FilingMetadata(CanonicalModel):
    """Receipt-level metadata shared by every document in a filing package."""

    doc_id: str = Field(min_length=1)
    document_group: DocumentGroup
    document_subtype: str | None = None
    report_name_raw: str = Field(min_length=1)
    receipt_number: str = Field(pattern=r"^\d{14}$")
    receipt_date: date
    filer_name: str = Field(min_length=1)
    base_year: int | None = Field(default=None, ge=1900, le=2200)
    base_month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def validate_reporting_period(self) -> Self:
        has_year = self.base_year is not None
        has_month = self.base_month is not None
        if has_year != has_month:
            raise ValueError("base_year and base_month must be provided together")
        if self.document_group is DocumentGroup.PERIODIC and not has_year:
            raise ValueError("periodic filings require base_year and base_month")
        if self.document_group is not DocumentGroup.PERIODIC and has_year:
            raise ValueError("base_year and base_month are only valid for periodic filings")
        return self


class CorrectionMetadata(CanonicalModel):
    """Correction status and lineage hints extracted from the filing."""

    is_correction: bool = False
    correction_type: CorrectionType = CorrectionType.NONE
    original_receipt_number: str | None = Field(default=None, pattern=r"^\d{14}$")
    original_filing_date: date | None = None
    correction_reason_raw: str | None = None
    corrected_items_raw: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_type(self) -> Self:
        if not self.is_correction and self.correction_type is not CorrectionType.NONE:
            raise ValueError("non-correction filings must use correction_type='none'")
        if self.is_correction and self.correction_type is CorrectionType.NONE:
            raise ValueError("correction filings require a non-'none' correction_type")
        return self


class SourceFile(CanonicalModel):
    """One physical file belonging to a receipt-level filing package."""

    source_file_id: str = Field(min_length=1)
    archive_path_raw: str = Field(min_length=1)
    archive_path_normalized: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    source_role: SourceRole = SourceRole.UNKNOWN
    declared_extension: str = Field(min_length=1)
    detected_content_format: ContentFormat = ContentFormat.UNKNOWN
    parser_profile: ParserProfile = ParserProfile.UNKNOWN
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    is_primary: bool = False
    companion_to_source_file_id: str | None = None

    @field_validator("archive_path_normalized", mode="before")
    @classmethod
    def normalize_archive_path(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_nfc(value.replace("\\", "/"))
        return value

    @field_validator("file_name", mode="before")
    @classmethod
    def normalize_file_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_nfc(value)
        return value

    @field_validator("declared_extension", mode="before")
    @classmethod
    def normalize_extension(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.lower().lstrip(".")
        return value

    @model_validator(mode="after")
    def validate_file_identity(self) -> Self:
        path_name = PurePosixPath(self.archive_path_normalized).name
        if path_name != self.file_name:
            raise ValueError("file_name must match archive_path_normalized basename")
        if self.source_role is SourceRole.COMPANION_VIEWER:
            if not self.companion_to_source_file_id:
                raise ValueError("companion viewer must reference its primary source file")
            if self.is_primary:
                raise ValueError("companion viewer cannot be a primary source")
        return self


class SourceLocator(CanonicalModel):
    """Traceable location of a canonical object in one source file."""

    source_file_id: str = Field(min_length=1)
    xpath: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    element_index: int | None = Field(default=None, ge=0)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_char_range(self) -> Self:
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end must be greater than or equal to char_start")
        return self


class CanonicalSection(CanonicalModel):
    """Hierarchical section preserved from the original filing."""

    section_id: str = Field(min_length=1)
    parent_section_id: str | None = None
    order: int = Field(ge=0)
    level: int = Field(ge=0)
    title_raw: str | None = None
    title_normalized: str | None = None
    source_locator: SourceLocator | None = None
    attributes_raw: dict[str, Any] = Field(default_factory=dict)


class TableCell(CanonicalModel):
    """Loss-minimising table cell, including spans and DART fact attributes."""

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    is_header: bool = False
    text_raw: str = ""
    text_normalized: str = ""
    numeric_value: Decimal | None = None
    unit_raw: str | None = None
    currency: str | None = None
    concept_code: str | None = None
    context_ref: str | None = None
    decimals_raw: str | None = None
    is_negated: bool | None = None
    attributes_raw: dict[str, Any] = Field(default_factory=dict)
    source_locator: SourceLocator | None = None


class TableData(CanonicalModel):
    """Canonical table structure without flattening merged or empty cells."""

    table_id: str = Field(min_length=1)
    caption_raw: str | None = None
    caption_normalized: str | None = None
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    header_row_indices: list[int] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    attributes_raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        coordinates: set[tuple[int, int]] = set()
        for cell in self.cells:
            coordinate = (cell.row_index, cell.column_index)
            if coordinate in coordinates:
                raise ValueError(f"duplicate table-cell coordinate: {coordinate}")
            coordinates.add(coordinate)
            if cell.row_index + cell.row_span > self.row_count:
                raise ValueError("table cell row span exceeds row_count")
            if cell.column_index + cell.column_span > self.column_count:
                raise ValueError("table cell column span exceeds column_count")
        if any(row >= self.row_count for row in self.header_row_indices):
            raise ValueError("header row index exceeds row_count")
        return self


class CanonicalBlock(CanonicalModel):
    """Ordered semantic content block inside a canonical document."""

    block_id: str = Field(min_length=1)
    section_id: str | None = None
    order: int = Field(ge=0)
    block_type: BlockType
    text_raw: str | None = None
    text_normalized: str | None = None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    table: TableData | None = None
    source_locator: SourceLocator | None = None
    attributes_raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.block_type is BlockType.TABLE and self.table is None:
            raise ValueError("table blocks require table data")
        if self.block_type is not BlockType.TABLE and self.table is not None:
            raise ValueError("only table blocks may contain table data")
        if self.block_type is BlockType.HEADING and self.heading_level is None:
            raise ValueError("heading blocks require heading_level")
        return self


class ParseIssue(CanonicalModel):
    """Recoverable parser or data-quality issue retained for auditing."""

    issue_code: str = Field(min_length=1)
    severity: IssueSeverity
    message: str = Field(min_length=1)
    occurrence_count: int = Field(default=1, ge=1)
    source_file_id: str | None = None
    source_locator: SourceLocator | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ParseSummary(CanonicalModel):
    """File/document-level parsing outcome and diagnostic counters."""

    status: ParseStatus = ParseStatus.PENDING
    parser_name: str | None = None
    parser_version: str | None = None
    detected_encoding: str | None = None
    recovered: bool = False
    source_element_count: int | None = Field(default=None, ge=0)
    emitted_section_count: int = Field(default=0, ge=0)
    emitted_block_count: int = Field(default=0, ge=0)
    emitted_table_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)


class AmendmentRelation(CanonicalModel):
    """Explicit or inferred relationship between two filing packages."""

    relation_type: AmendmentRelationType
    target_filing_id: str = Field(min_length=1)
    target_receipt_number: str | None = Field(default=None, pattern=r"^\d{14}$")
    evidence_raw: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CanonicalDocument(CanonicalModel):
    """One semantic document produced from one or more physical source files."""

    document_id: str = Field(min_length=1)
    filing_id: str = Field(min_length=1)
    document_role: SourceRole
    title_raw: str | None = None
    title_normalized: str | None = None
    primary_source_file_id: str = Field(min_length=1)
    source_file_ids: list[str] = Field(min_length=1)
    sections: list[CanonicalSection] = Field(default_factory=list)
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    parse_summary: ParseSummary = Field(default_factory=ParseSummary)
    parse_issues: list[ParseIssue] = Field(default_factory=list)
    attributes_raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_files(self) -> Self:
        if len(self.source_file_ids) != len(set(self.source_file_ids)):
            raise ValueError("source_file_ids must be unique")
        if self.primary_source_file_id not in self.source_file_ids:
            raise ValueError("primary_source_file_id must be listed in source_file_ids")
        return self


class FilingPackage(CanonicalModel):
    """Receipt-level canonical object tying metadata, files, and documents together."""

    schema_version: str = SCHEMA_VERSION
    filing_id: str = Field(min_length=1)
    company: CompanyIdentity
    filing: FilingMetadata
    correction: CorrectionMetadata = Field(default_factory=CorrectionMetadata)
    amendment_relations: list[AmendmentRelation] = Field(default_factory=list)
    source_files: list[SourceFile] = Field(min_length=1)
    documents: list[CanonicalDocument] = Field(min_length=1)
    package_issues: list[ParseIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.filing_id != self.filing.doc_id:
            raise ValueError("filing_id must match filing.doc_id")

        source_ids = [source.source_file_id for source in self.source_files]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_file_id values must be unique within a package")

        document_ids = [document.document_id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("document_id values must be unique within a package")

        source_id_set = set(source_ids)
        for source in self.source_files:
            if (
                source.companion_to_source_file_id is not None
                and source.companion_to_source_file_id not in source_id_set
            ):
                raise ValueError("companion_to_source_file_id is not in source_files")

        for document in self.documents:
            if document.filing_id != self.filing_id:
                raise ValueError("every document filing_id must match package filing_id")
            missing = set(document.source_file_ids) - source_id_set
            if missing:
                raise ValueError(f"document references unknown source files: {sorted(missing)}")
        return self


def company_from_manifest(entry: CorpusManifestEntry) -> CompanyIdentity:
    """Create stable company metadata from a validated manifest entry."""

    return CompanyIdentity(
        corp_code=entry.corp_code,
        stock_code=entry.stock_code,
        corp_name=entry.corp_name,
        listed_name=entry.listed_name,
        industry=entry.industry,
        sector=entry.sector,
    )


def filing_from_manifest(entry: CorpusManifestEntry) -> FilingMetadata:
    """Create receipt-level filing metadata from a manifest entry."""

    return FilingMetadata(
        doc_id=entry.doc_id,
        document_group=entry.doc_group,
        document_subtype=entry.doc_subtype,
        report_name_raw=entry.report_nm,
        receipt_number=entry.rcept_no,
        receipt_date=entry.receipt_date,
        filer_name=entry.flr_nm,
        base_year=entry.base_year,
        base_month=entry.base_month,
    )


def correction_from_manifest(entry: CorpusManifestEntry) -> CorrectionMetadata:
    """Build initial correction metadata before document-level lineage parsing."""

    correction_type = (
        CorrectionType.FILING_CORRECTION if entry.is_correction else CorrectionType.NONE
    )
    return CorrectionMetadata(
        is_correction=entry.is_correction,
        correction_type=correction_type,
    )
