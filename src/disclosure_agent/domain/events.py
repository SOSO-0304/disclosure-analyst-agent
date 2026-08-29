"""Typed domain events extracted from canonical disclosures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class EventType(StrEnum):
    """Supported typed disclosure events."""

    SUPPLY_CONTRACT = "supply_contract"
    SUPPLY_CONTRACT_TERMINATION = "supply_contract_termination"
    SUPPLY_CONTRACT_SUCCESSION = "supply_contract_succession"


class DisclosureEvent(BaseModel):
    """Common metadata shared by typed events."""

    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    filing_id: str
    receipt_number: str
    company_name: str
    stock_code: str
    is_correction: bool


class SupplyContractEvent(DisclosureEvent):
    """Typed representation of a single supply-contract filing."""

    event_type: EventType = EventType.SUPPLY_CONTRACT
    contract_type: str | None = None
    contract_name: str | None = None
    contract_amount: int | None = None
    recent_revenue: int | None = None
    revenue_ratio: Decimal | None = None
    counterparty: str | None = None
    relationship: str | None = None
    region: str | None = None
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    contract_date: date | None = None
    major_conditions: str | None = None


class SupplyContractTerminationEvent(DisclosureEvent):
    """Typed representation of a supply-contract termination filing."""

    event_type: EventType = EventType.SUPPLY_CONTRACT_TERMINATION
    termination_type: str | None = None
    contract_name: str | None = None
    termination_amount: int | None = None
    recent_revenue: int | None = None
    revenue_ratio: Decimal | None = None
    counterparty: str | None = None
    relationship: str | None = None
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    termination_reason: str | None = None
    termination_date: date | None = None
    notes: str | None = None
    related_disclosures: str | None = None


class SupplyContractSuccessionEvent(DisclosureEvent):
    """Typed representation of a supply-contract succession disclosure."""

    event_type: EventType = EventType.SUPPLY_CONTRACT_SUCCESSION
    title: str | None = None
    contract_type: str | None = None
    succession_amount_krw: int | None = None
    succession_amount_usd: int | None = None
    disclosed_exchange_rate: Decimal | None = None
    fulfilled_amount_usd: int | None = None
    fulfillment_ratio: Decimal | None = None
    counterparty: str | None = None
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    decision_date: date | None = None
    correction_reason: str | None = None
    notes: str | None = None
    related_disclosures: str | None = None
    source_contract_reference_dates: tuple[date, ...] = ()
