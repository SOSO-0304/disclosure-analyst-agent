"""Loss-minimising parser for DART DOCUMENT XML sources."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

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
from disclosure_agent.parsing.table_parser import parse_table, raw_element_text
from disclosure_agent.parsing.text_normalizer import normalize_text
from disclosure_agent.parsing.xml_loader import load_dart_xml

_BLOCK_TEXT_TAGS = {"P", "PARAGRAPH", "NOTE", "LIST", "WARNING"}
_TABLE_TAGS = {"TABLE", "TABLE-GROUP"}


def _local_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    return etree.QName(element).localname.upper()


def _section_level(element: etree._Element) -> int | None:
    name = _local_name(element)
    if not name.startswith("SECTION-"):
        return None
    try:
        return int(name.split("-", 1)[1])
    except ValueError:
        return None


def _xpath(element: etree._Element) -> str | None:
    try:
        return element.getroottree().getpath(element)
    except (AttributeError, ValueError):
        return None


class DartParser:
    """Parse one physical DART XML file into one semantic document."""

    parser_version = "2.0.0"

    def parse(
        self,
        path: str | Path,
        source: SourceFile,
        *,
        filing_id: str,
        title: str,
    ) -> CanonicalDocument:
        loaded = load_dart_xml(path, source.source_file_id)
        self.source = source
        self.filing_id = filing_id
        self.sections: list[CanonicalSection] = []
        self.blocks: list[CanonicalBlock] = []
        self.issues: list[ParseIssue] = list(loaded.issues)

        body_nodes = loaded.root.xpath("//*[local-name()='BODY']")
        body = body_nodes[0] if body_nodes else loaded.root
        top_sections = [child for child in body if _section_level(child) is not None]

        if top_sections:
            for order, element in enumerate(top_sections):
                self._parse_section(element, parent_id=None, order=order)
        else:
            root_section_id = self._new_section_id()
            self.sections.append(
                CanonicalSection(
                    section_id=root_section_id,
                    order=0,
                    level=0,
                    title_raw=title,
                    title_normalized=normalize_text(title),
                    source_locator=SourceLocator(
                        source_file_id=source.source_file_id,
                        xpath=_xpath(body),
                    ),
                )
            )
            for child in body:
                self._emit_content(child, root_section_id)

        if not self.blocks:
            self.issues.append(
                ParseIssue(
                    issue_code="empty_document",
                    severity=IssueSeverity.ERROR,
                    message="No canonical content blocks were emitted from the XML source.",
                    source_file_id=source.source_file_id,
                )
            )

        error_count = sum(issue.severity is IssueSeverity.ERROR for issue in self.issues)
        warning_count = sum(issue.severity is IssueSeverity.WARNING for issue in self.issues)
        if not self.blocks:
            status = ParseStatus.FAILED
        elif loaded.recovered or error_count or warning_count:
            status = ParseStatus.PARTIAL
        else:
            status = ParseStatus.SUCCESS

        return CanonicalDocument(
            document_id=f"{filing_id}:{source.source_role.value}",
            filing_id=filing_id,
            document_role=source.source_role,
            title_raw=title,
            title_normalized=normalize_text(title),
            primary_source_file_id=source.source_file_id,
            source_file_ids=[source.source_file_id],
            sections=self.sections,
            blocks=self.blocks,
            parse_summary=ParseSummary(
                status=status,
                parser_name=type(self).__name__,
                parser_version=self.parser_version,
                detected_encoding=loaded.detected_encoding,
                recovered=loaded.recovered,
                source_element_count=sum(1 for _ in loaded.root.iter()),
                emitted_section_count=len(self.sections),
                emitted_block_count=len(self.blocks),
                emitted_table_count=sum(
                    block.block_type is BlockType.TABLE for block in self.blocks
                ),
                warning_count=warning_count,
                error_count=error_count,
            ),
            parse_issues=self.issues,
            attributes_raw={str(key): value for key, value in loaded.root.attrib.items()},
        )

    def _new_section_id(self) -> str:
        return f"{self.filing_id}:{self.source.source_file_id}:section:{len(self.sections) + 1}"

    def _new_block_id(self) -> str:
        return f"{self.filing_id}:{self.source.source_file_id}:block:{len(self.blocks) + 1}"

    def _parse_section(
        self,
        element: etree._Element,
        *,
        parent_id: str | None,
        order: int,
    ) -> None:
        section_id = self._new_section_id()
        level = _section_level(element) or 1
        title_node = next(
            (child for child in element if _local_name(child) == "TITLE"),
            None,
        )
        title_raw = raw_element_text(title_node) if title_node is not None else None
        self.sections.append(
            CanonicalSection(
                section_id=section_id,
                parent_section_id=parent_id,
                order=order,
                level=level,
                title_raw=title_raw,
                title_normalized=normalize_text(title_raw),
                source_locator=SourceLocator(
                    source_file_id=self.source.source_file_id,
                    xpath=_xpath(element),
                ),
                attributes_raw={str(key): value for key, value in element.attrib.items()},
            )
        )
        if normalize_text(title_raw):
            self.blocks.append(
                CanonicalBlock(
                    block_id=self._new_block_id(),
                    section_id=section_id,
                    order=len(self.blocks),
                    block_type=BlockType.HEADING,
                    text_raw=title_raw,
                    text_normalized=normalize_text(title_raw),
                    heading_level=min(max(level, 1), 6),
                    source_locator=SourceLocator(
                        source_file_id=self.source.source_file_id,
                        xpath=_xpath(title_node),
                    ),
                )
            )

        child_section_order = 0
        for child in element:
            if child is title_node:
                continue
            if _section_level(child) is not None:
                self._parse_section(
                    child,
                    parent_id=section_id,
                    order=child_section_order,
                )
                child_section_order += 1
            else:
                self._emit_content(child, section_id)

    def _emit_content(self, element: etree._Element, section_id: str) -> None:
        name = _local_name(element)
        if name == "PGBRK":
            self.blocks.append(
                CanonicalBlock(
                    block_id=self._new_block_id(),
                    section_id=section_id,
                    order=len(self.blocks),
                    block_type=BlockType.PAGE_BREAK,
                    source_locator=SourceLocator(
                        source_file_id=self.source.source_file_id,
                        xpath=_xpath(element),
                    ),
                )
            )
            return

        if name == "TABLE":
            table_number = sum(block.table is not None for block in self.blocks) + 1
            table_id = f"{self.filing_id}:{self.source.source_file_id}:table:{table_number}"
            table = parse_table(element, table_id, self.source.source_file_id)
            self.blocks.append(
                CanonicalBlock(
                    block_id=self._new_block_id(),
                    section_id=section_id,
                    order=len(self.blocks),
                    block_type=BlockType.TABLE,
                    table=table,
                    source_locator=SourceLocator(
                        source_file_id=self.source.source_file_id,
                        xpath=_xpath(element),
                    ),
                )
            )
            return

        contains_structural_children = any(
            _section_level(child) is not None or _local_name(child) in _TABLE_TAGS
            for child in element
        )
        if name == "TABLE-GROUP" or contains_structural_children:
            for child in element:
                if _section_level(child) is not None:
                    self._parse_section(child, parent_id=section_id, order=0)
                else:
                    self._emit_content(child, section_id)
            return

        raw_text = raw_element_text(element)
        normalized = normalize_text(raw_text)
        if normalized is None:
            return
        block_type = BlockType.PARAGRAPH
        if name == "NOTE":
            block_type = BlockType.NOTE
        elif name == "LIST":
            block_type = BlockType.LIST
        elif name not in _BLOCK_TEXT_TAGS:
            block_type = BlockType.UNKNOWN
        self.blocks.append(
            CanonicalBlock(
                block_id=self._new_block_id(),
                section_id=section_id,
                order=len(self.blocks),
                block_type=block_type,
                text_raw=raw_text,
                text_normalized=normalized,
                source_locator=SourceLocator(
                    source_file_id=self.source.source_file_id,
                    xpath=_xpath(element),
                ),
                attributes_raw={str(key): value for key, value in element.attrib.items()},
            )
        )
