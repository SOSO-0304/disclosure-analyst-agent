from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.retrieval.source_references import (
    build_source_references,
    render_source_references,
)
from disclosure_agent.storage.db_models import SourceFilingRow


def _pack() -> EvidencePack:
    first = EvidenceItem(
        evidence_id="semantic:c1",
        source_kind="semantic_chunk",
        rank=1,
        score=0.8,
        semantic_score=0.7,
        lexical_score=1.0,
        company_name="카카오",
        filing_id="periodic_1",
        report_name="분기보고서 (2025.09)",
        document_id="document:1",
        section_id="section:1",
        content_text="첫 번째 근거",
        truncated=False,
        matched_terms=("투자",),
        block_ids=("block:1",),
        table_ids=("table:1",),
    )
    second = EvidenceItem(
        evidence_id="semantic:c2",
        source_kind="semantic_chunk",
        rank=2,
        score=0.7,
        semantic_score=0.6,
        lexical_score=0.9,
        company_name="카카오",
        filing_id="periodic_1",
        report_name="분기보고서 (2025.09)",
        document_id="document:1",
        section_id="section:2",
        content_text="두 번째 근거",
        truncated=False,
        matched_terms=("계획",),
        block_ids=("block:2",),
        table_ids=("table:2",),
    )
    return EvidencePack(
        query="투자 계획",
        retrieval_status="MATCHES_FOUND",
        items=(first, second),
        total_chars=len(first.content_text) + len(second.content_text),
    )


def test_build_source_references_deduplicates_filing_and_keeps_evidence_labels() -> None:
    session = Mock()
    session.get.return_value = SimpleNamespace(
        report_name="분기보고서 (2025.09)",
        receipt_date=date(2025, 11, 14),
    )

    references = build_source_references(session, _pack())

    assert len(references) == 1
    assert references[0].evidence_labels == ("E1", "E2")
    assert references[0].receipt_date == date(2025, 11, 14)
    session.get.assert_called_once_with(SourceFilingRow, "periodic_1")


def test_render_source_references_includes_report_name_and_receipt_date() -> None:
    session = Mock()
    session.get.return_value = SimpleNamespace(
        report_name="분기보고서 (2025.09)",
        receipt_date=date(2025, 11, 14),
    )

    rendered = render_source_references(build_source_references(session, _pack()))

    assert "근거 공시" in rendered
    assert "[E1,E2]" not in rendered
    assert "분기보고서 (2025.09) | 공시일: 2025-11-14" in rendered


def test_render_source_references_marks_empty_sources() -> None:
    assert render_source_references(()) == "근거 공시\n- 확인된 근거 공시 없음"
