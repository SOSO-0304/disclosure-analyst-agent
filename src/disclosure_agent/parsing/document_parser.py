"""Document-level parsing orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import CanonicalDisclosure
from disclosure_agent.parsing.dart_document_parsers import HoldingParser, MajorParser, PeriodicParser
from disclosure_agent.parsing.exchange_parser import ExchangeParser


class DocumentParser:
    def __init__(self) -> None:
        self.parsers = {
            "exchange": ExchangeParser(),
            "periodic": PeriodicParser(),
            "major": MajorParser(),
            "holding": HoldingParser(),
        }

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        group = str(manifest["doc_group"])
        try:
            parser = self.parsers[group]
        except KeyError as exc:
            raise ValueError(f"Unsupported doc_group: {group}") from exc
        if not file_paths:
            raise ValueError(f"No source files for {manifest.get('doc_id')}")
        return parser.parse(file_paths, manifest=manifest)
