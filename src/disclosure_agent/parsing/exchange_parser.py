"""Parser for exchange HTML-form disclosures, including .xml-named HTML."""

from __future__ import annotations

from pathlib import Path

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
    IssueSeverity,
    ParseIssue,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceLocator,
)
from disclosure_agent.parsing.table_parser import build_table_ids, parse_table, raw_element_text
from disclosure_agent.parsing.text_normalizer import normalize_text
from disclosure_agent.parsing.xml_loader import load_html


class ExchangeParser:
    """Preserve exchange-form tables before event extraction."""

    parser_version = "2.2.0"

    def parse(
        self,
        path: str | Path,
        source: SourceFile,
        *,
        filing_id: str,
        title: str,
    ) -> CanonicalDocument:
        loaded = load_html(path, source.source_file_id)
        section_id = f"{filing_id}:{source.source_file_id}:section:1"
        section = CanonicalSection(
            section_id=section_id,
            order=0,
            level=1,
            title_raw=title,
            title_normalized=normalize_text(title),
            source_locator=SourceLocator(source_file_id=source.source_file_id, xpath="/html"),
        )
        blocks: list[CanonicalBlock] = []
        table_ids = build_table_ids(loaded.root, f"{filing_id}:{source.source_file_id}")

        for node in loaded.root.xpath(
            "//h1[not(ancestor::table)] | //h2[not(ancestor::table)] | "
            "//h3[not(ancestor::table)] | //p[not(ancestor::table)] | //table"
        ):
            xpath = node.getroottree().getpath(node)
            if str(node.tag).lower() == "table":
                table = parse_table(
                    node,
                    table_ids[node],
                    source.source_file_id,
                    table_ids=table_ids,
                )
                blocks.append(
                    CanonicalBlock(
                        block_id=f"{filing_id}:{source.source_file_id}:block:{len(blocks) + 1}",
                        section_id=section_id,
                        order=len(blocks),
                        block_type=BlockType.TABLE,
                        table=table,
                        source_locator=SourceLocator(
                            source_file_id=source.source_file_id,
                            xpath=xpath,
                        ),
                    )
                )
                continue

            raw_text = raw_element_text(node)
            normalized = normalize_text(raw_text)
            if normalized is None:
                continue
            tag = str(node.tag).lower()
            block_type = BlockType.HEADING if tag.startswith("h") else BlockType.PARAGRAPH
            blocks.append(
                CanonicalBlock(
                    block_id=f"{filing_id}:{source.source_file_id}:block:{len(blocks) + 1}",
                    section_id=section_id,
                    order=len(blocks),
                    block_type=block_type,
                    text_raw=raw_text,
                    text_normalized=normalized,
                    heading_level=int(tag[1]) if tag.startswith("h") else None,
                    source_locator=SourceLocator(
                        source_file_id=source.source_file_id,
                        xpath=xpath,
                    ),
                )
            )

        issues: list[ParseIssue] = list(loaded.issues)
        if not blocks:
            issues.append(
                ParseIssue(
                    issue_code="empty_document",
                    severity=IssueSeverity.ERROR,
                    message="No canonical content blocks were emitted from the HTML source.",
                    source_file_id=source.source_file_id,
                )
            )
        errors = sum(
            issue.occurrence_count for issue in issues if issue.severity is IssueSeverity.ERROR
        )
        warnings = sum(
            issue.occurrence_count for issue in issues if issue.severity is IssueSeverity.WARNING
        )
        status = ParseStatus.SUCCESS
        if not blocks:
            status = ParseStatus.FAILED
        elif loaded.structural_recovery or errors:
            status = ParseStatus.PARTIAL

        return CanonicalDocument(
            document_id=f"{filing_id}:{source.source_role.value}",
            filing_id=filing_id,
            document_role=source.source_role,
            title_raw=title,
            title_normalized=normalize_text(title),
            primary_source_file_id=source.source_file_id,
            source_file_ids=[source.source_file_id],
            sections=[section],
            blocks=blocks,
            parse_summary=ParseSummary(
                status=status,
                parser_name=type(self).__name__,
                parser_version=self.parser_version,
                detected_encoding=loaded.detected_encoding,
                recovered=loaded.recovered,
                source_element_count=sum(1 for _ in loaded.root.iter()),
                emitted_section_count=1,
                emitted_block_count=len(blocks),
                emitted_table_count=sum(block.table is not None for block in blocks),
                warning_count=warnings,
                error_count=errors,
            ),
            parse_issues=issues,
        )
