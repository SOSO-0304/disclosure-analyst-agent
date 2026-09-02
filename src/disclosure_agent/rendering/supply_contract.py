"""Deterministic user-facing rendering for Supply Contract lifecycle answers."""

from __future__ import annotations

from disclosure_agent.rendering.money import format_krw
from disclosure_agent.retrieval.evidence_pack import EvidencePack
from disclosure_agent.services.supply_contract_analysis import TerminatedContractsInYearResult


def _citation(labels: tuple[str, ...]) -> str:
    return "".join(f"[{label}]" for label in labels)


def _labels_by_filing_id(pack: EvidencePack) -> dict[str, str]:
    return {item.filing_id: f"E{item.rank}" for item in pack.items}


def render_supply_contract_termination_answer(
    result: TerminatedContractsInYearResult,
    pack: EvidencePack,
) -> str:
    """Render closed factual termination findings without LLM citation drift."""

    if result.status != "ANSWERABLE":
        raise ValueError("deterministic supply contract answer requires ANSWERABLE result")
    if not result.findings:
        raise ValueError("deterministic supply contract answer requires at least one finding")

    labels = _labels_by_filing_id(pack)
    existence_labels: list[str] = []
    for finding in result.findings:
        for filing_id in (
            finding.contract.root_filing_id,
            finding.contract.termination_filing_id,
        ):
            label = labels.get(filing_id)
            if label is None:
                raise ValueError(f"missing Evidence label for filing: {filing_id}")
            if label not in existence_labels:
                existence_labels.append(label)

    company = result.company_name or result.findings[0].contract.company_name
    count = len(result.findings)
    lines = [
        (
            f"네. {company}의 {result.year}년 체결 계약 중 이후 해지된 계약이 "
            f"{count}건 확인됩니다 {_citation(tuple(existence_labels))}."
        )
    ]

    for index, finding in enumerate(result.findings, start=1):
        contract = finding.contract
        root_label = labels.get(contract.root_filing_id)
        termination_label = labels.get(contract.termination_filing_id)
        latest_label = labels.get(contract.latest_formation_filing_id)
        if root_label is None or termination_label is None or latest_label is None:
            raise ValueError("missing Evidence label for supply contract lifecycle")

        if count > 1:
            lines.append("")
            lines.append(f"{index}. {contract.contract_name or '계약명 확인 불가'}")

        lines.append(
            f"해당 계약은 '{contract.contract_name or '계약명 확인 불가'}'이며, "
            f"원계약일은 {contract.contract_date.isoformat()}입니다 [{root_label}]."
        )

        correction_labels = tuple(
            labels[step.formation.filing_id]
            for step in finding.formation_steps
            if step.formation.is_correction and step.formation.filing_id in labels
        )
        if contract.correction_count > 0:
            if len(correction_labels) != contract.correction_count:
                raise ValueError("correction Evidence count does not match correction_count")
            lines.append(
                f"이후 정정공시가 {contract.correction_count}회 확인됩니다 "
                f"{_citation(correction_labels)}."
            )

        latest_parts: list[str] = []
        if contract.contract_amount is not None:
            latest_parts.append(f"계약금액 {format_krw(contract.contract_amount)}")
        if contract.counterparty:
            latest_parts.append(f"거래상대방 {contract.counterparty}")
        if latest_parts:
            lines.append(
                f"최종 계약조건은 {', '.join(latest_parts)}입니다 [{latest_label}]."
            )

        termination_date = (
            contract.termination_date.isoformat() if contract.termination_date else "확인 불가"
        )
        reason = contract.termination_reason or "확인 불가"
        lines.append(
            f"이 계약은 {termination_date}에 해지되었으며, 해지 사유는 '{reason}'입니다 "
            f"[{termination_label}]."
        )

    return "\n".join(lines)
