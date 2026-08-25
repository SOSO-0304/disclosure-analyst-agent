"""Canonical domain models for disclosure parsing."""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class DisclosureType(StrEnum):
    PERIODIC = "periodic"
    EXCHANGE = "exchange"
    MAJOR = "major"
    HOLDING = "holding"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Company(DomainModel):
    corp_code: str
    corp_name: str
    listed_name: str | None = None
    stock_code: str | None = None
    industry: str | None = None
    sector: str | None = None


class SourceFile(DomainModel):
    path: str
    file_name: str | None = None
    order: int = 0


class DisclosureMetadata(DomainModel):
    report_name: str
    receipt_no: str
    receipt_date: date
    doc_group: str
    doc_subtype: str | None = None
    filer_name: str | None = None
    base_year: int | None = None
    base_month: int | None = None


class RevisionInfo(DomainModel):
    is_correction: bool = False
    corrects_document_id: str | None = None
    revision_order: int | None = None
    is_latest: bool | None = None


class DisclosureField(DomainModel):
    label: str
    value: str | None = None
    path: list[str] = Field(default_factory=list)
    code: str | None = None
    unit: str | None = None
    unit_value: str | None = None
    raw_value: str | None = None
    order: int | None = None


class TableCell(DomainModel):
    text: str | None = None
    code: str | None = None
    unit: str | None = None
    unit_value: str | None = None
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    is_header: bool = False


class TableRow(DomainModel):
    cells: list[TableCell] = Field(default_factory=list)


class DisclosureTable(DomainModel):
    table_id: str
    title: str | None = None
    rows: list[TableRow] = Field(default_factory=list)
    section_path: list[str] = Field(default_factory=list)
    order: int | None = None


class TextBlock(DomainModel):
    text: str
    order: int | None = None


class Section(DomainModel):
    section_id: str
    level: int = Field(ge=1)
    title: str | None = None
    path: list[str] = Field(default_factory=list)
    text_blocks: list[TextBlock] = Field(default_factory=list)
    fields: list[DisclosureField] = Field(default_factory=list)
    tables: list[DisclosureTable] = Field(default_factory=list)
    children: list["Section"] = Field(default_factory=list)
    order: int | None = None


class PeriodicPayload(DomainModel):
    kind: Literal["periodic"] = "periodic"
    fiscal_year: int | None = None
    sections: list[Section] = Field(default_factory=list)


class ExchangePayload(DomainModel):
    kind: Literal["exchange"] = "exchange"
    title: str
    fields: list[DisclosureField] = Field(default_factory=list)
    tables: list[DisclosureTable] = Field(default_factory=list)


class MajorPayload(DomainModel):
    kind: Literal["major"] = "major"
    sections: list[Section] = Field(default_factory=list)
    fields: list[DisclosureField] = Field(default_factory=list)


class HoldingPayload(DomainModel):
    kind: Literal["holding"] = "holding"
    sections: list[Section] = Field(default_factory=list)
    fields: list[DisclosureField] = Field(default_factory=list)


CanonicalPayload = Annotated[
    PeriodicPayload | ExchangePayload | MajorPayload | HoldingPayload,
    Field(discriminator="kind"),
]


class CanonicalDisclosure(DomainModel):
    schema_version: str = "1.0"
    document_id: str
    document_type: DisclosureType
    company: Company
    metadata: DisclosureMetadata
    revision: RevisionInfo = Field(default_factory=RevisionInfo)
    source_files: list[SourceFile] = Field(default_factory=list)
    payload: CanonicalPayload
