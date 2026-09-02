from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy

import pytest

from disclosure_agent.retrieval.contract_answers import (
    contract_findings,
    render_contract_findings,
    source_table_sql,
)
from disclosure_agent.retrieval.search import scope_sql

RUN = {"embedding_run_id": "v2", "chunk_run_id": "chunks"}


def cell(row, column, value, **kwargs):
    return {
        "row_index": row,
        "column_index": column,
        "text_raw": value,
        "text_normalized": value,
        "source_locator": {
            "source_file_id": "source-1",
            "xpath": f"/table/tr[{row + 1}]/td[{column + 1}]",
        },
        **kwargs,
    }


def fixture():
    hit = {
        "chunk_id": "chunk",
        "filing_id": "filing",
        "document_id": "document",
        "source_table_id": "table",
        "source_block_ids": ["block"],
        "chunk_type": "table",
        "content_sha256": "hash",
        "content": "this is only a truncated retrieval fragment",
        "company_name": "테스트회사",
        "corp_code": "001",
        "is_correction": False,
        "report_name": "단일판매ㆍ공급계약체결",
        "receipt_date": "2025-07-07",
        "receipt_number": "20250707800058",
    }
    row = {
        "chunk_id": "chunk",
        "chunk_content_sha256": "hash",
        "table_id": "table",
        "block_id": "block",
        "document_id": "document",
        "filing_id": "filing",
        "load_run_id": "source-load",
        "row_count": 4,
        "column_count": 3,
        "caption_raw": None,
        "caption_normalized": None,
        "document_group": "exchange",
        "document_subtype": "단일판매공급계약체결",
        "parse_status": "success",
        "recovered": False,
        "grid": {
            "cells": [
                cell(0, 0, "2. 계약내역"),
                cell(0, 1, "계약금액(원)"),
                cell(0, 2, "869,400,000,000"),
                cell(1, 0, "3. 계약상대", column_span=2),
                cell(1, 2, "아프리카 지역 선주"),
                cell(2, 0, "5. 계약기간", row_span=2),
                cell(2, 1, "시작일"),
                cell(2, 2, "2025-07-04"),
                cell(3, 1, "종료일"),
                cell(3, 2, "2025-09-30"),
            ]
        },
    }
    return hit, row


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


class Connection:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return Result(self.rows)


def extract(hit, row):
    return contract_findings(Connection([row]), run=RUN, hits=[hit])


def test_complete_fields_expand_full_table_and_preserve_literal_counterparty_and_cells():
    hit, row = fixture()
    before = deepcopy(row)
    report = extract(hit, row)
    finding = report["findings"][0]
    assert report["status"] == "completed"
    assert report["extraction_provider_calls"] == report["database_writes"] == 0
    assert finding["fields"]["contract_amount"]["value"] == "869400000000"
    assert finding["fields"]["contract_amount"]["unit"] == "KRW"
    assert finding["fields"]["counterparty"]["value"] == "아프리카 지역 선주"
    assert finding["fields"]["contract_start_date"]["value"] == "2025-07-04"
    evidence = finding["fields"]["contract_end_date"]["evidence"][0]
    assert evidence["table_id"] == "table" and evidence["row_index"] == 3
    assert evidence["value_locator"]["xpath"] == "/table/tr[4]/td[3]"
    assert evidence["label_locators"][0]["xpath"] == "/table/tr[3]/td[1]"
    assert finding["evidence_scope"] == "full_source_table"
    assert len(finding["table_snapshot_sha256"]) == 64
    assert row == before
    rendered = render_contract_findings(report)
    assert "869,400,000,000원" in rendered and "행 4, 열 3" in rendered
    assert "20250707800058" in rendered
    assert "최신 유효 계약" in rendered


@pytest.mark.parametrize(
    "value,status",
    [
        ("-", "missing"),
        ("비공개", "withheld"),
        ("100.5", "invalid"),
        ("1e9", "invalid"),
        ("NaN", "invalid"),
        ("Infinity", "invalid"),
        ("-100", "invalid"),
        ("12,34", "invalid"),
        ("약 100억원", "invalid"),
        ("100 USD", "invalid"),
        ("미정", "invalid"),
    ],
)
def test_amount_never_guesses_or_truncates(value, status):
    hit, row = fixture()
    row["grid"]["cells"][2].update(text_raw=value, text_normalized=value)
    field = extract(hit, row)["findings"][0]["fields"]["contract_amount"]
    assert field["status"] == status and field["value"] is None
    assert field["evidence"][0]["raw_value"] == value


def test_new_form_amount_without_explicit_unit_is_not_assumed_krw():
    hit, row = fixture()
    row["grid"]["cells"][1].update(text_normalized="확정 계약금액", text_raw="확정 계약금액")
    field = extract(hit, row)["findings"][0]["fields"]["contract_amount"]
    assert field["status"] == "unit_unknown" and field["value"] is None
    row["grid"]["cells"][2]["unit_raw"] = "원"
    assert extract(hit, row)["findings"][0]["fields"]["contract_amount"]["status"] == "extracted"
    row["grid"]["cells"][2]["currency"] = "USD"
    assert (
        extract(hit, row)["findings"][0]["fields"]["contract_amount"]["status"]
        == "unsupported_unit"
    )


def test_zero_and_large_money_values_are_exact_decimal_strings():
    hit, row = fixture()
    for value in ("0", "9007199254740993", "+10,000.00원"):
        row["grid"]["cells"][2].update(text_raw=value, text_normalized=value)
        field = extract(hit, row)["findings"][0]["fields"]["contract_amount"]
        assert (
            field["value"]
            == {"0": "0", "9007199254740993": "9007199254740993", "+10,000.00원": "10000"}[value]
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2025.7.4", "2025-07-04"),
        ("2025/07/04", "2025-07-04"),
        ("2025-02-30", None),
        ("2025-07-04 (예정)", None),
        ("미정", None),
    ],
)
def test_date_normalization_requires_a_complete_valid_date(value, expected):
    hit, row = fixture()
    row["grid"]["cells"][7].update(text_raw=value, text_normalized=value)
    assert extract(hit, row)["findings"][0]["fields"]["contract_start_date"]["value"] == expected


def test_reversed_period_is_not_silently_swapped():
    hit, row = fixture()
    row["grid"]["cells"][7].update(text_raw="2026-01-01", text_normalized="2026-01-01")
    fields = extract(hit, row)["findings"][0]["fields"]
    for name in ("contract_start_date", "contract_end_date"):
        assert fields[name]["status"] == "inconsistent_period" and fields[name]["value"] is None
        assert fields[name]["evidence"]


def test_conflicting_values_are_ambiguous_even_with_preferred_alias():
    hit, row = fixture()
    row["row_count"] = 5
    row["grid"]["cells"].extend(
        [cell(4, 0, "3. 계약상대방", column_span=2), cell(4, 2, "다른 회사")]
    )
    field = extract(hit, row)["findings"][0]["fields"]["counterparty"]
    assert field["status"] == "ambiguous" and field["value"] is None
    assert len(field["evidence"]) == 2


def test_repeated_contract_forms_do_not_merge_even_identical_values():
    hit, row = fixture()
    row["row_count"] = 6
    for number in (4, 5):
        row["grid"]["cells"].extend(
            [cell(number, 0, "1. 판매ㆍ공급계약 구분", column_span=2), cell(number, 2, "공사수주")]
        )
    finding = extract(hit, row)["findings"][0]
    assert finding["status"] == "invalid_or_ambiguous_table" and finding["fields"] == {}


@pytest.mark.parametrize(
    "key,value",
    [
        ("table_id", "other"),
        ("filing_id", "other"),
        ("document_id", "other"),
        ("chunk_content_sha256", "stale"),
        ("block_id", "other"),
    ],
)
def test_source_identity_mismatch_never_supplies_fields(key, value):
    hit, row = fixture()
    row[key] = value
    assert extract(hit, row)["findings"][0]["status"] == "source_mismatch"


@pytest.mark.parametrize(
    "change,status",
    [
        ({"document_subtype": "단일판매공급계약해지"}, "unsupported_filing"),
        ({"document_group": "major"}, "unsupported_filing"),
        ({"grid": None}, "table_limit_exceeded"),
        ({"row_count": 1}, "invalid_or_ambiguous_table"),
        ({"column_count": 50000}, "invalid_or_ambiguous_table"),
    ],
)
def test_unsupported_or_invalid_source_is_explicit(change, status):
    hit, row = fixture()
    row.update(change)
    result = extract(hit, row)["findings"][0]
    assert result["status"] == status and result["fields"] == {}


def test_missing_source_does_not_fall_back_to_chunk_text():
    hit, _ = fixture()
    report = contract_findings(Connection(), run=RUN, hits=[hit])
    assert report["findings"][0]["status"] == "source_unavailable"


def test_corrections_and_distinct_filings_are_not_merged_and_recovery_warns():
    hit, row = fixture()
    correction, other = deepcopy(hit), deepcopy(row)
    correction.update(
        chunk_id="other",
        filing_id="correction",
        is_correction=True,
        source_table_id="other-table",
        receipt_number="20250801800001",
    )
    other.update(chunk_id="other", filing_id="correction", table_id="other-table", recovered=True)
    report = contract_findings(Connection([row, other]), run=RUN, hits=[hit, correction])
    assert len(report["findings"]) == 2
    assert report["findings"][1]["warnings"]
    assert report["findings"][1]["citation"]["lineage_status"] == "not_resolved"


def test_duplicate_fragments_dedup_per_source_table():
    hit, row = fixture()
    report = contract_findings(Connection([row]), run=RUN, hits=[hit, dict(hit)])
    assert len(report["findings"]) == 1


def test_empty_and_narrative_results_make_no_expansion_queries():
    connection = Connection()
    empty = contract_findings(connection, run=RUN, hits=[])
    assert empty["status"] == "no_results"
    assert "계약이 없다는 뜻은 아닙니다" in render_contract_findings(empty)
    hit, _ = fixture()
    hit.update(chunk_type="narrative", source_table_id=None)
    assert (
        contract_findings(connection, run=RUN, hits=[hit])["findings"][0]["status"]
        == "unsupported_chunk"
    )
    assert connection.calls == []


def test_bounded_expansion_uses_shared_scope_and_current_source_load():
    hit, row = fixture()
    connection = Connection([row])
    filters = {"corp_code": "x' OR true --", "corrections": "only", "date_from": "2025-01-01"}
    contract_findings(connection, run=RUN, hits=[hit], filters=filters)
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    where, _ = scope_sql(RUN, **filters)
    assert sql == source_table_sql(where) and where in sql
    assert "OR true" not in sql and params["corp_code"] == "x' OR true --"
    assert params["ids"] == ["chunk"] and params["run_id"] == "v2"
    assert "t.load_run_id = cr.source_load_run_id" in sql
    assert "f.load_run_id = cr.source_load_run_id" in sql
    assert "d.filing_id = t.filing_id" in sql
    assert "cr.is_active" in sql and "er.status = 'completed'" in sql
    assert "CASE WHEN" in sql and params["max_cells"] == 50000
    with pytest.raises(ValueError, match="20"):
        contract_findings(Connection(), run=RUN, hits=[hit] * 21)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "UPDATE public.source_tables SET load_run_id = 'other'",
        "UPDATE public.source_documents SET load_run_id = 'other'",
        "UPDATE public.source_filings SET load_run_id = 'other'",
        "UPDATE public.source_tables SET document_id = 'other'",
        "UPDATE public.source_tables SET filing_id = 'other'",
        "UPDATE public.retrieval_embeddings SET chunk_content_sha256 = 'stale'",
        "UPDATE public.retrieval_embeddings SET embedding_run_id = 'v1'",
        "UPDATE public.embedding_runs SET status = 'partial'",
        "UPDATE public.retrieval_chunk_runs SET is_active = 0",
        "UPDATE public.source_filings SET corp_code = '002'",
    ],
)
def test_actual_expansion_sql_rejects_mixed_snapshots_and_scope(mutation):
    # Execute the production joins in portable SQL. This is not a PG planner test.
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
        conn.create_function("octet_length", 1, lambda value: len(value.encode()))
        conn.executescript("""
            ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.retrieval_embeddings (
                chunk_id TEXT, chunk_run_id TEXT, embedding_run_id TEXT, chunk_content_sha256 TEXT
            );
            CREATE TABLE public.retrieval_chunks (
                chunk_id TEXT, chunk_run_id TEXT, filing_id TEXT, document_id TEXT,
                source_table_id TEXT, content TEXT, content_sha256 TEXT, chunk_type TEXT
            );
            CREATE TABLE public.source_filings (
                filing_id TEXT, load_run_id TEXT, corp_code TEXT,
                document_group TEXT, document_subtype TEXT, is_correction BOOLEAN
            );
            CREATE TABLE public.embedding_runs (
                embedding_run_id TEXT, chunk_run_id TEXT, status TEXT
            );
            CREATE TABLE public.retrieval_chunk_runs (
                chunk_run_id TEXT, source_load_run_id TEXT, is_active BOOLEAN, status TEXT
            );
            CREATE TABLE public.source_documents (
                document_id TEXT, filing_id TEXT, load_run_id TEXT,
                parse_status TEXT, recovered BOOLEAN
            );
            CREATE TABLE public.source_tables (
                table_id TEXT, block_id TEXT, document_id TEXT, filing_id TEXT,
                load_run_id TEXT, row_count INTEGER, column_count INTEGER, caption_raw TEXT,
                caption_normalized TEXT, source_locator TEXT, grid TEXT
            );
            INSERT INTO public.retrieval_embeddings VALUES ('chunk', 'chunks', 'v2', 'hash');
            INSERT INTO public.retrieval_chunks VALUES
                ('chunk', 'chunks', 'filing', 'document', 'table', '계약', 'hash', 'table');
            INSERT INTO public.source_filings VALUES
                ('filing', 'source-load', '001', 'exchange', '단일판매공급계약체결', 0);
            INSERT INTO public.embedding_runs VALUES ('v2', 'chunks', 'completed');
            INSERT INTO public.retrieval_chunk_runs VALUES
                ('chunks', 'source-load', 1, 'completed');
            INSERT INTO public.source_documents VALUES
                ('document', 'filing', 'source-load', 'success', 0);
        """)
        _, row = fixture()
        conn.execute(
            "INSERT INTO public.source_tables VALUES "
            "('table', 'block', 'document', 'filing', 'source-load', 4, 3, NULL, NULL, NULL, ?)",
            (json.dumps(row["grid"]),),
        )
        if mutation:
            conn.execute(mutation)
        where, params = scope_sql(RUN, corp_code="001", corrections="exclude")
        sql = (
            source_table_sql(where)
            .replace("c.content ~", "c.content REGEXP")
            .replace("t.grid::text", "t.grid")
        )
        sql = sql.replace("c.chunk_id = ANY(CAST(:ids AS varchar[]))", "c.chunk_id = :selected_id")
        rows = conn.execute(
            sql, {**params, "selected_id": "chunk", "max_cells": 50000, "max_bytes": 2000000}
        ).fetchall()
        assert len(rows) == (1 if mutation is None else 0)
        if rows:
            assert json.loads(rows[0]["grid"]) == row["grid"]
