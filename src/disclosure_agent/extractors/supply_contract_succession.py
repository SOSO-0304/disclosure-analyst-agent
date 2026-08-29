"""Extract the special Supply Contract succession disclosure into a typed event."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from disclosure_agent.domain.events import SupplyContractSuccessionEvent
from disclosure_agent.domain.models import FilingPackage
from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader, SemanticField

SUCCESSION_SUBTYPE = "투자판단관련주요경영사항"
SUCCESSION_REPORT_TOKEN = "단일판매 공급계약 잔여금액 승계"

ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("1. 제목",),
    "details": ("2. 주요내용",),
    "decision_date": ("3. 이사회결의일(결정일) 또는 사실확인일",),
    "correction_reason": ("3. 정정사유",),
    "notes": ("4. 기타 투자판단과 관련한 중요사항",),
    "related_disclosures": ("※ 관련공시",),
}

KOREAN_DATE_PATTERN = re.compile(r"(?P<year>\d{4})년\s*(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일")
SOURCE_REFERENCE_MARKER = re.compile(r"※\s*관련공시\(OCI\s*홀딩스㈜\)(?P<tail>.*)$")


@dataclass(frozen=True, slots=True)
class SupplyContractSuccessionExtraction:
    """A typed succession event plus the semantic fields used as evidence."""

    event: SupplyContractSuccessionEvent
    evidence: dict[str, SemanticField]


def is_supply_contract_succession(package: FilingPackage) -> bool:
    """Return whether a package is the special contract-succession disclosure."""

    return (
        package.filing.document_group.value == "exchange"
        and package.filing.document_subtype == SUCCESSION_SUBTYPE
        and SUCCESSION_REPORT_TOKEN in package.filing.report_name_raw
    )


def extract_supply_contract_succession(
    package: FilingPackage,
    *,
    reader: ExchangeFieldReader | None = None,
) -> SupplyContractSuccessionExtraction:
    """Extract the contract-succession disclosure conservatively."""

    if not is_supply_contract_succession(package):
        raise ValueError("Package is not the Supply Contract succession disclosure")

    field_reader = reader or ExchangeFieldReader()
    fields = field_reader.read_package(package)
    chosen: dict[str, SemanticField] = {}
    for attribute, aliases in ALIASES.items():
        field = _first(fields, aliases)
        if field is not None:
            chosen[attribute] = field

    details = _text(chosen.get("details"))
    notes = _text(chosen.get("notes"))
    parsed = _parse_details(details)

    event = SupplyContractSuccessionEvent(
        filing_id=package.filing_id,
        receipt_number=package.filing.receipt_number,
        company_name=package.company.listed_name,
        stock_code=package.company.stock_code,
        is_correction=package.correction.is_correction,
        title=_text(chosen.get("title")),
        contract_type=parsed.get("contract_type"),
        succession_amount_krw=_parse_int(parsed.get("succession_amount_krw")),
        succession_amount_usd=_parse_int(parsed.get("succession_amount_usd")),
        disclosed_exchange_rate=_parse_decimal(parsed.get("disclosed_exchange_rate")),
        fulfilled_amount_usd=_parse_int(parsed.get("fulfilled_amount_usd")),
        fulfillment_ratio=_parse_decimal(parsed.get("fulfillment_ratio")),
        counterparty=parsed.get("counterparty"),
        contract_start_date=_parse_korean_date(parsed.get("contract_start_date")),
        contract_end_date=_parse_korean_date(parsed.get("contract_end_date")),
        decision_date=_parse_iso_date(_text(chosen.get("decision_date"))),
        correction_reason=_text(chosen.get("correction_reason")),
        notes=notes,
        related_disclosures=_text(chosen.get("related_disclosures")),
        source_contract_reference_dates=_source_reference_dates(notes),
    )
    return SupplyContractSuccessionExtraction(event=event, evidence=chosen)


def _first(fields: Iterable[SemanticField], aliases: tuple[str, ...]) -> SemanticField | None:
    by_key: dict[str, SemanticField] = {}
    for field in fields:
        if field.path_key not in by_key and _text(field) is not None:
            by_key[field.path_key] = field
    for alias in aliases:
        if alias in by_key:
            return by_key[alias]
    return None


def _text(field: SemanticField | None) -> str | None:
    if field is None:
        return None
    value = field.value.strip()
    if value in {"", "-", "해당없음", "해당 없음"}:
        return None
    return value


def _parse_details(details: str | None) -> dict[str, str]:
    if not details:
        return {}

    parsed: dict[str, str] = {}
    patterns = {
        "contract_type": (r"1\)\s*판매[ㆍ·]?공급계약\s*구분\s*:\s*(?P<value>.+?)\s+2\)"),
        "counterparty": (r"3\)\s*계약상대\s*:\s*(?P<value>.+?)\s+4\)\s*계약기간"),
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, details)
        if match:
            parsed[key] = match.group("value").strip()

    amount = re.search(
        r"잔여금액\s*승계\s*(?P<krw>[\d,]+)원\s*"
        r"\(USD\s*(?P<usd>[\d,]+),\s*최초에\s*공시된\s*환율\s*:\s*"
        r"(?P<rate>[\d,.]+)원/1USD\)",
        details,
    )
    if amount:
        parsed["succession_amount_krw"] = amount.group("krw")
        parsed["succession_amount_usd"] = amount.group("usd")
        parsed["disclosed_exchange_rate"] = amount.group("rate")

    fulfillment = re.search(
        r"현재기준\s*USD\s*(?P<amount>[\d,]+)\s*공급,\s*"
        r"이행률\s*:\s*(?P<ratio>[\d,.]+)%",
        details,
    )
    if fulfillment:
        parsed["fulfilled_amount_usd"] = fulfillment.group("amount")
        parsed["fulfillment_ratio"] = fulfillment.group("ratio")

    period = re.search(
        r"4\)\s*계약기간\s*:\s*"
        r"(?P<start>\d{4}년\s*\d{1,2}월\s*\d{1,2}일)\s*~\s*"
        r"(?P<end>\d{4}년\s*\d{1,2}월\s*\d{1,2}일)",
        details,
    )
    if period:
        parsed["contract_start_date"] = period.group("start")
        parsed["contract_end_date"] = period.group("end")

    return parsed


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return None


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.replace(".", "-").replace("/", "-"))
    except ValueError:
        return None


def _parse_korean_date(value: str | None) -> date | None:
    if value is None:
        return None
    match = KOREAN_DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None


def _source_reference_dates(notes: str | None) -> tuple[date, ...]:
    if not notes:
        return ()
    marker = SOURCE_REFERENCE_MARKER.search(notes)
    if not marker:
        return ()

    dates: list[date] = []
    for match in KOREAN_DATE_PATTERN.finditer(marker.group("tail")):
        try:
            parsed = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        if parsed not in dates:
            dates.append(parsed)
    return tuple(dates)
