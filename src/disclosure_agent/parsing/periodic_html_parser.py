"""Fallback parser for periodic disclosures provided as viewer HTML instead of XML."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import html

from disclosure_agent.domain.models import (
    CanonicalDisclosure,
    Company,
    DisclosureMetadata,
    DisclosureType,
    PeriodicPayload,
    RevisionInfo,
    Section,
    SourceFile,
    TextBlock,
)
from disclosure_agent.parsing.table_parser import element_text, parse_table
from disclosure_agent.parsing.text_normalizer import optional_int, optional_str, parse_date
from disclosure_agent.parsing.xml_loader import decode_source


class PeriodicHtmlParser:
    """Parse DART viewer HTML when the original periodic XML is unavailable."""

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        html_paths = [Path(p) for p in file_paths if Path(p).suffix.lower() in {".html", ".htm"}]
        if not html_paths:
            raise ValueError(f"No viewer HTML source for {manifest.get('doc_id')}")

        # Prefer the DART viewer file when multiple HTML files are present.
        html_path = next((p for p in html_paths if "viewer" in p.stem.lower()), html_paths[0])
        root = html.fromstring(decode_source(html_path))

        report_name = str(manifest["report_nm"])
        section_path = [report_name]
        tables = [
            parse_table(table, f"{manifest['doc_id']}:html-table:{i}", i, section_path)
            for i, table in enumerate(root.xpath("//table"), 1)
        ]

        # Viewer HTML varies by issuer/year. Preserve visible paragraph/heading text
        # conservatively and deduplicate repeated wrapper text.
        text_blocks: list[TextBlock] = []
        seen: set[str] = set()
        order = 0
        for node in root.xpath("//h1 | //h2 | //h3 | //h4 | //h5 | //h6 | //p"):
            text = element_text(node)
            if not text or text in seen:
                continue
            seen.add(text)
            order += 1
            text_blocks.append(TextBlock(text=text, order=order))

        if not text_blocks:
            body_nodes = root.xpath("//body")
            body = body_nodes[0] if body_nodes else root
            text = element_text(body)
            if text:
                text_blocks.append(TextBlock(text=text, order=1))

        section = Section(
            section_id=f"{manifest['doc_id']}:viewer-html",
            level=1,
            title=report_name,
            path=section_path,
            text_blocks=text_blocks,
            tables=tables,
            order=1,
        )

        receipt_date = parse_date(manifest.get("rcept_dt"))
        if receipt_date is None:
            raise ValueError(f"Invalid receipt date: {manifest.get('rcept_dt')}")

        return CanonicalDisclosure(
            document_id=str(manifest["doc_id"]),
            document_type=DisclosureType.PERIODIC,
            company=Company(
                corp_code=str(manifest["corp_code"]),
                corp_name=str(manifest["corp_name"]),
                listed_name=optional_str(manifest.get("listed_name")),
                stock_code=optional_str(manifest.get("stock_code")),
                industry=optional_str(manifest.get("industry")),
                sector=optional_str(manifest.get("sector")),
            ),
            metadata=DisclosureMetadata(
                report_name=report_name,
                receipt_no=str(manifest["rcept_no"]),
                receipt_date=receipt_date,
                doc_group=str(manifest["doc_group"]),
                doc_subtype=optional_str(manifest.get("doc_subtype")),
                filer_name=optional_str(manifest.get("flr_nm")),
                base_year=optional_int(manifest.get("base_year")),
                base_month=optional_int(manifest.get("base_month")),
            ),
            revision=RevisionInfo(is_correction=bool(manifest.get("is_correction", False))),
            source_files=[
                SourceFile(path=str(p), file_name=Path(p).name, order=i)
                for i, p in enumerate(file_paths)
            ],
            payload=PeriodicPayload(
                fiscal_year=optional_int(manifest.get("base_year")),
                sections=[section],
            ),
        )
