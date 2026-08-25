"""Document-level parsing orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import CanonicalDisclosure
from disclosure_agent.parsing.dart_document_parsers import HoldingParser, MajorParser, PeriodicParser
from disclosure_agent.parsing.exchange_parser import ExchangeParser
from disclosure_agent.parsing.periodic_html_parser import PeriodicHtmlParser


class DocumentParser:
    def __init__(self) -> None:
        self.parsers = {
            "exchange": ExchangeParser(),
            "periodic": PeriodicParser(),
            "major": MajorParser(),
            "holding": HoldingParser(),
        }
        self.periodic_html = PeriodicHtmlParser()

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        group = str(manifest["doc_group"])
        if not file_paths:
            raise ValueError(f"No source files for {manifest.get('doc_id')}")

        if group == "periodic":
            xml_paths = [p for p in file_paths if Path(p).suffix.lower() == ".xml"]
            if xml_paths:
                # Preserve the XML-first behavior for normal periodic reports.
                return self.parsers["periodic"].parse(xml_paths, manifest=manifest)
            html_paths = [p for p in file_paths if Path(p).suffix.lower() in {".html", ".htm"}]
            if html_paths:
                # Pass all matched sources so PDF/HTML provenance is preserved.
                return self.periodic_html.parse(file_paths, manifest=manifest)
            raise ValueError(f"Periodic source has no XML or viewer HTML: {manifest.get('doc_id')}")

        try:
            parser = self.parsers[group]
        except KeyError as exc:
            raise ValueError(f"Unsupported doc_group: {group}") from exc

        # Non-periodic corpus documents are expected to use XML-like sources.
        parseable = [p for p in file_paths if Path(p).suffix.lower() in {".xml", ".html", ".htm"}]
        return parser.parse(parseable or file_paths, manifest=manifest)
