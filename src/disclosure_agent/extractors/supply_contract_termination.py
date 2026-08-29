"""Extract typed Supply Contract Termination events from canonical Exchange fields."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from disclosure_agent.domain.events import SupplyContractTerminationEvent
from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField


@dataclass(frozen=True, slots=True)
class SupplyContractTerminationExtraction:
    """A typed termination event plus the semantic fields used as evidence."""

    event: SupplyContractTerminationEvent
    evidence: dict[str, SemanticField]


ALIASES: dict[str, tuple[str, ...]] = {
    "termination_type": ("1. 판매ㆍ공급계약 해지 구분",),
    "contract_name": ("- 해지계약명", "- 세부물건"),
    "termination_amount": ("2. 해지내역 > 해지금액(원)",),
    "recent_revenue": ("2. 해지내역 > 최근매출액(원)",),
    "revenue_ratio": ("2. 해지내역 > 매출액대비(%)",),
    "counterparty": ("3. 계약상대",),
    "relationship": ("- 회사와의 관계",),
    "contract_start_date": ("4. 계약기간 > 시작일",),
    "contract_end_date": ("4. 계약기간 > 종료일",),
    "termination_reason": ("5. 해지 주요사유",),
    "termination_date": ("6. 해지일자",),
    "notes": (
        "8. 기타 투자판단과 관련한 중요사항",
        "7. 기타 투자판단과 관련한 중요사항",
    ),
    "related_disclosures": (
        "8. 기타 투자판단과 관련한 중요사항 > 관련공시",
        "7. 기타 투자판단과 관련한 중요사항 > ※ 관련공시",
    ),
}


def _is_missing(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip() in {"", "-", "해당없음", "해당 없음"}


def _first(fields: Iterable[SemanticField], aliases: tuple[str, ...]) -> SemanticField | None:
    by_key: dict[str, SemanticField] = {}
    for field in fields:
        if field.path_key not in by_key and not _is_missing(field.value):
            by_key[field.path_key] = field
    for alias in aliases:
        if alias in by_key:
            return by_key[alias]
    return None


def _parse_int(value: str | None) -> int | None:
    if _is_missing(value):
        return None
    cleaned = value.replace(",", "").replace("원", "").strip()
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        return int(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if _is_missing(value):
        return None
    cleaned = value.replace(",", "").replace("%", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(value: str | None) -> date | None:
    if _is_missing(value):
        return None
    text = value.strip().replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(field: SemanticField | None) -> str | None:
    if field is None or _is_missing(field.value):
        return None
    return field.value.strip()


def extract_supply_contract_termination(
    package: FilingPackage,
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractTerminationExtraction:
    """Extract one SupplyContractTerminationEvent from a termination filing."""

    if package.filing.document_group.value != "exchange":
        raise ValueError("Supply Contract Termination extractor requires an Exchange filing")
    if package.filing.document_subtype != "단일판매공급계약해지":
        raise ValueError(
            "Supply Contract Termination extractor requires document_subtype=단일판매공급계약해지"
        )

    field_reader = reader or ExchangeFieldReader()
    fields = field_reader.read_package(package)
    chosen: dict[str, SemanticField] = {}

    for attribute, aliases in ALIASES.items():
        field = _first(fields, aliases)
        if field is not None:
            chosen[attribute] = field

    event = SupplyContractTerminationEvent(
        filing_id=package.filing_id,
        receipt_number=package.filing.receipt_number,
        company_name=package.company.listed_name,
        stock_code=package.company.stock_code,
        is_correction=package.correction.is_correction,
        termination_type=_text(chosen.get("termination_type")),
        contract_name=_text(chosen.get("contract_name")),
        termination_amount=_parse_int(_text(chosen.get("termination_amount"))),
        recent_revenue=_parse_int(_text(chosen.get("recent_revenue"))),
        revenue_ratio=_parse_decimal(_text(chosen.get("revenue_ratio"))),
        counterparty=_text(chosen.get("counterparty")),
        relationship=_text(chosen.get("relationship")),
        contract_start_date=_parse_date(_text(chosen.get("contract_start_date"))),
        contract_end_date=_parse_date(_text(chosen.get("contract_end_date"))),
        termination_reason=_text(chosen.get("termination_reason")),
        termination_date=_parse_date(_text(chosen.get("termination_date"))),
        notes=_text(chosen.get("notes")),
        related_disclosures=_text(chosen.get("related_disclosures")),
    )
    return SupplyContractTerminationExtraction(event=event, evidence=chosen)
