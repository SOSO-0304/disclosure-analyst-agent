"""Conservative correction lineage for facility-investment disclosures."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

CORPUS_START_DATE = date(2023, 1, 1)


@dataclass(frozen=True, slots=True)
class FacilityInvestmentSnapshot:
    """Small typed snapshot used only for correction-chain resolution."""

    filing_id: str
    corp_code: str
    receipt_date: date
    is_correction: bool
    decision_date: date | None
    investment_subject: str | None
    investment_type: str | None
    purpose: str | None
    investment_amount_krw: int | None
    related_filing_date: date | None = None


@dataclass(frozen=True, slots=True)
class FacilityInvestmentCorrectionResolution:
    """One correction filing's predecessor/root resolution."""

    correction_filing_id: str
    predecessor_filing_id: str | None
    root_filing_id: str
    status: str
    candidate_filing_ids: tuple[str, ...]
    match_score: int | None


@dataclass(frozen=True, slots=True)
class FacilityInvestmentLifecycle:
    """Latest effective filing for one in-corpus or external-predecessor chain."""

    root_filing_id: str
    corp_code: str
    latest_filing_id: str
    latest_receipt_date: date
    correction_count: int
    lineage_complete: bool
    status: str


@dataclass(frozen=True, slots=True)
class FacilityInvestmentLineageResult:
    """All correction links and latest-state rows for a typed facility corpus."""

    corrections: tuple[FacilityInvestmentCorrectionResolution, ...]
    lifecycles: tuple[FacilityInvestmentLifecycle, ...]


def resolve_facility_investment_lineage(
    snapshots: list[FacilityInvestmentSnapshot] | tuple[FacilityInvestmentSnapshot, ...],
) -> FacilityInvestmentLineageResult:
    """Resolve corrections using only earlier same-company typed disclosures.

    Decision date is the strongest in-corpus matching signal. When no predecessor can
    be matched, the correction-table reference filing date is preferred for deciding
    whether the predecessor predates the supplied corpus because a correction can also
    change the decision date itself. Subject, amount, purpose and investment type are
    supporting signals. External predecessor chains remain explicit rather than guessed.
    """

    ordered = sorted(snapshots, key=lambda item: (item.receipt_date, item.filing_id))
    prior_by_corp: dict[str, list[FacilityInvestmentSnapshot]] = defaultdict(list)
    root_by_filing: dict[str, str] = {}
    status_by_filing: dict[str, str] = {}
    correction_rows: list[FacilityInvestmentCorrectionResolution] = []

    for snapshot in ordered:
        if not snapshot.is_correction:
            root_by_filing[snapshot.filing_id] = snapshot.filing_id
            status_by_filing[snapshot.filing_id] = "root"
            prior_by_corp[snapshot.corp_code].append(snapshot)
            continue

        ranked: list[tuple[int, FacilityInvestmentSnapshot]] = []
        for candidate in prior_by_corp[snapshot.corp_code]:
            score = _match_score(snapshot, candidate)
            if score is not None:
                ranked.append((score, candidate))

        if ranked:
            best_score = max(score for score, _candidate in ranked)
            best_candidates = [candidate for score, candidate in ranked if score == best_score]
            candidate_ids = tuple(
                candidate.filing_id
                for candidate in sorted(
                    best_candidates,
                    key=lambda item: (item.receipt_date, item.filing_id),
                )
            )
            candidate_roots = {
                root_by_filing.get(candidate.filing_id, candidate.filing_id)
                for candidate in best_candidates
            }
            if len(candidate_roots) == 1:
                predecessor = max(
                    best_candidates,
                    key=lambda item: (item.receipt_date, item.filing_id),
                )
                predecessor_id: str | None = predecessor.filing_id
                root = root_by_filing.get(predecessor.filing_id, predecessor.filing_id)
                status = "resolved"
            else:
                predecessor_id = None
                root = snapshot.filing_id
                status = "ambiguous"
        else:
            predecessor_id = None
            root = snapshot.filing_id
            best_score = None
            candidate_ids = ()
            reference_date = snapshot.related_filing_date or snapshot.decision_date
            if reference_date is not None and reference_date < CORPUS_START_DATE:
                status = "out_of_corpus_predecessor"
            else:
                status = "unresolved"

        root_by_filing[snapshot.filing_id] = root
        status_by_filing[snapshot.filing_id] = status
        correction_rows.append(
            FacilityInvestmentCorrectionResolution(
                correction_filing_id=snapshot.filing_id,
                predecessor_filing_id=predecessor_id,
                root_filing_id=root,
                status=status,
                candidate_filing_ids=candidate_ids,
                match_score=best_score,
            )
        )
        prior_by_corp[snapshot.corp_code].append(snapshot)

    grouped: dict[str, list[FacilityInvestmentSnapshot]] = defaultdict(list)
    for snapshot in ordered:
        grouped[root_by_filing.get(snapshot.filing_id, snapshot.filing_id)].append(snapshot)

    lifecycles: list[FacilityInvestmentLifecycle] = []
    for root_filing_id, members in grouped.items():
        latest = max(members, key=lambda item: (item.receipt_date, item.filing_id))
        member_statuses = {status_by_filing.get(member.filing_id, "root") for member in members}
        if "ambiguous" in member_statuses:
            status = "ambiguous"
            complete = False
        elif "unresolved" in member_statuses:
            status = "unresolved"
            complete = False
        elif "out_of_corpus_predecessor" in member_statuses:
            status = "out_of_corpus_predecessor"
            complete = False
        else:
            status = "resolved"
            complete = True
        lifecycles.append(
            FacilityInvestmentLifecycle(
                root_filing_id=root_filing_id,
                corp_code=latest.corp_code,
                latest_filing_id=latest.filing_id,
                latest_receipt_date=latest.receipt_date,
                correction_count=sum(1 for member in members if member.is_correction),
                lineage_complete=complete,
                status=status,
            )
        )

    return FacilityInvestmentLineageResult(
        corrections=tuple(sorted(correction_rows, key=lambda item: item.correction_filing_id)),
        lifecycles=tuple(
            sorted(
                lifecycles,
                key=lambda item: (
                    item.corp_code,
                    item.latest_receipt_date,
                    item.root_filing_id,
                ),
            )
        ),
    )


def _match_score(
    correction: FacilityInvestmentSnapshot,
    candidate: FacilityInvestmentSnapshot,
) -> int | None:
    decision_match = (
        correction.decision_date is not None
        and candidate.decision_date is not None
        and correction.decision_date == candidate.decision_date
    )
    subject_match = _same_text(correction.investment_subject, candidate.investment_subject)
    if not decision_match and not subject_match:
        return None

    score = 10 if decision_match else 0
    if subject_match:
        score += 5
    if (
        correction.investment_amount_krw is not None
        and correction.investment_amount_krw == candidate.investment_amount_krw
    ):
        score += 2
    if _same_text(correction.purpose, candidate.purpose):
        score += 2
    if _same_text(correction.investment_type, candidate.investment_type):
        score += 1
    return score


def _same_text(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return _compact(left) == _compact(right)


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"\W+", "", normalized, flags=re.UNICODE)
