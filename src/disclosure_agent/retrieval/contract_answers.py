"""Bounded, read-only contract facts from retrieved canonical tables, not an LLM."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Any

import orjson
from pydantic import ValidationError
from sqlalchemy import Connection, text

from disclosure_agent.domain.models import TableData
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField
from disclosure_agent.extractors.supply_contract import ALIASES
from disclosure_agent.retrieval.hybrid import citation
from disclosure_agent.retrieval.search import JOINS, scope_sql

FIELDS = ("counterparty", "contract_amount", "contract_start_date", "contract_end_date")
FIELD_LABELS = {
    "counterparty": "계약상대방",
    "contract_amount": "계약금액",
    "contract_start_date": "계약 시작일",
    "contract_end_date": "계약 종료일",
}
MAX_RESULTS = 20
MAX_GRID_CELLS = 50_000
MAX_GRID_BYTES = 2_000_000
MISSING = {"", "-", "—", "해당없음", "해당 없음", "없음"}
LIMITATIONS = [
    "검색된 표에서 확인한 공시별 값이며, 회사의 모든 계약을 조회한 결과가 아닙니다.",
    "정정·해지 연결은 적용하지 않았으므로 최신 유효 계약이나 현재 잔여 수주를 뜻하지 않습니다.",
    "다른 표·공시의 값으로 빈칸을 채우지 않으며, 계약상대방은 원문 표현을 유지합니다.",
]


def source_table_sql(where: str) -> str:
    """Expand only selected hits, retaining scope/hash and source-load identities."""
    return f"""
        SELECT c.chunk_id, c.content_sha256 AS chunk_content_sha256,
            t.table_id, t.block_id, t.document_id, t.filing_id, t.load_run_id,
            t.row_count, t.column_count, t.caption_raw, t.caption_normalized,
            t.source_locator, f.document_group, f.document_subtype,
            d.parse_status, d.recovered,
            CASE WHEN t.row_count * CAST(t.column_count AS bigint) <= :max_cells
                AND octet_length(t.grid::text) <= :max_bytes
                THEN t.grid ELSE NULL END AS grid
        {JOINS}
        JOIN public.embedding_runs er ON er.embedding_run_id = e.embedding_run_id
            AND er.chunk_run_id = e.chunk_run_id AND er.status = 'completed'
        JOIN public.retrieval_chunk_runs cr ON cr.chunk_run_id = c.chunk_run_id
            AND cr.is_active AND cr.status = 'completed'
        JOIN public.source_tables t ON t.table_id = c.source_table_id
            AND t.document_id = c.document_id AND t.filing_id = c.filing_id
            AND t.load_run_id = cr.source_load_run_id
        JOIN public.source_documents d ON d.document_id = t.document_id
            AND d.filing_id = t.filing_id AND d.load_run_id = t.load_run_id
        WHERE {where} AND f.load_run_id = cr.source_load_run_id
            AND c.chunk_id = ANY(CAST(:ids AS varchar[]))
            AND c.chunk_type = 'table'
    """


def _table(row: Mapping[str, Any]) -> TableData:
    grid = row["grid"]
    if not isinstance(grid, dict) or not isinstance(grid.get("cells"), list):
        raise ValueError("Missing table grid")
    if (
        len(grid["cells"]) > MAX_GRID_CELLS
        or int(row["row_count"]) * int(row["column_count"]) > MAX_GRID_CELLS
        or len(orjson.dumps(grid)) > MAX_GRID_BYTES
    ):
        raise ValueError("Table size exceeds bounded extraction")
    return TableData.model_validate(
        {
            "table_id": row["table_id"],
            "row_count": row["row_count"],
            "column_count": row["column_count"],
            "cells": grid["cells"],
            "header_row_indices": grid.get("header_row_indices", []),
            "caption_raw": row.get("caption_raw"),
            "caption_normalized": row.get("caption_normalized"),
        }
    )


def _key(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("·", "ㆍ")


def _normalise(name: str, field: SemanticField, cell: Any) -> tuple[str, str | None, str | None]:
    raw = field.value.strip()
    if raw in MISSING:
        return "missing", None, None
    if any(
        word in _key(raw)
        for word in ("비공개", "미공개", "공시유보", "공개유보", "기재유보", "추후공시")
    ):
        return "withheld", None, None
    if name == "counterparty":
        return "extracted", raw, None
    if name == "contract_amount":
        # Never truncate decimals, parse an exponent, infer FX or infer units from revenue.
        if cell.is_negated:
            return "invalid", None, None
        if not re.fullmatch(r"\+?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.0+)?\s*(?:원)?", raw):
            return "invalid", None, None
        units = {str(unit).strip() for unit in (cell.unit_raw, cell.currency) if unit}
        if units - {"원", "KRW", "krw"}:
            return "unsupported_unit", None, None
        explicit_won = "(원)" in _key(field.path_key) or raw.endswith("원") or bool(units)
        if not explicit_won:
            return "unit_unknown", None, None
        value = Decimal(raw.replace(",", "").removesuffix("원").strip())
        return "extracted", str(int(value)), "KRW"
    match = re.fullmatch(r"([0-9]{4})\s*[-./]\s*([0-9]{1,2})\s*[-./]\s*([0-9]{1,2})\.?", raw)
    if not match:
        return "invalid", None, None
    try:
        value = date(*(int(part) for part in match.groups()))
    except ValueError:
        return "invalid", None, None
    return "extracted", value.isoformat(), None


def _extract_fields(table: TableData, row: Mapping[str, Any]) -> dict[str, Any]:
    fields = ExchangeFieldReader().read_table(
        filing_id=row["filing_id"], document_id=row["document_id"], table=table
    )
    # Distinct repeated form headers can represent multiple contracts within one table.
    for alias in (*ALIASES["contract_type"], *ALIASES["contract_name"]):
        if sum(_key(field.path_key) == _key(alias) for field in fields) > 1:
            raise ValueError("Multiple form headers in one table")
    result = {}
    for name in FIELDS:
        aliases = {_key(alias) for alias in ALIASES[name]}
        matches = [field for field in fields if _key(field.path_key) in aliases]
        evidence = []
        outcomes = []
        for field in matches:
            # Find the value's original anchor, including inherited rowspan values.
            cell = next(
                (
                    cell
                    for cell in table.cells
                    if cell.column_index == field.value_column_index
                    and cell.row_index <= field.row_index < cell.row_index + cell.row_span
                ),
                None,
            )
            if cell is None:
                raise ValueError("Missing canonical value cell")
            status, value, unit = _normalise(name, field, cell)
            outcomes.append((status, value, unit, field.value if value is None else None))
            evidence.append(
                {
                    "field_path": field.path_key,
                    "raw_value": cell.text_raw,
                    "normalized_text": cell.text_normalized,
                    "row_index": cell.row_index,
                    "column_index": cell.column_index,
                    "row_span": cell.row_span,
                    "column_span": cell.column_span,
                    "unit_raw": cell.unit_raw,
                    "currency": cell.currency,
                    "value_locator": cell.source_locator.model_dump(mode="json")
                    if cell.source_locator
                    else None,
                    "label_locators": [
                        locator.model_dump(mode="json") if locator else None
                        for locator in field.label_locators
                    ],
                    "filing_id": field.filing_id,
                    "document_id": field.document_id,
                    "table_id": field.table_id,
                }
            )
        distinct = set(outcomes)
        status, value, unit = ("missing", None, None)
        if len(distinct) == 1:
            status, value, unit, _ = outcomes[0]
        elif len(distinct) > 1:
            status = "ambiguous"
        result[name] = {
            "label": FIELD_LABELS[name],
            "status": status,
            "value": value,
            "unit": unit,
            "evidence": evidence,
        }
    start, end = (result[name] for name in ("contract_start_date", "contract_end_date"))
    if start["value"] and end["value"] and start["value"] > end["value"]:
        for field in (start, end):
            field.update(status="inconsistent_period", value=None)
    return result


def contract_findings(
    connection: Connection,
    *,
    run: Mapping[str, Any],
    hits: Sequence[Mapping[str, Any]],
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read in the search transaction. Do not call any provider or mutate a database."""
    if len(hits) > MAX_RESULTS:
        raise ValueError("Contract field extraction is limited to 20 retrieved results")
    where, params = scope_sql(run, **dict(filters or {}))
    requested = [
        hit for hit in hits if hit.get("source_table_id") and hit.get("chunk_type") == "table"
    ]
    tables = {}
    if requested:
        rows = connection.execute(
            text(source_table_sql(where)),
            {
                **params,
                "ids": [hit["chunk_id"] for hit in requested],
                "max_cells": MAX_GRID_CELLS,
                "max_bytes": MAX_GRID_BYTES,
            },
        ).mappings()
        for row in rows:
            if row["chunk_id"] in tables:
                raise ValueError("Source expansion returned a duplicate chunk identity")
            tables[row["chunk_id"]] = dict(row)
    findings = []
    seen = set()
    for hit in hits:
        identity = (hit["filing_id"], hit.get("source_table_id") or hit["chunk_id"])
        if identity in seen:
            continue
        seen.add(identity)
        item = {
            "chunk_id": hit["chunk_id"],
            "filing_id": hit["filing_id"],
            "document_id": hit["document_id"],
            "table_id": hit.get("source_table_id"),
            "company_name": hit.get("company_name"),
            "corp_code": hit["corp_code"],
            "report_name": hit.get("report_name"),
            "receipt_date": hit.get("receipt_date"),
            "is_correction": hit["is_correction"],
            "citation": citation(hit),
            "evidence_scope": "full_source_table",
            "fields": {},
            "warnings": [],
        }
        findings.append(item)
        row = tables.get(hit["chunk_id"])
        if not hit.get("source_table_id") or hit.get("chunk_type") != "table":
            item["status"] = "unsupported_chunk"
            continue
        if row is None:
            item["status"] = "source_unavailable"
            continue
        if (
            any(
                row[key] != hit[hit_key]
                for key, hit_key in (
                    ("table_id", "source_table_id"),
                    ("filing_id", "filing_id"),
                    ("document_id", "document_id"),
                    ("chunk_content_sha256", "content_sha256"),
                )
            )
            or row["block_id"] not in hit["source_block_ids"]
        ):
            item["status"] = "source_mismatch"
            continue
        if row["document_group"] != "exchange" or row["document_subtype"] != "단일판매공급계약체결":
            item["status"] = "unsupported_filing"
            continue
        item["source_load_run_id"] = row["load_run_id"]
        item["parse_status"] = row["parse_status"]
        if row["recovered"] or row["parse_status"] != "success":
            item["warnings"].append(
                "원본 파싱에 복구 또는 비정상 상태가 있어 근거 셀을 확인해야 합니다."
            )
        if row["grid"] is None:
            item["status"] = "table_limit_exceeded"
            continue
        try:
            table = _table(row)
            item["fields"] = _extract_fields(table, row)
        except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
            item["status"] = "invalid_or_ambiguous_table"
            continue
        item["table_snapshot_sha256"] = sha256(
            orjson.dumps(table.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        for field in item["fields"].values():
            for evidence in field["evidence"]:
                evidence["source_block_id"] = row["block_id"]
                evidence["citation_url"] = item["citation"]["url"]
        count = sum(field["status"] == "extracted" for field in item["fields"].values())
        item["status"] = (
            "extracted" if count == len(FIELDS) else "partial" if count else "no_supported_fields"
        )
    return {
        "schema_version": "retrieval-contract-fields-v1",
        "embedding_run_id": run["embedding_run_id"],
        "chunk_run_id": run["chunk_run_id"],
        "status": "completed"
        if findings and all(item["status"] == "extracted" for item in findings)
        else "partial"
        if findings
        else "no_results",
        "database_writes": 0,
        "extraction_provider_calls": 0,
        "limitations": LIMITATIONS,
        "findings": findings,
    }


def render_contract_findings(report: Mapping[str, Any]) -> str:
    lines = ["=== 계약 필드 추출 (검색된 공시 기준) ==="]
    if report.get("status") in {"unsupported", "clarification_required"}:
        lines.extend([f"상태: {report['status']}", str(report["reason"])])
        return "\n".join(lines)
    lines.extend(f"해석: {note}" for note in report.get("query_plan", {}).get("notes", []))
    for index, item in enumerate(report["findings"], 1):
        correction = "정정공시" if item["is_correction"] else "비정정공시"
        lines.append(f"\n{index}. {item['company_name']} / {item['report_name']} / {correction}")
        lines.append(f"   상태: {item['status']}")
        for name, field in item["fields"].items():
            value = (
                field["value"]
                if field["status"] == "extracted"
                else f"확정하지 않음 ({field['status']})"
            )
            if name == "contract_amount" and field["status"] == "extracted":
                value = f"{int(field['value']):,}원"
            lines.append(f"   {field['label']}: {value}")
            for evidence in field["evidence"]:
                lines.append(
                    f"     근거: {evidence['field_path']} = {evidence['raw_value']} "
                    f"[행 {evidence['row_index'] + 1}, 열 {evidence['column_index'] + 1}]"
                )
        lines.append(f"   표: {item['table_id']}")
        lines.append(f"   출처: {item['citation']['url']}")
        lines.extend(f"   주의: {warning}" for warning in item["warnings"])
    if not report["findings"]:
        lines.append("검색 범위에서 결과를 찾지 못했습니다. 계약이 없다는 뜻은 아닙니다.")
    lines.extend(f"주의: {note}" for note in report["limitations"])
    return "\n".join(lines)
