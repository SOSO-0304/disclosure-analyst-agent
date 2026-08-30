from __future__ import annotations

from datetime import date

from disclosure_agent.domain.facility_investment_lineage import (
    FacilityInvestmentSnapshot,
    resolve_facility_investment_lineage,
)


def _snapshot(
    filing_id: str,
    receipt_date: date,
    *,
    correction: bool,
    decision_date: date | None,
    subject: str | None = "제5공장 신설",
    amount: int | None = 100,
    related_filing_date: date | None = None,
) -> FacilityInvestmentSnapshot:
    return FacilityInvestmentSnapshot(
        filing_id=filing_id,
        corp_code="001",
        receipt_date=receipt_date,
        is_correction=correction,
        decision_date=decision_date,
        investment_subject=subject,
        investment_type="신규시설투자",
        purpose="생산능력 확대",
        investment_amount_krw=amount,
        related_filing_date=related_filing_date,
    )


def test_corrections_chain_to_latest_prior_filing_and_share_root() -> None:
    original = _snapshot(
        "original",
        date(2023, 3, 17),
        correction=False,
        decision_date=date(2023, 3, 17),
    )
    correction_1 = _snapshot(
        "correction-1",
        date(2023, 6, 5),
        correction=True,
        decision_date=date(2023, 3, 17),
    )
    correction_2 = _snapshot(
        "correction-2",
        date(2024, 12, 18),
        correction=True,
        decision_date=date(2023, 3, 17),
        amount=120,
    )

    result = resolve_facility_investment_lineage([correction_2, original, correction_1])

    links = {row.correction_filing_id: row for row in result.corrections}
    assert links["correction-1"].predecessor_filing_id == "original"
    assert links["correction-2"].predecessor_filing_id == "correction-1"
    assert links["correction-2"].root_filing_id == "original"

    assert len(result.lifecycles) == 1
    lifecycle = result.lifecycles[0]
    assert lifecycle.latest_filing_id == "correction-2"
    assert lifecycle.correction_count == 2
    assert lifecycle.lineage_complete is True
    assert lifecycle.status == "resolved"


def test_correction_with_pre_corpus_decision_is_marked_external_predecessor() -> None:
    correction = _snapshot(
        "correction",
        date(2024, 6, 18),
        correction=True,
        decision_date=date(2021, 6, 29),
        subject="13,000TEU 컨테이너 선박 12척",
    )

    result = resolve_facility_investment_lineage([correction])

    link = result.corrections[0]
    assert link.predecessor_filing_id is None
    assert link.root_filing_id == "correction"
    assert link.status == "out_of_corpus_predecessor"
    assert result.lifecycles[0].lineage_complete is False
    assert result.lifecycles[0].status == "out_of_corpus_predecessor"


def test_correction_uses_reference_date_when_decision_date_changed() -> None:
    correction = _snapshot(
        "correction",
        date(2025, 1, 20),
        correction=True,
        decision_date=date(2025, 1, 20),
        related_filing_date=date(2022, 11, 23),
        subject="온산제련소 퓨머(Fumer)",
        amount=89_200_000_000,
    )

    result = resolve_facility_investment_lineage([correction])

    link = result.corrections[0]
    assert link.predecessor_filing_id is None
    assert link.status == "out_of_corpus_predecessor"
    assert result.lifecycles[0].lineage_complete is False


def test_same_company_different_subject_does_not_create_false_lineage() -> None:
    original = _snapshot(
        "other-project",
        date(2024, 1, 1),
        correction=False,
        decision_date=date(2024, 1, 1),
        subject="A 공장",
    )
    correction = _snapshot(
        "unresolved-correction",
        date(2024, 2, 1),
        correction=True,
        decision_date=date(2024, 1, 15),
        subject="B 공장",
    )

    result = resolve_facility_investment_lineage([original, correction])

    link = result.corrections[0]
    assert link.predecessor_filing_id is None
    assert link.status == "unresolved"
    assert len(result.lifecycles) == 2
