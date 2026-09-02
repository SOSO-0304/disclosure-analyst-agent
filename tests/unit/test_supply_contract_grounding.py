from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT
from disclosure_agent.rendering.supply_contract import (
    render_supply_contract_termination_answer,
)
from disclosure_agent.retrieval import supply_contract_query_resolver
from disclosure_agent.retrieval.company_resolver import CompanyIdentity
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.retrieval.supply_contract_evidence import (
    render_deterministic_supply_contract_analysis,
)
from disclosure_agent.retrieval.supply_contract_query_resolver import (
    resolve_supply_contract_query_target,
)
from disclosure_agent.services.supply_contract_analysis import (
    SupplyContractFormationStepFinding,
    TerminatedContractFinding,
    TerminatedContractsInYearResult,
)
from disclosure_agent.storage.supply_contract_query_repository import (
    SupplyContractFormationRecord,
    TerminatedContractRecord,
    _formation_chain_statement,
)


def _company(corp_code: str, listed_name: str) -> CompanyIdentity:
    return CompanyIdentity(
        corp_code=corp_code,
        stock_code=None,
        listed_name=listed_name,
        corp_name=listed_name,
    )


def _formation(
    filing_id: str,
    receipt_date: date,
    *,
    is_correction: bool,
    is_latest: bool,
    amount: int,
) -> SupplyContractFormationStepFinding:
    return SupplyContractFormationStepFinding(
        formation=SupplyContractFormationRecord(
            filing_id=filing_id,
            receipt_number=filing_id,
            receipt_date=receipt_date,
            is_correction=is_correction,
            predecessor_filing_id=None,
            lineage_status="resolved" if is_correction else "root",
            is_latest_for_root=is_latest,
            contract_date=date(2023, 10, 6),
            contract_name="연료전지 시스템 공급 계약",
            contract_amount=amount,
            counterparty="㈜태영건설",
        ),
        evidence=(),
    )


def _finding(*, lineage_complete: bool = True) -> TerminatedContractFinding:
    steps = (
        _formation(
            "exchange_root",
            date(2023, 10, 6),
            is_correction=False,
            is_latest=False,
            amount=70_000_000_000,
        ),
        _formation(
            "exchange_correction_1",
            date(2024, 6, 1),
            is_correction=True,
            is_latest=False,
            amount=71_000_000_000,
        ),
        _formation(
            "exchange_correction_2",
            date(2025, 4, 2),
            is_correction=True,
            is_latest=True,
            amount=72_200_000_000,
        ),
    )
    return TerminatedContractFinding(
        contract=TerminatedContractRecord(
            corp_code="1",
            company_name="두산퓨얼셀",
            root_filing_id="exchange_root",
            root_receipt_number="20231006800130",
            contract_date=date(2023, 10, 6),
            latest_formation_filing_id="exchange_correction_2",
            latest_formation_receipt_number="20250402800790",
            contract_name="연료전지 시스템 공급 계약",
            contract_amount=72_200_000_000,
            counterparty="㈜태영건설",
            correction_count=2,
            correction_lineage_complete=lineage_complete,
            termination_filing_id="exchange_termination",
            termination_receipt_number="20250402800768",
            termination_link_status="resolved",
            termination_date=date(2025, 4, 2),
            termination_reason="PF금융약정 체결 무산에 따른 해지",
        ),
        root_formation_evidence=(),
        latest_formation_evidence=(),
        termination_evidence=(),
        formation_steps=steps,
    )


def _evidence_item(rank: int, filing_id: str, source_kind: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"evidence:{filing_id}",
        source_kind=source_kind,
        rank=rank,
        score=1.0,
        semantic_score=0.0,
        lexical_score=0.0,
        company_name="두산퓨얼셀",
        filing_id=filing_id,
        report_name="공시",
        document_id=f"document:{filing_id}",
        section_id=None,
        content_text="evidence",
        truncated=False,
        matched_terms=(),
        block_ids=(),
        table_ids=(),
    )


def _evidence_pack() -> EvidencePack:
    items = (
        _evidence_item(1, "exchange_root", "supply_contract_formation"),
        _evidence_item(2, "exchange_correction_1", "supply_contract_correction"),
        _evidence_item(3, "exchange_correction_2", "supply_contract_correction"),
        _evidence_item(4, "exchange_termination", "supply_contract_termination"),
    )
    return EvidencePack(
        query="질문",
        retrieval_status="MATCHES_FOUND",
        items=items,
        total_chars=sum(len(item.content_text) for item in items),
    )


def test_resolves_supply_contract_company_and_formation_year(monkeypatch) -> None:
    companies = (_company("1", "두산퓨얼셀"), _company("2", "삼성중공업"))
    monkeypatch.setattr(
        supply_contract_query_resolver,
        "_source_companies",
        lambda session: companies,
    )

    target = resolve_supply_contract_query_target(
        SimpleNamespace(),
        query="두산퓨얼셀이 2023년에 체결한 주요 계약 중 이후 해지된 계약이 존재하는가?",
    )

    assert target.status == "RESOLVED"
    assert target.company_name == "두산퓨얼셀"
    assert target.year == 2023


def test_renders_full_correction_chain_and_termination() -> None:
    result = TerminatedContractsInYearResult(
        year=2023,
        company_name="두산퓨얼셀",
        findings=(_finding(),),
    )

    rendered = render_deterministic_supply_contract_analysis(
        result,
        evidence_labels_by_filing_id={
            "exchange_root": "E1",
            "exchange_correction_1": "E2",
            "exchange_correction_2": "E3",
            "exchange_termination": "E4",
        },
    )

    assert result.status == "ANSWERABLE"
    assert "해지 존재 여부: YES" in rendered
    assert "정정 2회" in rendered
    assert "형성단계: 원계약" in rendered
    assert "형성단계: 정정1" in rendered
    assert "형성단계: 정정2(관측 최신)" in rendered
    assert "최종 계약조건" in rendered
    assert "722억 원" in rendered
    assert "PF금융약정 체결 무산에 따른 해지 [E4]" in rendered
    assert "derived_from: [E1],[E2],[E3],[E4]" in rendered


def test_user_answer_has_deterministic_local_citations() -> None:
    result = TerminatedContractsInYearResult(
        year=2023,
        company_name="두산퓨얼셀",
        findings=(_finding(),),
    )

    rendered = render_supply_contract_termination_answer(result, _evidence_pack())

    assert "두산퓨얼셀의 2023년 체결 계약" in rendered
    assert "두산퓨얼셀가" not in rendered
    assert "이후 해지된 계약이 1건 확인됩니다 [E1][E4]" in rendered
    assert "정정공시가 2회 확인됩니다 [E2][E3]" in rendered
    assert "계약금액 722억 원, 거래상대방 ㈜태영건설입니다 [E3]" in rendered
    assert "해지 사유는 'PF금융약정 체결 무산에 따른 해지'입니다 [E4]" in rendered
    assert "[DETERMINISTIC ANALYSIS]" not in rendered


def test_incomplete_correction_lineage_is_partial() -> None:
    result = TerminatedContractsInYearResult(
        year=2023,
        company_name="두산퓨얼셀",
        findings=(_finding(lineage_complete=False),),
    )

    rendered = render_deterministic_supply_contract_analysis(
        result,
        evidence_labels_by_filing_id={
            "exchange_root": "E1",
            "exchange_correction_1": "E2",
            "exchange_correction_2": "E3",
            "exchange_termination": "E4",
        },
    )

    assert result.status == "PARTIAL"
    assert "최종 계약조건: 확정 불가" in rendered


def test_formation_chain_query_is_ordered_by_filing_date() -> None:
    statement = _formation_chain_statement(root_filing_id="exchange_root")
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "supply_contract_events.root_filing_id" in sql
    assert "ORDER BY disclosures.receipt_date, disclosures.receipt_number" in sql
    assert "exchange_root" in compiled.params.values()


def test_prompt_guards_supply_contract_lifecycle_semantics() -> None:
    assert "supply_contract_termination_lifecycle" in GROUNDING_SYSTEM_PROMPT
    assert "원계약, 정정공시, 해지공시의 시간 순서" in GROUNDING_SYSTEM_PROMPT
    assert "lineage가 불완전하면" in GROUNDING_SYSTEM_PROMPT
    assert "유효한 인용은 오직 [E1], [E2] 같은 Evidence 번호" in GROUNDING_SYSTEM_PROMPT
    assert "원계약과 해지 Evidence를 바로 인용" in GROUNDING_SYSTEM_PROMPT
    assert "해당 모든 정정 Evidence" in GROUNDING_SYSTEM_PROMPT
