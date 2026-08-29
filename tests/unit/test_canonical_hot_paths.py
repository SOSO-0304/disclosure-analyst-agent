from lxml import etree

import disclosure_agent.parsing.markup_repair as markup_repair
from disclosure_agent.parsing.table_parser import parse_table


def test_tag_end_scanner_preserves_quoted_delimiters_and_prose_apostrophes() -> None:
    assert markup_repair._find_tag_end(b'<TD NOTE="1 > 0">value</TD>', 0) == 16
    assert markup_repair._find_tag_end("<신설 '23. 3.16.>".encode(), 0) == 18
    assert markup_repair._find_tag_end(b"<P>value < 10</P>", 9) is None


def test_known_dart_tags_skip_generic_validation(monkeypatch) -> None:
    raw = (
        b'<DOCUMENT><BODY><TABLE ACLASS="EXTRACTION"><TR>'
        b'<TD ROWSPAN="1">R&amp;D</TD></TR></TABLE></BODY></DOCUMENT>'
    )

    def fail_if_called(token: bytes) -> bool:
        raise AssertionError(f"known DART tag reached generic validation: {token!r}")

    monkeypatch.setattr(markup_repair, "_is_valid_tag", fail_if_called)

    assert markup_repair.repair_dart_xml(raw, "source").content == raw


def test_table_attributes_are_looked_up_once_without_changing_raw_values() -> None:
    table = parse_table(
        etree.fromstring(
            b'<TABLE><TR><TE RowSpan="2" ColSpan="2" AUnitValue="KRW" '
            b'ACurrency="KRW" ACode="revenue" AContext="current" '
            b'ADecimal="0" ANegated="true">100</TE></TR></TABLE>'
        ),
        "table",
        "source",
    )

    cell = table.cells[0]
    assert cell.row_span == 2
    assert cell.column_span == 2
    assert cell.is_header is True
    assert cell.numeric_value == -100
    assert cell.unit_raw == "KRW"
    assert cell.currency == "KRW"
    assert cell.concept_code == "revenue"
    assert cell.context_ref == "current"
    assert cell.decimals_raw == "0"
    assert cell.attributes_raw == {
        "RowSpan": "2",
        "ColSpan": "2",
        "AUnitValue": "KRW",
        "ACurrency": "KRW",
        "ACode": "revenue",
        "AContext": "current",
        "ADecimal": "0",
        "ANegated": "true",
    }
