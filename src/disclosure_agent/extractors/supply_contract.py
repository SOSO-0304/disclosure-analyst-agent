"""Extract typed Supply Contract events from canonical Exchange fields."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from disclosure_agent.domain.events import SupplyContractEvent
from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField


@dataclass(frozen=True, slots=True)
class SupplyContractExtraction:
    """A typed event plus the semantic fields used as evidence."""

    event: SupplyContractEvent
    evidence: dict[str, SemanticField]


ALIASES: dict[str, tuple[str, ...]] = {
    "contract_type": (
        "1. 판매ㆍ공급계약 구분",
    ),
    "contract_name": (
        "- 체결계약명",
        "- 세부내용",
        "1. 판매ㆍ공급계약 내용",
    ),
    "contract_amount": (
        "2. 계약내역 > 계약금액(원)",
        "2. 계약내역 > 확정 계약금액",
        "2. 계약내역 > 계약금액 총액(원)",
    ),
    "recent_revenue": (
        "2. 계약내역 > 최근매출액(원)",
        "2. 계약내역 > 최근 매출액(원)",
    ),
    "revenue_ratio": (
        "2. 계약내역 > 매출액대비(%)",
        "2. 계약내역 > 매출액 대비(%)",
    ),
    "counterparty": (
        "3. 계약상대",
        "3. 계약상대방",
    ),
    "relationship": (
        "- 회사와의 관계",
        "-회사와의 관계",
    ),
    "region": (
        "4. 판매ㆍ공급지역",
        "4. 판매·공급지역",
    ),
    "contract_start_date": (
        "5. 계약기간 > 시작일",
    ),
    "contract_end_date": (
        "5. 계약기간 > 종료일",
    ),
    "contract_date": (
        "7. 계약(수주)일자",
        "7. 계약(수주)일",
        "8. 계약(수주)일자",
    ),
    "major_conditions": (
        "6. 주요 계약조건",
        "6. 주요 계약조건 > 대금지급 조건 등",
        "대금지급 조건 등",
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


def extract_supply_contract(
    package: FilingPackage,
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractExtraction:
    """Extract one SupplyContractEvent from a selected filing package."""

    if package.filing.document_group.value != "exchange":
        raise ValueError("Supply Contract extractor requires an Exchange filing")
    if package.filing.document_subtype != "단일판매공급계약체결":
        raise ValueError("Supply Contract extractor requires document_subtype=단일판매공급계약체결")

    field_reader = reader or ExchangeFieldReader()
    fields = field_reader.read_package(package)
    chosen: dict[str, SemanticField] = {}

    for attribute, aliases in ALIASES.items():
        field = _first(fields, aliases)
        if field is not None:
            chosen[attribute] = field

    event = SupplyContractEvent(
        filing_id=package.filing_id,
        receipt_number=package.filing.receipt_number,
        company_name=package.company.listed_name,
        stock_code=package.company.stock_code,
        is_correction=package.correction.is_correction,
        contract_type=_text(chosen.get("contract_type")),
        contract_name=_text(chosen.get("contract_name")),
        contract_amount=_parse_int(_text(chosen.get("contract_amount"))),
        recent_revenue=_parse_int(_text(chosen.get("recent_revenue"))),
        revenue_ratio=_parse_decimal(_text(chosen.get("revenue_ratio"))),
        counterparty=_text(chosen.get("counterparty")),
        relationship=_text(chosen.get("relationship")),
        region=_text(chosen.get("region")),
        contract_start_date=_parse_date(_text(chosen.get("contract_start_date"))),
        contract_end_date=_parse_date(_text(chosen.get("contract_end_date"))),
        contract_date=_parse_date(_text(chosen.get("contract_date"))),
        major_conditions=_text(chosen.get("major_conditions")),
    )
    return SupplyContractExtraction(event=event, evidence=chosen)
