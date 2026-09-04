"""Stable public request and response schemas for the contract API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractQueryRequest(BaseModel):
    """A bounded four-field supply-contract question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=10_000)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    corp_code: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    date_from: date | None = None
    date_to: date | None = None
    corrections: Literal["all", "only", "exclude"] = "all"
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def question_must_have_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain visible text")
        return value.strip()

    @model_validator(mode="after")
    def date_order_is_valid(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be later than date_to")
        return self


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_number: str
    url: str | None
    url_status: str
    filing_id: str
    document_id: str
    section_id: str | None
    chunk_id: str
    source_table_id: str | None
    source_block_ids: list[str]
    correction_status: str
    lineage_status: str


class RunIdentity(BaseModel):
    embedding_run_id: str
    chunk_run_id: str


class Usage(BaseModel):
    query_tokens: int = Field(ge=0)
    embedding_provider_calls: int = Field(ge=0)
    extraction_provider_calls: int = Field(ge=0)
    database_writes: Literal[0] = 0


class ContractQueryResponse(BaseModel):
    """Evidence-bearing response; no free-form model generation is required."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["contract-query-api-v1"] = "contract-query-api-v1"
    request_id: str
    status: Literal[
        "completed",
        "partial",
        "no_results",
        "unsupported",
        "clarification_required",
    ]
    answer: str
    reason_code: str | None = None
    reason: str | None = None
    interpreted_query: dict[str, Any]
    applied_filters: dict[str, Any]
    findings: list[dict[str, Any]]
    citations: list[Citation]
    limitations: list[str]
    retrieval: dict[str, Any] | None = None
    run: RunIdentity
    usage: Usage
    timing_seconds: dict[str, float]


class ErrorResponse(BaseModel):
    schema_version: Literal["contract-query-error-v1"] = "contract-query-error-v1"
    request_id: str
    status: Literal["error", "clarification_required"]
    error_code: str
    message: str


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    embedding_run_id: str
    chunk_run_id: str
    model: str
    input_version: str
    provider_checked: Literal[False]
    database_writes: Literal[0]
