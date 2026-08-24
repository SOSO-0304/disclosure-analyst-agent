"""Core domain models."""

from dataclasses import dataclass


@dataclass(slots=True)
class DisclosureDocument:
    """Minimal identity for a disclosure document."""

    document_id: str
    title: str
