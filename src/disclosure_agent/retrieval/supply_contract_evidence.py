"""Evidence construction for deterministic Supply Contract lifecycle analysis."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import (
    EvidenceItem,
    EvidencePack,
    build_hybrid_evidence_pack,
)
from disclosure_agent.services.supply_contract_analysis import (
    SupplyContractFormationStepFinding,
    TerminatedContractFinding,
    TerminatedContractsInYearResult,
)
from disclosure_agent.storage.db_models import DisclosureRow, SourceBlockRow, SourceTableRow
from disclosure_agent.storage.supply_contract_query_repository import EvidenceRecord


def _refs(labels: tuple[str, ...]) -> str:
    return ",".join(f"[{label}]" for label in labels) or "-"


def _amount(value: int | None) -> str:
    return format_krw(value) if value is not None else "확인되지 않음"


def _formation_content(
    result: TerminatedContractsInYearResult,
    finding: TerminatedContractFinding,
    step: SupplyContractFormationStepFinding,
    *,
    step_index: int,
    step_count: int,
) -> str:
    formation = step.formation
    if not formation.is_correction:
        stage = "최초 계약"
    elif formation.is_latest_for_root and finding.contract.correction_lineage_complete:
        stage = "최종 정정 계약조건"
    elif formation.is_latest_for_root:
        stage = "현재 corpus에서 관측된 최신 정정(최종 확정 불가)"
    else:
        stage = "중간 정정"

    return "\n".join(
        (
            f"회사: {finding.contract.company_name}",
            f"원계약 체결 연도: {result.year}",
            f"lifecycle 단계: {stage} ({step_index}/{step_count})",
            f"공시일: {formation.receipt_date.isoformat()}",
            f"계약일: {formation.contract_date.isoformat() if formation.contract_date else '확인되지 않음'}",
            f"계약명: {formation.contract_name or '확인되지 않음'}",
            f"계약금액: {_amount(formation.contract_amount)}",
            f"거래상대방: {formation.counterparty or '확인되지 않음'}",
            f"정정 계보 상태: {formation.lineage_status or '-'}",
            (
                "해석 기준: 동일 root 계약의 형성/정정 chain에 속한 한 단계이며, "
                "correction lineage가 완전할 때만 최신 단계를 최종 계약조건으로 해석할 수 있음"
            ),
        )
    )


def _termination_content(
    result: TerminatedContractsInYearResult,
    finding: TerminatedContractFinding,
) -> str:
    contract = finding.contract
    return "\n".join(
        (
            f"회사: {contract.company_name}",
            f"원계약 체결 연도: {result.year}",
            "lifecycle 단계: 계약 해지",
            f"계약명: {contract.contract_name or '확인되지 않음'}",
            f"해지일: {contract.termination_date.isoformat() if contract.termination_date else '확인되지 않음'}",
            f"해지 사유: {contract.termination_reason or '확인되지 않음'}",
            f"해지 연결 상태: {contract.termination_link_status}",
            (
                "해석 기준: resolved 또는 resolved_by_fingerprint 상태로 원계약 root와 "
                "deterministic하게 연결된 해지 공시임"
            ),
        )
    )


def _lineage(
    session: Session,
    evidence: tuple[EvidenceRecord, ...],
) -> tuple[str | None, str | None, tuple[str, ...], tuple[str, ...]]:
    document_id = evidence[0].document_id if evidence else None
    table_ids = tuple(dict.fromkeys(record.table_id for record in evidence))
    block_ids: list[str] = []
    section_id = None
    for table_id in table_ids:
        table = session.get(SourceTableRow, table_id)
        if table is None:
            continue
        if table.block_id not in block_ids:
            block_ids.append(table.block_id)
        if section_id is None:
            block = session.get(SourceBlockRow, table.block_id)
            if block is not None:
                section_id = block.section_id
    return document_id, section_id, tuple(block_ids), table_ids


def _item(
    session: Session,
    *,
    evidence_id: str,
    source_kind: str,
    company_name: str,
    filing_id: str,
    content_text: str,
    evidence: tuple[EvidenceRecord, ...],
) -> EvidenceItem | None:
    filing = session.get(DisclosureRow, filing_id)
    if filing is None or not evidence:
        return None
    document_id, section_id, block_ids, table_ids = _lineage(session, evidence)
    event_ids = tuple(dict.fromkeys(record.event_id for record in evidence))
    return EvidenceItem(
        evidence_id=evidence_id,
        source_kind=source_kind,
        rank=0,
        score=1.0,
        semantic_score=0.0,
        lexical_score=0.0,
        company_name=company_name,
        filing_id=filing_id,
        report_name=filing.report_name,
        document_id=document_id,
        section_id=section_id,
        content_text=content_text,
        truncated=False,
        matched_terms=(),
        block_ids=block_ids,
        table_ids=table_ids,
        fact_ids=(),
        event_ids=event_ids,
    )


def render_deterministic_supply_contract_analysis(
    result: TerminatedContractsInYearResult,
    *,
    evidence_labels_by_filing_id: dict[str, str],
) -> str:
    """Render lifecycle order and answerability that the LLM must preserve."""

    all_labels: list[str] = []
    for finding in result.findings:
        for step in finding.formation_steps:
            label = evidence_labels_by_filing_id.get(step.formation.filing_id)
            if label and label not in all_labels:
                all_labels.append(label)
        termination_label = evidence_labels_by_filing_id.get(finding.contract.termination_filing_id)
        if termination_label and termination_label not in all_labels:
            all_labels.append(termination_label)

    lines = [
        "analysis_type: supply_contract_termination_lifecycle",
        f"status: {result.status}",
        f"company: {result.company_name or '-'}",
        f"formation_year: {result.year}",
        f"terminated_contract_count: {len(result.findings)}",
        f"derived_from: {_refs(tuple(all_labels))}",
        "판정 기준: 원계약 체결연도 기준으로 조회하고 resolved 계열 해지 링크만 확정 해지로 인정",
    ]

    if not result.findings:
        lines.append("해지 존재 여부: 제공된 공시에서 확정적으로 연결된 해지 계약을 확인하지 못함")
        return "\n".join(lines)

    lines.append(f"해지 존재 여부: YES | 확인된 계약 {len(result.findings)}건")
    for index, finding in enumerate(result.findings, start=1):
        contract = finding.contract
        chain_labels = tuple(
            evidence_labels_by_filing_id[step.formation.filing_id]
            for step in finding.formation_steps
            if step.formation.filing_id in evidence_labels_by_filing_id
        )
        termination_label = evidence_labels_by_filing_id.get(contract.termination_filing_id)
        finding_labels = (*chain_labels, *((termination_label,) if termination_label else ()))
        lines.append(
            f"계약 {index}: {contract.contract_name or '계약명 확인 불가'} | "
            f"원계약일 {contract.contract_date.isoformat()} | "
            f"정정 {contract.correction_count}회 | "
            f"correction_lineage_complete={str(contract.correction_lineage_complete).lower()} | "
            f"해지연결={contract.termination_link_status} | {_refs(finding_labels)}"
        )
        for step_index, step in enumerate(finding.formation_steps, start=1):
            formation = step.formation
            label = evidence_labels_by_filing_id.get(formation.filing_id)
            stage = "원계약" if not formation.is_correction else f"정정{step_index - 1}"
            if formation.is_latest_for_root:
                stage += "(관측 최신)"
            suffix = f" [{label}]" if label else ""
            lines.append(
                f"형성단계: {stage} | 공시일 {formation.receipt_date.isoformat()} | "
                f"계약명 {formation.contract_name or '확인 불가'} | "
                f"계약금액 {_amount(formation.contract_amount)} | "
                f"거래상대방 {formation.counterparty or '확인 불가'}{suffix}"
            )
        if contract.correction_lineage_complete:
            latest_label = evidence_labels_by_filing_id.get(contract.latest_formation_filing_id)
            suffix = f" [{latest_label}]" if latest_label else ""
            lines.append(
                f"최종 계약조건: 계약명 {contract.contract_name or '확인 불가'} | "
                f"계약금액 {_amount(contract.contract_amount)} | "
                f"거래상대방 {contract.counterparty or '확인 불가'}{suffix}"
            )
        else:
            lines.append(
                "최종 계약조건: 확정 불가 | correction lineage가 불완전하므로 "
                "관측 최신 공시 값을 최종값으로 단정하지 않음"
            )
        termination_suffix = f" [{termination_label}]" if termination_label else ""
        lines.append(
            f"해지: {contract.termination_date.isoformat() if contract.termination_date else '일자 확인 불가'} | "
            f"사유 {contract.termination_reason or '확인 불가'}{termination_suffix}"
        )

    return "\n".join(lines)


def build_supply_contract_evidence_pack(
    session: Session,
    *,
    query: str,
    result: TerminatedContractsInYearResult,
    max_chars_per_item: int = 1800,
    max_total_chars: int = 12000,
) -> EvidencePack:
    """Build one evidence item per formation/correction step and termination filing."""

    structured_items: list[EvidenceItem] = []
    filing_id_by_evidence_id: dict[str, str] = {}
    expected_items = 0

    for finding in result.findings:
        step_count = len(finding.formation_steps)
        expected_items += step_count + 1
        for step_index, step in enumerate(finding.formation_steps, start=1):
            formation = step.formation
            evidence_id = f"supply-contract:{finding.contract.root_filing_id}:{formation.filing_id}"
            filing_id_by_evidence_id[evidence_id] = formation.filing_id
            item = _item(
                session,
                evidence_id=evidence_id,
                source_kind=(
                    "supply_contract_correction"
                    if formation.is_correction
                    else "supply_contract_formation"
                ),
                company_name=finding.contract.company_name,
                filing_id=formation.filing_id,
                content_text=_formation_content(
                    result,
                    finding,
                    step,
                    step_index=step_index,
                    step_count=step_count,
                ),
                evidence=step.evidence,
            )
            if item is not None:
                structured_items.append(item)

        termination_id = f"supply-contract-termination:{finding.contract.termination_filing_id}"
        filing_id_by_evidence_id[termination_id] = finding.contract.termination_filing_id
        termination_item = _item(
            session,
            evidence_id=termination_id,
            source_kind="supply_contract_termination",
            company_name=finding.contract.company_name,
            filing_id=finding.contract.termination_filing_id,
            content_text=_termination_content(result, finding),
            evidence=finding.termination_evidence,
        )
        if termination_item is not None:
            structured_items.append(termination_item)

    pack = build_hybrid_evidence_pack(
        query,
        structured_items=tuple(structured_items),
        semantic_hits=(),
        max_semantic_items=0,
        max_chars_per_item=max_chars_per_item,
        max_total_chars=max_total_chars,
    )

    if result.findings and len(pack.items) != expected_items:
        raise ValueError("supply contract lifecycle requires evidence for every lifecycle step")

    labels_by_filing_id: dict[str, str] = {}
    for item in pack.items:
        filing_id = filing_id_by_evidence_id.get(item.evidence_id)
        if filing_id is not None:
            labels_by_filing_id[filing_id] = f"E{item.rank}"

    analysis = render_deterministic_supply_contract_analysis(
        result,
        evidence_labels_by_filing_id=labels_by_filing_id,
    )
    return replace(pack, deterministic_analysis=analysis[:6000])
