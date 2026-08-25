"""Common parser for periodic, major and holding DART XML documents."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from lxml import etree

from disclosure_agent.domain.models import DisclosureField, DisclosureTable, Section, TextBlock
from disclosure_agent.parsing.table_parser import element_text, parse_table
from disclosure_agent.parsing.xml_loader import load_dart_xml


class DartParser:
    def parse_files(self, file_paths: list[str | Path]) -> tuple[list[Section], list[DisclosureField]]:
        sections: list[Section] = []
        flat_fields: list[DisclosureField] = []
        for file_no, path in enumerate(file_paths, 1):
            root = load_dart_xml(path)
            body = _first(root.xpath("//BODY")) or root
            top = [e for e in body if _section_level(e.tag) is not None]
            for order, element in enumerate(top, 1):
                section = self._parse_section(element, [], f"f{file_no}", order)
                sections.append(section)
                _collect_fields(section, flat_fields)
        return sections, flat_fields

    def _parse_section(self, element: etree._Element, parents: list[str], prefix: str, order: int) -> Section:
        level = _section_level(element.tag) or 1
        title_node = _first(element.xpath("./TITLE"))
        title = element_text(title_node) if title_node is not None else None
        path = parents + ([title] if title else [])
        section_id = f"{prefix}:section:{order}:{level}"
        text_blocks: list[TextBlock] = []
        tables: list[DisclosureTable] = []
        fields: list[DisclosureField] = []
        children: list[Section] = []
        text_order = table_order = field_order = child_order = 0

        for child in element:
            tag = child.tag.upper() if isinstance(child.tag, str) else ""
            child_level = _section_level(tag)
            if child_level is not None:
                child_order += 1
                children.append(self._parse_section(child, path, f"{section_id}.{child_order}", child_order))
                continue
            if tag == "TITLE":
                continue
            if tag in {"TABLE-GROUP", "TABLE"}:
                table_elements = [child] if tag == "TABLE" else child.xpath("./TABLE")
                for table_el in table_elements:
                    table_order += 1
                    table = parse_table(table_el, f"{section_id}:table:{table_order}", table_order, path)
                    tables.append(table)
                    for row in table.rows:
                        coded = [c for c in row.cells if c.code]
                        if not coded:
                            continue
                        # Preserve every coded DART cell; semantic normalization happens later.
                        for cell in coded:
                            field_order += 1
                            fields.append(DisclosureField(
                                label=cell.code or "unknown", value=cell.text, path=path,
                                code=cell.code, unit=cell.unit, unit_value=cell.unit_value,
                                raw_value=cell.text, order=field_order,
                            ))
                continue
            # Paragraph-like nodes outside tables. Avoid recursively flattening nested tables.
            if not child.xpath(".//TABLE"):
                text = element_text(child)
                if text:
                    text_order += 1
                    text_blocks.append(TextBlock(text=text, order=text_order))

        return Section(section_id=section_id, level=level, title=title, path=path,
                       text_blocks=text_blocks, fields=fields, tables=tables,
                       children=children, order=order)


def _section_level(tag: str) -> int | None:
    tag = str(tag).upper()
    if not tag.startswith("SECTION-"):
        return None
    try:
        return int(tag.split("-", 1)[1])
    except ValueError:
        return None


def _first(items):
    return items[0] if items else None


def _collect_fields(section: Section, target: list[DisclosureField]) -> None:
    target.extend(section.fields)
    for child in section.children:
        _collect_fields(child, target)
