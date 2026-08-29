#!/usr/bin/env python3
"""Read-only canonical audit (stdlib only, Python 3.11+).

Run from the project root:
  python scripts/canonical_audit.py --input data/processed/canonical-v22-smoke.jsonl
  python scripts/canonical_audit.py --self-test

Never imports or executes disclosure_agent parsers, changes source files, runs
Git, contacts a network, or replaces an earlier output. Writes a NEW audit folder
and a ZIP of diagnostic reports only. MATCH means the stated comparison matched,
not that a document is certified lossless. Table comparison is sampled by default.
"""

from __future__ import annotations

import argparse
import codecs
import gzip
import hashlib
import json
import re
import sys
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from html.entities import html5
from pathlib import Path

VERSION = "1.1.0"
# Exact receipts from the user's previous FAILED DOCUMENTS report, not a name guess.
KNOWN_FAILURES = {
    **dict.fromkeys(
        (
            "20240318000420",
            "20240514001108",
            "20240814002522",
            "20241114001327",
            "20260316001313",
            "20260515002689",
            "20260515002756",
        ),
        "PUBG",
    ),
    **dict.fromkeys(
        ("20230515001087", "20230814001827", "20231114002343", "20240320000873"), "PERIOD"
    ),
    **dict.fromkeys(
        (
            "20230515000336",
            "20230814000127",
            "20231114000318",
            "20240315000844",
            "20240320001078",
            "20250814002202",
            "20251114000867",
            "20260323001211",
            "20260515001051",
        ),
        "SOURCE",
    ),
}
PATTERNS = {
    "PUBG": re.compile(r"<PUBG:\s*[^<>]{1,200}>"),
    "BGMI": re.compile(r"<BGMI>"),
    "PERIOD": re.compile(r"<기간:\s*[^<>]{1,500}>"),
    "SOURCE": re.compile(r"<자료:\s*[^<>]{1,500}>"),
    "R&D": re.compile(r"R&D"),
    "M&A": re.compile(r"M&A"),
    "S&P": re.compile(r"S&P"),
}
ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9a-fA-F]+);")
LITERAL_LABELS = {
    "BGMI",
    "STS",
    "CG",
    "MANIFESTO",
    "GranData",
    "DataBada",
    "DREAM",
    "SIT",
    "IIT",
    "SHEESH",
}
NAME = r"[A-Za-z_][A-Za-z0-9_.:-]*"
ATTR = rf"\s+{NAME}\s*=\s*(?:\"[^\"]*\"|'[^']*')"
TOKEN = re.compile(
    r"(?P<comment><!--[\s\S]*?-->)"
    r"|(?P<cdata><!\[CDATA\[[\s\S]*?\]\]>)"
    r"|(?P<pi><\?[\s\S]*?\?>)"
    r"|(?P<doctype><!DOCTYPE(?:[^>\[\"']|\"[^\"]*\"|'[^']*'|\[[\s\S]*?\])*> )"
    rf"|(?P<tag></?{NAME}(?:{ATTR})*\s*/?>)",
    re.VERBOSE,
)
TAGNAME = re.compile(rf"</?({NAME})")
BOUNDARY = re.compile(
    r"^(?:P|PARAGRAPH|TITLE|NOTE|LIST|TR|TD|TH|TE|TU|TABLE|CAPTION|BODY|SECTION-\d+)$"
)
MAX_SPAN = 10000
MAX_CELLS = 200000


def norm(text):
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


def xml_unescape(text):
    """Decode standard XML/HTML references once; leave unknown entities literal."""
    mapping = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}

    def replace(match):
        key = match.group()[1:-1]
        if key in mapping:
            return mapping[key]
        if not key.startswith("#"):
            return html5.get(key + ";", match.group())
        try:
            return chr(int(key[2:], 16) if key.startswith("#x") else int(key[1:]))
        except (ValueError, OverflowError):
            return match.group()

    return ENTITY.sub(replace, text)


def lexical_tokens(text):
    """Omit reviewed unpaired prose labels from markup tokens, not the text view."""
    unpaired = {name for name in LITERAL_LABELS if not re.search(r"</" + name + r"\s*>", text)}
    return [
        match
        for match in TOKEN.finditer(text)
        if not (match.lastgroup == "tag" and match.group()[1:-1].strip() in unpaired)
    ]


def decode_bytes(raw, hint=None):
    encodings = []
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings.append("utf-32")
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings.append("utf-16")
    declared = re.search(rb"encoding\s*=\s*['\"]([^'\"]+)['\"]", raw[:512], re.I)
    if declared:
        encodings.append(declared.group(1).decode("ascii", errors="strict"))
    encodings.extend([hint, "utf-8-sig", "cp949", "euc-kr"])
    for encoding in dict.fromkeys(encodings):
        if not encoding or "replace" in encoding:
            continue
        try:
            return raw.decode(encoding, errors="strict"), encoding
        except (UnicodeError, LookupError):
            continue
    raise ValueError("No lossless supported decoding; no replacement characters were inserted")


def lexical_table_paths(tokens):
    """Construct element paths from tokens, independently of any XML recovery.

    Each path part retains its parent's final sibling counts so [1] is emitted
    only when the tag actually has same-name siblings (lxml getpath convention).
    """
    frames, tables = [], {}
    roots = Counter()
    for match in tokens:
        if match.lastgroup != "tag":
            continue
        token = match.group()
        name = TAGNAME.match(token).group(1)
        if token.startswith("</"):
            for index in range(len(frames) - 1, -1, -1):
                if frames[index]["name"] == name:
                    del frames[index:]
                    break
            continue
        siblings = frames[-1]["children"] if frames else roots
        siblings[name] += 1
        frame = {"name": name, "index": siblings[name], "siblings": siblings, "children": Counter()}
        if name.upper() == "TABLE":
            tables[match.start()] = [*frames, frame]
        if not token.rstrip().endswith("/>"):
            frames.append(frame)
    paths = {}
    for offset, chain in tables.items():
        if any(":" in item["name"] for item in chain):
            paths[offset] = None  # namespace-dependent paths require manual review
        else:
            paths[offset] = "/" + "/".join(
                item["name"] + (f"[{item['index']}]" if item["siblings"][item["name"]] > 1 else "")
                for item in chain
            )
    return paths


def lexical_view(text, body_only=True):
    """Independent lexical text view + all TABLE ranges; not a recovering XML parser.

    Quoted attributes, comments and processing instructions are excluded; CDATA is
    literal. Known natural-language pseudo-tags remain text. Stack anomalies are
    reported and prevent a document-wide MATCH verdict.
    """
    tokens = lexical_tokens(text)
    start, end = 0, len(text)
    reviews = []
    if body_only:
        opens = [
            m
            for m in tokens
            if m.lastgroup == "tag"
            and TAGNAME.match(m.group()).group(1).upper() == "BODY"
            and not m.group().startswith("</")
        ]
        closes = [
            m
            for m in tokens
            if m.lastgroup == "tag"
            and TAGNAME.match(m.group()).group(1).upper() == "BODY"
            and m.group().startswith("</")
        ]
        if len(opens) == 1 and len(closes) == 1 and opens[0].end() <= closes[0].start():
            start, end = opens[0].end(), closes[0].start()
        else:
            reviews.append("BODY boundary missing/ambiguous: text scope is entire source")
    pieces, ranges, stack = [], [], []
    table_stack = []
    cursor = start
    for match in tokens:
        if match.start() < start or match.end() > end:
            continue
        pieces.append(xml_unescape(text[cursor : match.start()]))
        cursor = match.end()
        kind, token = match.lastgroup, match.group()
        if kind == "cdata":
            pieces.append(token[9:-3])
        elif kind == "tag":
            name = TAGNAME.match(token).group(1).upper()
            closing = token.startswith("</")
            self_closing = token.rstrip().endswith("/>")
            if BOUNDARY.match(name):
                pieces.append("\n")
            if closing:
                if not stack or stack[-1] != name:
                    reviews.append("Unbalanced lexical tag: " + name)
                    if name in stack:
                        del stack[stack.index(name) :]
                else:
                    stack.pop()
            elif not self_closing:
                stack.append(name)
            if name == "TABLE":
                if closing:
                    if table_stack:
                        item = table_stack.pop()
                        item["end"] = match.end()
                        ranges.append(item)
                    else:
                        reviews.append("Unmatched TABLE close")
                else:
                    item = {
                        "start": match.start(),
                        "end": match.end(),
                        "nested": False,
                        "depth": len(table_stack),
                    }
                    if table_stack:
                        for parent in table_stack:
                            parent["nested"] = True
                    if self_closing:
                        ranges.append(item)
                    else:
                        table_stack.append(item)
    pieces.append(xml_unescape(text[cursor:end]))
    if stack or table_stack:
        reviews.append("Unclosed lexical elements")
    paths = lexical_table_paths(tokens)
    ranges.sort(key=lambda item: item["start"])
    for item in ranges:
        item["xpath"] = paths.get(item["start"])
    return "".join(pieces), ranges, sorted(set(reviews))


def canonical_fragments(document):
    """Actual emitted fields only; no attributes, issue examples or duplicate titles."""
    fragments = []
    for block in document.get("blocks", []):
        if block.get("table") is not None:
            table = block["table"]
            if table.get("caption_raw"):
                fragments.append((table["caption_raw"], block.get("block_id"), "caption"))
            for cell in table.get("cells", []):
                fragments.append(
                    (
                        cell.get("text_raw", ""),
                        block.get("block_id"),
                        [cell.get("row_index"), cell.get("column_index")],
                    )
                )
        elif block.get("text_raw") is not None:
            fragments.append((block["text_raw"], block.get("block_id"), None))
    return fragments


def text_comparisons(source_text, document):
    visible, ranges, reviews = lexical_view(source_text)
    source_display = norm(visible)
    fragments = [(norm(text), bid, cell) for text, bid, cell in canonical_fragments(document)]
    results = []
    for label, pattern in PATTERNS.items():
        expected = Counter(m.group() for m in pattern.finditer(source_display))
        actual = Counter()
        for text, _, _ in fragments:
            actual.update(m.group() for m in pattern.finditer(text))
        if not expected and not actual:
            continue
        missing = expected - actual
        extra = actual - expected
        outcome = "MATCH" if not missing and not extra else "REVIEW"
        if reviews:
            outcome = "REVIEW"
        examples = []
        for expression in list(dict.fromkeys([*missing, *extra, *expected]))[:5]:
            position = source_display.find(expression)
            found = [(text, bid, cell) for text, bid, cell in fragments if expression in text]
            examples.append(
                {
                    "expression": expression,
                    "source_count": expected[expression],
                    "canonical_count": actual[expression],
                    "source_display_excerpt": source_display[
                        max(0, position - 100) : position + len(expression) + 100
                    ]
                    if position >= 0
                    else None,
                    "canonical_hits": [
                        {
                            "block_id": bid,
                            "cell": cell,
                            "excerpt": text[
                                max(0, text.find(expression) - 80) : text.find(expression)
                                + len(expression)
                                + 80
                            ],
                        }
                        for text, bid, cell in found[:3]
                    ],
                }
            )
        results.append(
            {
                "target": label,
                "outcome": outcome,
                "source_occurrences": sum(expected.values()),
                "canonical_occurrences": sum(actual.values()),
                "missing_occurrences": sum(missing.values()),
                "extra_occurrences": sum(extra.values()),
                "scope_warnings": reviews,
                "examples": examples,
            }
        )
    return results, ranges, reviews


def local_tag(element):
    return element.tag.rsplit("}", 1)[-1].upper() if isinstance(element.tag, str) else ""


def independent_reference(fragment):
    """Strict stdlib XML parse; limited lexical repairs only, never recover=True.

    Repairs are independent of production code, and disclosed in each result.
    Unknown structural damage or entities remain unverifiable.
    """
    mode = "strict_original_fragment"
    try:
        root = ET.fromstring(fragment)
    except ET.ParseError:
        pieces, cursor = [], 0

        # Protect comments, CDATA and markup; only repair data text segments.
        def repair_segment(segment):
            segment = ENTITY.sub(
                lambda m: (
                    "".join(f"&#{ord(c)};" for c in xml_unescape(m.group()))
                    if xml_unescape(m.group()) != m.group()
                    else m.group()
                ),
                segment,
            )
            segment = re.sub(r"&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)", "&amp;", segment)
            for key in ("PUBG", "PERIOD", "SOURCE", "BGMI"):
                segment = PATTERNS[key].sub(
                    lambda m: m.group().replace("<", "&lt;").replace(">", "&gt;"), segment
                )
            return segment

        for token in lexical_tokens(fragment):
            pieces.append(repair_segment(fragment[cursor : token.start()]))
            pieces.append(token.group())
            cursor = token.end()
        pieces.append(repair_segment(fragment[cursor:]))
        root = ET.fromstring("".join(pieces))
        mode = "strict_after_limited_text_escapes"
    if local_tag(root) != "TABLE":
        raise ValueError("Reference root is not TABLE")
    parents = {child: parent for parent in root.iter() for child in parent}

    def owner(node):
        while node in parents:
            node = parents[node]
            if local_tag(node) == "TABLE":
                return node
        return None

    def owned_text(node):
        parts = [node.text or ""]
        for child in node:
            if local_tag(child) != "TABLE":
                parts.append(owned_text(child))
            parts.append(child.tail or "")
        return "".join(parts)

    rows = [node for node in root.iter() if local_tag(node) == "TR" and owner(node) is root]
    occupied, expected = defaultdict(list), []
    row_count, column_count = len(rows), 0
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            if local_tag(cell) not in {"TD", "TH", "TE", "TU"}:
                continue
            attrs = {key.lower(): value for key, value in cell.attrib.items()}
            rs, cs = int(attrs.get("rowspan", "1")), int(attrs.get("colspan", "1"))
            if not 1 <= rs <= MAX_SPAN or not 1 <= cs <= MAX_SPAN:
                raise ValueError("Invalid or excessive source span")
            while True:
                collisions = [
                    (left, right) for left, right in occupied[r] if c < right and c + cs > left
                ]
                if not collisions:
                    break
                c = max(right for _, right in collisions)
            expected.append(
                {
                    "row_index": r,
                    "column_index": c,
                    "row_span": rs,
                    "column_span": cs,
                    "text_raw": owned_text(cell),
                    "nested_table_count": sum(
                        local_tag(node) == "TABLE" and owner(node) is root
                        for node in cell.iter()
                        if node is not cell
                    ),
                    "is_header": local_tag(cell) in {"TH", "TE"},
                }
            )
            if len(expected) > MAX_CELLS:
                raise ValueError("Table cell limit exceeded")
            for occupied_row in range(r, r + rs):
                occupied[occupied_row].append((c, c + cs))
            row_count = max(row_count, r + rs)
            column_count = max(column_count, c + cs)
            c += cs
    return {"row_count": row_count, "column_count": column_count, "cells": expected}, mode


def grid_findings(table):
    """Canonical-only bounds, complete rectangle overlap, and invalid spans."""
    findings, occupied = [], defaultdict(list)
    rows, cols = table.get("row_count"), table.get("column_count")
    if type(rows) is not int or type(cols) is not int or rows < 0 or cols < 0:
        return ["invalid_dimensions"]
    cells = table.get("cells", [])
    if len(cells) > MAX_CELLS:
        return ["cell_limit_unverified"]
    covered_rows = 0
    for cell in cells:
        values = [cell.get(k) for k in ("row_index", "column_index", "row_span", "column_span")]
        if any(type(value) is not int for value in values):
            findings.append("invalid_coordinate_type")
            continue
        r, c, rs, cs = values
        if min(r, c) < 0 or not 1 <= rs <= MAX_SPAN or not 1 <= cs <= MAX_SPAN:
            findings.append("invalid_or_excessive_span")
            continue
        covered_rows += rs
        if covered_rows > 1000000:
            return sorted(set(findings + ["grid_resource_limit_unverified"]))
        if r + rs > rows or c + cs > cols:
            findings.append("out_of_bounds")
        for row in range(r, r + rs):
            if any(c < end and c + cs > start for start, end in occupied[row]):
                findings.append("overlapping_cell_rectangles")
            occupied[row].append((c, c + cs))
    return sorted(set(findings))


def compare_table(fragment, table):
    result = {
        "outcome": "REVIEW",
        "reference_mode": None,
        "canonical_grid_findings": grid_findings(table),
    }
    try:
        expected, mode = independent_reference(fragment)
    except (ET.ParseError, ValueError, RecursionError) as exc:
        return {**result, "reason": "source_fragment_unverifiable", "error": str(exc)[:700]}
    result["reference_mode"] = mode
    actual = table.get("cells", [])
    result["source_dimensions"] = [expected["row_count"], expected["column_count"]]
    result["canonical_dimensions"] = [table.get("row_count"), table.get("column_count")]
    result["source_cell_count"], result["canonical_cell_count"] = (
        len(expected["cells"]),
        len(actual),
    )
    dims_match = result["source_dimensions"] == result["canonical_dimensions"]
    differences, raw_only = [], 0
    for index in range(max(len(expected["cells"]), len(actual))):
        source_cell = expected["cells"][index] if index < len(expected["cells"]) else None
        actual_cell = actual[index] if index < len(actual) else None
        changed = []
        if source_cell is None or actual_cell is None:
            changed.append("missing_or_extra_cell")
        else:
            for key in ("row_index", "column_index", "row_span", "column_span", "is_header"):
                if source_cell[key] != actual_cell.get(key):
                    changed.append(key)
            if source_cell["nested_table_count"] != len(actual_cell.get("nested_table_ids", [])):
                changed.append("nested_table_count")
            if norm(source_cell["text_raw"]) != norm(actual_cell.get("text_raw", "")):
                changed.append("display_text")
            elif source_cell["text_raw"] != actual_cell.get("text_raw", ""):
                raw_only += 1
        if changed and len(differences) < 12:

            def brief(cell):
                if cell is None:
                    return None
                return {
                    key: (str(value)[:700] if key == "text_raw" else value)
                    for key, value in cell.items()
                    if key
                    in {
                        "row_index",
                        "column_index",
                        "row_span",
                        "column_span",
                        "is_header",
                        "text_raw",
                    }
                }

            differences.append(
                {
                    "cell_order": index,
                    "fields": changed,
                    "source": brief(source_cell),
                    "canonical": brief(actual_cell),
                }
            )
    result["differences_first_12"] = differences
    result["whitespace_or_unicode_only_cell_differences"] = raw_only
    if dims_match and not differences and not result["canonical_grid_findings"]:
        result["outcome"] = "MATCH_DISPLAY" if raw_only else "MATCH_RAW"
    return result


class Sources:
    def __init__(self, root):
        self.root = root.resolve()
        self.index = defaultdict(list)
        scan_root = root / "raw" if (root / "raw").is_dir() else root
        for path in scan_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".xml", ".html", ".htm", ".pdf"}:
                resolved = path.resolve()
                try:
                    resolved.relative_to(self.root)
                except ValueError:
                    continue
                key = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
                self.index[key].append(resolved)

    def resolve(self, source):
        candidates = set()
        for field in ("archive_path_normalized", "archive_path_raw"):
            value = source.get(field)
            if not value:
                continue
            key = unicodedata.normalize("NFC", value.replace("\\", "/"))
            if key.startswith("/") or ".." in key.split("/") or re.match(r"^[A-Za-z]:", key):
                raise ValueError("Unsafe/nonportable source path: " + value)
            candidates.update(self.index.get(key, []))
        if len(candidates) != 1:
            raise ValueError(
                "Source resolution is " + ("missing" if not candidates else "ambiguous")
            )
        return candidates.pop()


def json_rows(path):
    with path.open("rb") as raw_stream:
        compressed = raw_stream.peek(2)[:2] == b"\x1f\x8b"
        stream = gzip.GzipFile(fileobj=raw_stream, mode="rb") if compressed else raw_stream
        try:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    try:
                        yield number, json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSON at line {number}: {exc}") from exc
        finally:
            if stream is not raw_stream:
                stream.close()


def short_issues(issues):
    # Retain every issue group and count; bounded diagnostic examples only.
    result = []
    for issue in issues:
        item = {
            key: issue.get(key) for key in ("issue_code", "severity", "message", "source_locator")
        }
        item["occurrence_count"] = issue.get("occurrence_count", 1)
        details = issue.get("details") or {}
        item["details"] = {key: details[key] for key in ("type", "domain") if key in details}
        item["details"]["examples"] = (details.get("examples") or [])[:3]
        result.append(item)
    return result


def context(package, document, source):
    return {
        "filing_id": package.get("filing_id"),
        "receipt": package["filing"].get("receipt_number"),
        "company": package.get("company", {}).get("corp_name"),
        "group": package["filing"].get("document_group"),
        "report": package["filing"].get("report_name_raw"),
        "document_id": document.get("document_id"),
        "role": document.get("document_role"),
        "parse_status": document["parse_summary"].get("status"),
        "parser": document["parse_summary"].get("parser_name"),
        "source": source.get("archive_path_normalized"),
        "source_file_id": document.get("primary_source_file_id"),
    }


def choose_tables(blocks, count):
    if count == 0 or len(blocks) <= count:
        return list(range(len(blocks)))
    chosen = []

    def add(index):
        if index not in chosen and len(chosen) < count:
            chosen.append(index)

    add(0)
    for predicate in (
        lambda c: c.get("row_span", 1) > 1 or c.get("column_span", 1) > 1,
        lambda c: not norm(c.get("text_raw")),
        lambda c: bool(re.search(r"\d", c.get("text_raw", ""))),
    ):
        for index, block in enumerate(blocks):
            if any(predicate(cell) for cell in block["table"].get("cells", [])):
                add(index)
                break
    add(len(blocks) - 1)
    for index in range(len(blocks)):
        add(index)
    return sorted(chosen)


def pair_tables(ranges, blocks):
    """Use source locators; never shift all tables when a cover/TOC was omitted."""
    by_xpath = defaultdict(list)
    for item in ranges:
        if item.get("xpath"):
            by_xpath[item["xpath"]].append(item)
    matches, used = {}, set()
    all_missing_locators = all(
        not (block.get("source_locator") or {}).get("xpath") for block in blocks
    )
    for index, block in enumerate(blocks):
        xpath = (block.get("source_locator") or {}).get("xpath")
        candidates = by_xpath.get(xpath, [])
        if len(candidates) == 1:
            item, mode = candidates[0], "independent_lexical_xpath"
        elif all_missing_locators and len(ranges) == len(blocks):
            item, mode = ranges[index], "order_fallback_no_locators_equal_count"
        else:
            continue
        if item["start"] in used:
            continue
        matches[index] = (item, mode)
        used.add(item["start"])
    return matches, [item for item in ranges if item["start"] not in used]


def run_audit(args):
    input_path, data_root = args.input.resolve(), args.data_root.resolve()
    if not input_path.is_file():
        raise ValueError("Input JSONL not found: " + str(input_path))
    if not data_root.is_dir():
        raise ValueError("Data root not found: " + str(data_root))
    before_path = args.before
    if before_path is None:
        candidate = input_path.with_name("canonical-smoke.jsonl")
        before_path = (
            candidate if candidate.is_file() and candidate.resolve() != input_path else None
        )
    elif not before_path.is_file() or before_path.resolve() == input_path:
        raise ValueError("--before must be a different existing JSONL")
    baseline, baseline_duplicates = {}, []
    if before_path:
        print("Reading previous status snapshot (no parsing)...", flush=True)
        for _, package in json_rows(before_path):
            for doc in package["documents"]:
                key = doc["document_id"]
                if key in baseline:
                    baseline_duplicates.append(key)
                baseline[key] = doc["parse_summary"]["status"]
    print("Indexing source paths once...", flush=True)
    sources = Sources(data_root)
    run_name = (
        "canonical-audit-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    output = args.output_root.resolve() / run_name
    output.mkdir(parents=True, exist_ok=False)
    streams = {}

    def emit(name, record):
        if name not in streams:
            streams[name] = (output / (name + ".jsonl")).open("x", encoding="utf-8")
        streams[name].write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    totals, status, by_group, by_parser, schema, versions = (Counter() for _ in range(6))
    issue_occ, issue_docs, message_occ, message_docs = (Counter() for _ in range(4))
    transitions, coverage, text_outcomes, table_outcomes, samples = (Counter() for _ in range(5))
    known_outcomes = Counter()
    seen_docs, seen_packages, seen_known = set(), set(), set()
    errors = []
    started = time.monotonic()
    input_stat = input_path.stat()
    try:
        for line_number, package in json_rows(input_path):
            totals["packages"] += 1
            fid = package["filing_id"]
            if fid in seen_packages:
                emit(
                    "input_findings",
                    {"line": line_number, "reason": "duplicate_filing_id", "filing_id": fid},
                )
                coverage["duplicate_filing_id"] += 1
            seen_packages.add(fid)
            schema[package.get("schema_version", "missing")] += 1
            source_map = {source["source_file_id"]: source for source in package["source_files"]}
            for doc in package["documents"]:
                totals["documents"] += 1
                summary = doc["parse_summary"]
                state = summary["status"]
                source = source_map.get(doc.get("primary_source_file_id"), {})
                ctx = context(package, doc, source)
                did, group, parser = ctx["document_id"], ctx["group"], ctx["parser"]
                if did in seen_docs:
                    coverage["duplicate_document_id"] += 1
                    emit("input_findings", {**ctx, "reason": "duplicate_document_id"})
                seen_docs.add(did)
                status[state] += 1
                by_group[(group, state)] += 1
                by_parser[(parser, state)] += 1
                versions[(parser, summary.get("parser_version"))] += 1
                if before_path:
                    transitions[(baseline.get(did, "not_in_baseline"), state)] += 1
                is_known = ctx["receipt"] in KNOWN_FAILURES and ctx["role"] == "primary_report"
                if is_known:
                    seen_known.add(ctx["receipt"])
                issues = doc.get("parse_issues", [])
                if state != "success":
                    emit(
                        "partial_and_failed_documents",
                        {**ctx, "parse_summary": summary, "issues": short_issues(issues)},
                    )
                code_set, msg_set = set(), set()
                for issue in issues:
                    count = issue.get("occurrence_count", 1)
                    if type(count) is not int or count < 1:
                        raise ValueError("Invalid occurrence_count in " + str(did))
                    key = (state, issue.get("issue_code"), issue.get("severity"))
                    details = issue.get("details") or {}
                    msg_key = (
                        *key,
                        details.get("domain"),
                        details.get("type"),
                        issue.get("message"),
                    )
                    issue_occ[key] += count
                    message_occ[msg_key] += count
                    code_set.add(key)
                    msg_set.add(msg_key)
                issue_docs.update(code_set)
                message_docs.update(msg_set)
                blocks = [
                    block for block in doc.get("blocks", []) if block.get("table") is not None
                ]
                totals["canonical_tables"] += len(blocks)
                for block in blocks:
                    findings = grid_findings(block["table"])
                    if findings:
                        coverage["tables_with_internal_findings"] += 1
                        emit(
                            "table_grid_findings",
                            {
                                **ctx,
                                "table_id": block["table"].get("table_id"),
                                "findings": findings,
                            },
                        )
                if source.get("detected_content_format") != "dart_xml":
                    coverage["non_dart_source_not_compared"] += 1
                    emit(
                        "source_coverage",
                        {**ctx, "outcome": "SKIPPED", "reason": "non_DART_format_no_XML_claim"},
                    )
                    if is_known:
                        known_outcomes["UNVERIFIED_NON_DART_SOURCE"] += 1
                        emit(
                            "known_20_checks",
                            {
                                **ctx,
                                "required_target": KNOWN_FAILURES[ctx["receipt"]],
                                "outcome": "UNVERIFIED_NON_DART_SOURCE",
                            },
                        )
                    continue
                verification = {}
                checks = []
                try:
                    path = sources.resolve(source)
                    pre_stat = path.stat()
                    raw = path.read_bytes()
                    after_stat = path.stat()
                    digest = hashlib.sha256(raw).hexdigest()
                    verification = {
                        "source_sha256_now": digest,
                        "size_now": len(raw),
                        "baseline_hash_available": bool(source.get("sha256")),
                    }
                    binding_warnings = []
                    if (pre_stat.st_size, pre_stat.st_mtime_ns) != (
                        after_stat.st_size,
                        after_stat.st_mtime_ns,
                    ):
                        binding_warnings.append("source_changed_during_read")
                    if source.get("size_bytes") is not None and source["size_bytes"] != len(raw):
                        binding_warnings.append("source_size_differs_from_canonical_metadata")
                    if source.get("sha256") and source["sha256"] != digest:
                        binding_warnings.append("source_hash_differs_from_canonical_metadata")
                    source_text, encoding = decode_bytes(raw, summary.get("detected_encoding"))
                    verification["decoded_encoding"] = encoding
                    verification["binding_warnings"] = binding_warnings
                    checks, ranges, scope_warnings = text_comparisons(source_text, doc)
                    coverage["dart_sources_scanned"] += 1
                    if not checks:
                        coverage["dart_sources_without_target_occurrences"] += 1
                    for check in checks:
                        if binding_warnings:
                            check["outcome"] = "REVIEW"
                        text_outcomes[(check["target"], check["outcome"])] += 1
                        emit("text_checks", {**ctx, **verification, **check})
                    count_outcome = "MATCH_COUNT" if len(ranges) == len(blocks) else "REVIEW"
                    if scope_warnings or binding_warnings:
                        count_outcome = "REVIEW"
                    pairs, unrepresented = pair_tables(ranges, blocks)
                    if len(pairs) != len(blocks) or unrepresented:
                        count_outcome = "REVIEW"
                    coverage["table_coverage_" + count_outcome] += 1
                    emit(
                        "source_coverage",
                        {
                            **ctx,
                            **verification,
                            "outcome": count_outcome,
                            "source_table_count": len(ranges),
                            "source_outer_table_count": sum(item["depth"] == 0 for item in ranges),
                            "canonical_table_count": len(blocks),
                            "scope_warnings": scope_warnings,
                            "nested_tables": sum(item["nested"] for item in ranges),
                            "unmapped_canonical_tables": len(blocks) - len(pairs),
                            "unrepresented_source_tables": len(unrepresented),
                            "unrepresented_source_examples": [
                                {
                                    "xpath": item.get("xpath"),
                                    "line": source_text.count("\n", 0, item["start"]) + 1,
                                    "xml_excerpt": source_text[
                                        item["start"] : min(item["end"], item["start"] + 1000)
                                    ],
                                }
                                for item in unrepresented[:8]
                            ],
                        },
                    )
                    stratum = (group, state, parser)
                    selected = bool(blocks or ranges) and (
                        is_known
                        or baseline.get(did) == "failed"
                        or samples[stratum] < args.sample_documents
                        or args.all_tables
                    )
                    if selected:
                        samples[stratum] += 1
                        limit = 0 if args.all_tables else args.tables_per_document
                        for index in choose_tables(blocks, limit):
                            block = blocks[index]
                            wrong_source = (block.get("source_locator") or {}).get(
                                "source_file_id"
                            ) not in (None, doc.get("primary_source_file_id"))
                            if (
                                index not in pairs
                                or scope_warnings
                                or binding_warnings
                                or wrong_source
                            ):
                                table_outcomes["UNVERIFIED_PAIRING"] += 1
                                emit(
                                    "table_checks",
                                    {
                                        **ctx,
                                        "table_id": block["table"].get("table_id"),
                                        "canonical_locator": block.get("source_locator"),
                                        "outcome": "UNVERIFIED_PAIRING",
                                        "reason": (
                                            "source_locator_structure_or_identity_needs_review"
                                        ),
                                    },
                                )
                                continue
                            span, pairing_mode = pairs[index]
                            fragment = source_text[span["start"] : span["end"]]
                            result = compare_table(fragment, block["table"])
                            table_outcomes[result["outcome"]] += 1
                            emit(
                                "table_checks",
                                {
                                    **ctx,
                                    **verification,
                                    "table_order_zero_based": index,
                                    "table_id": block["table"].get("table_id"),
                                    "canonical_locator": block.get("source_locator"),
                                    "source_line": source_text.count("\n", 0, span["start"]) + 1,
                                    "source_xpath": span.get("xpath"),
                                    "source_fragment_sha256_utf8": hashlib.sha256(
                                        fragment.encode("utf-8")
                                    ).hexdigest(),
                                    "pairing": pairing_mode,
                                    "source_xml_excerpt": fragment[:2500]
                                    if result["outcome"] == "REVIEW"
                                    else fragment[:500],
                                    **result,
                                },
                            )
                except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                    coverage["source_unverified"] += 1
                    emit(
                        "source_coverage", {**ctx, "outcome": "UNVERIFIED", "error": str(exc)[:900]}
                    )
                if is_known:
                    required = KNOWN_FAILURES[ctx["receipt"]]
                    target = next((check for check in checks if check["target"] == required), None)
                    verdict = (
                        target["outcome"]
                        if target
                        else "UNVERIFIED_TARGET_ABSENT_OR_SOURCE_UNAVAILABLE"
                    )
                    if state in {"failed", "unsupported", "pending"}:
                        verdict = "REVIEW_PARSE_STATUS"
                    known_outcomes[verdict] += 1
                    emit(
                        "known_20_checks",
                        {
                            **ctx,
                            **verification,
                            "required_target": required,
                            "outcome": verdict,
                            "target_check": target,
                            "note": "Target MATCH does not certify whole-document preservation",
                        },
                    )
            if totals["packages"] % 100 == 0:
                print(
                    f"[audit {totals['packages']} packages] documents={totals['documents']} "
                    f"source_unverified={coverage['source_unverified']} "
                    f"elapsed={time.monotonic() - started:.0f}s",
                    flush=True,
                )
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    finally:
        for receipt in sorted(set(KNOWN_FAILURES) - seen_known):
            known_outcomes["DOCUMENT_NOT_IN_INPUT"] += 1
            emit(
                "known_20_checks",
                {
                    "receipt": receipt,
                    "required_target": KNOWN_FAILURES[receipt],
                    "outcome": "DOCUMENT_NOT_IN_INPUT",
                },
            )
        if (
            input_path.stat().st_size != input_stat.st_size
            or input_path.stat().st_mtime_ns != input_stat.st_mtime_ns
        ):
            errors.append("Input JSONL changed during audit; rerun on a stable file")
        for stream in streams.values():
            stream.close()
    missing_baseline = sorted(set(baseline) - seen_docs)

    def count_records(counter, fields):
        rows = []
        for key, count in counter.most_common():
            values = key if isinstance(key, tuple) else (key,)
            rows.append({**dict(zip(fields, values, strict=True)), "count": count})
        return rows

    issues = [
        {
            "status": key[0],
            "code": key[1],
            "severity": key[2],
            "documents": issue_docs[key],
            "occurrences": count,
        }
        for key, count in issue_occ.most_common()
    ]
    messages = [
        {
            "status": key[0],
            "code": key[1],
            "severity": key[2],
            "domain": key[3],
            "type": key[4],
            "message": key[5],
            "documents": message_docs[key],
            "occurrences": count,
        }
        for key, count in message_occ.most_common()
    ]
    summary = {
        "audit_version": VERSION,
        "completed": not errors,
        "errors": errors,
        "input_file": input_path.name,
        "input_size_bytes": input_stat.st_size,
        "baseline_file": before_path.name if before_path else None,
        "baseline_duplicate_ids": baseline_duplicates,
        "missing_baseline_documents": missing_baseline,
        "totals": dict(totals),
        "document_status": dict(status),
        "schema_versions": dict(schema),
        "status_by_group": count_records(by_group, ["group", "status"]),
        "status_by_parser": count_records(by_parser, ["parser", "status"]),
        "parser_versions": count_records(versions, ["parser", "version"]),
        "status_transitions": count_records(transitions, ["before", "after"]),
        "issue_codes": issues,
        "issue_messages": messages,
        "source_coverage": dict(coverage),
        "text_check_documents": count_records(text_outcomes, ["target", "outcome"]),
        "table_checks": dict(table_outcomes),
        "known_20_found": len(seen_known),
        "known_20_outcomes": dict(known_outcomes),
        "known_20_missing": sorted(set(KNOWN_FAILURES) - seen_known),
        "sampling": {
            "all_tables": args.all_tables,
            "documents_per_group_status_parser": args.sample_documents,
            "tables_per_selected_document": args.tables_per_document,
            "selection": (
                "first N in each group/status/parser stratum; "
                "all known20 and baseline failed docs; "
                "first/merged/empty/numeric/last tables"
            ),
            "selected_document_counts": count_records(samples, ["group", "status", "parser"]),
        },
        "limitations": [
            "MATCH is scoped evidence, not a document-level pass or lossless certificate.",
            "Text counts are by exact expression after whitespace/NFC normalization; "
            "location duplicates can hide omissions elsewhere.",
            "Only seven target families are checked. Other text, figures, formulas "
            "and footnotes are not certified.",
            "DART XML only for source comparisons. HTML/PDF are explicitly skipped; "
            "canonical grid checks still run.",
            "Table source comparisons are sampled unless --all-tables; canonical grid "
            "checks cover all emitted tables.",
            "Table references use independent strict XML parsing, sometimes after limited "
            "text escapes; no production parser is imported.",
            "Source tables are paired by independent lexical XPath; omitted tables remain "
            "in source_coverage, not silently excluded.",
            "If --skip-hashes was used, current hashes cannot prove the source is "
            "unchanged since parsing.",
            "Source excerpts are bounded; full original files and full JSONL "
            "are not included in the ZIP.",
        ],
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "CANONICAL AUDIT " + VERSION,
        "Completed: " + str(not errors),
        "",
        "This is a diagnostic report, NOT a whole-corpus PASS certificate.",
        "=== TOTALS ===",
        json.dumps(dict(totals)),
        json.dumps(dict(status)),
        "",
        "=== SOURCE COVERAGE ===",
        json.dumps(dict(coverage)),
        "",
        "=== PARTIAL ISSUE CODES (documents != occurrences) ===",
    ]
    for row in issues:
        if row["status"] == "partial":
            lines.append(
                f"{row['code']} / {row['severity']}: documents={row['documents']} "
                f"occurrences={row['occurrences']}"
            )
    lines.extend(["", "=== TOP PARTIAL MESSAGE GROUPS ==="])
    for row in [r for r in messages if r["status"] == "partial"][:30]:
        lines.append(
            f"[{row['documents']} docs / {row['occurrences']} occurrences] "
            f"{row['type']}: {row['message']}"
        )
    lines.extend(["", "=== TARGET TEXT CHECKS ==="])
    lines.extend(f"{key}: {value}" for key, value in text_outcomes.items())
    lines.extend(
        [
            "",
            "=== TABLE COMPARISON (SAMPLE UNLESS --all-tables) ===",
            json.dumps(dict(table_outcomes)),
            "",
            f"Known previous failures present: {len(seen_known)}/20",
            "Known20 target outcomes: " + json.dumps(dict(known_outcomes)),
            "Missing known receipts: " + str(summary["known_20_missing"]),
            "Baseline snapshot: " + str(summary["baseline_file"]),
            "Missing baseline document IDs: " + str(len(missing_baseline)),
            "",
            "=== LIMITATIONS ===",
            *summary["limitations"],
            "",
            "=== EXECUTION ERRORS ===",
            *errors,
        ]
    )
    (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    archive = output.parent / (run_name + ".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.iterdir()):
            if path.is_file():
                bundle.write(path, arcname=path.name)
    print("\n" + "\n".join(lines[:10]))
    print("\nREPORT:", output / "report.txt")
    print("UPLOAD THIS ZIP:", archive)
    print("ZIP size: %.2f MiB" % (archive.stat().st_size / 1024**2))
    if errors:
        print("INCOMPLETE AUDIT:", errors)
    return 2 if errors else 0


def self_test():
    import tempfile
    import unittest

    class AuditTests(unittest.TestCase):
        def test_entities_and_cdata(self):
            text, _, warnings = lexical_view(
                '<BODY><P a="R&amp;D">R&amp;D <![CDATA[M&A &amp;]]></P><!--S&P--></BODY>'
            )
            self.assertEqual(norm(text), "R&D M&A &amp;")
            self.assertFalse(warnings)

        def test_pseudo_tags(self):
            text, _, warnings = lexical_view(
                "<BODY><P><PUBG: BATTLEGROUNDS> <기간: 2023년> <자료: 회사> R&D</P></BODY>"
            )
            self.assertIn("<PUBG: BATTLEGROUNDS>", text)
            self.assertIn("<기간: 2023년>", text)
            self.assertFalse(warnings)

        def test_known_receipts(self):
            self.assertEqual(len(KNOWN_FAILURES), 20)
            self.assertEqual(
                Counter(KNOWN_FAILURES.values()), {"PUBG": 7, "PERIOD": 4, "SOURCE": 9}
            )

        def test_no_attribute_or_issue_false_positive(self):
            doc = {
                "blocks": [{"block_id": "b", "text_raw": "lost", "attributes_raw": {"a": "R&D"}}],
                "parse_issues": [{"message": "R&D"}],
            }
            checks, _, _ = text_comparisons("<BODY><P>R&amp;D</P></BODY>", doc)
            self.assertEqual(checks[0]["outcome"], "REVIEW")
            self.assertEqual(checks[0]["missing_occurrences"], 1)

        def test_duplicate_occurrences_and_variants(self):
            doc = {"blocks": [{"block_id": "b", "text_raw": "R&D <기간: 2024년>"}]}
            checks, _, _ = text_comparisons("<BODY><P>R&D R&D <기간: 2023년></P></BODY>", doc)
            found = {item["target"]: item for item in checks}
            self.assertEqual(found["R&D"]["missing_occurrences"], 1)
            self.assertEqual(found["PERIOD"]["missing_occurrences"], 1)
            self.assertEqual(found["PERIOD"]["extra_occurrences"], 1)

        def test_block_boundaries_not_joined_into_target(self):
            doc = {"blocks": [{"text_raw": "R"}, {"text_raw": "&D"}]}
            checks, _, _ = text_comparisons("<BODY><P>R&amp;D</P></BODY>", doc)
            self.assertEqual(checks[0]["outcome"], "REVIEW")

        def test_raw_text_match(self):
            doc = {"blocks": [{"text_raw": "<자료: 회사> R&D"}]}
            checks, _, _ = text_comparisons("<BODY><P>&lt;자료: 회사&gt; R&amp;D</P></BODY>", doc)
            self.assertTrue(all(item["outcome"] == "MATCH" for item in checks))

        def test_broken_document_never_clean_match(self):
            doc = {"blocks": [{"text_raw": "R&D"}]}
            checks, _, warnings = text_comparisons("<BODY><P>R&D</BODY>", doc)
            self.assertTrue(warnings)
            self.assertEqual(checks[0]["outcome"], "REVIEW")

        def test_table_range_handles_comments_cdata_and_quoted_gt(self):
            source = (
                "<BODY><!--<TABLE/>--><P><![CDATA[<TABLE/>]]></P>"
                '<TABLE x=">"><TR><TD>1</TD></TR></TABLE></BODY>'
            )
            _, tables, warnings = lexical_view(source)
            self.assertFalse(warnings)
            self.assertEqual(len(tables), 1)
            self.assertTrue(
                source[tables[0]["start"] : tables[0]["end"]].startswith('<TABLE x=">">')
            )

        def test_nested_tables_have_independent_owned_rows_and_text(self):
            source = "<TABLE><TR><TD><TABLE><TR><TD>3</TD></TR></TABLE></TD></TR></TABLE>"
            _, ranges, _ = lexical_view("<BODY>" + source + "</BODY>")
            self.assertEqual(len(ranges), 2)
            self.assertTrue(ranges[0]["nested"])
            outer, _ = independent_reference(source)
            self.assertEqual(outer["row_count"], 1)
            self.assertEqual(outer["cells"][0]["text_raw"], "")
            self.assertEqual(outer["cells"][0]["nested_table_count"], 1)
            inner, _ = independent_reference(source[ranges[1]["start"] - 6 : ranges[1]["end"] - 6])
            self.assertEqual(inner["cells"][0]["text_raw"], "3")

        def test_korean_pubg_and_bgmi_labels(self):
            value = "<PUBG: 배틀그라운드> <BGMI>"
            checks, _, warnings = text_comparisons(
                "<BODY><P>" + value + "</P></BODY>", {"blocks": [{"text_raw": value}]}
            )
            self.assertFalse(warnings)
            self.assertEqual(
                {c["target"]: c["outcome"] for c in checks}, {"PUBG": "MATCH", "BGMI": "MATCH"}
            )

        def test_registered_mark_and_ampersand_cell_reference(self):
            source = "<TABLE><TR><TD>Intel &reg; S펜 & Touchscreen</TD></TR></TABLE>"
            reference, _ = independent_reference(source)
            self.assertEqual(reference["cells"][0]["text_raw"], "Intel ® S펜 & Touchscreen")

        def test_independent_xpath_with_sibling_indices(self):
            text = (
                "<DOCUMENT><BODY><TABLE/><SECTION-1><TABLE/><TABLE/></SECTION-1>"
                "<SECTION-1><TABLE/></SECTION-1></BODY></DOCUMENT>"
            )
            _, ranges, warnings = lexical_view(text)
            self.assertFalse(warnings)
            self.assertEqual(
                [item["xpath"] for item in ranges],
                [
                    "/DOCUMENT/BODY/TABLE",
                    "/DOCUMENT/BODY/SECTION-1[1]/TABLE[1]",
                    "/DOCUMENT/BODY/SECTION-1[1]/TABLE[2]",
                    "/DOCUMENT/BODY/SECTION-1[2]/TABLE",
                ],
            )

        def test_table_omission_does_not_shift_pairing(self):
            text = (
                "<DOCUMENT><BODY><TABLE/><SECTION-1><TABLE/><TABLE/></SECTION-1></BODY></DOCUMENT>"
            )
            _, ranges, _ = lexical_view(text)
            blocks = [{"source_locator": {"xpath": item["xpath"]}} for item in ranges[1:]]
            pairs, omitted = pair_tables(ranges, blocks)
            self.assertEqual(len(pairs), 2)
            self.assertEqual(len(omitted), 1)
            self.assertEqual(pairs[0][0]["start"], ranges[1]["start"])
            self.assertEqual(pairs[0][1], "independent_lexical_xpath")

        def test_wrong_xpath_does_not_fallback_to_order(self):
            _, ranges, _ = lexical_view("<BODY><TABLE/></BODY>")
            pairs, omitted = pair_tables(ranges, [{"source_locator": {"xpath": "/wrong"}}])
            self.assertFalse(pairs)
            self.assertEqual(len(omitted), 1)

        def test_merged_empty_cells(self):
            source = (
                '<TABLE><TR><TH ROWSPAN="2">제목</TH><TD>100</TD><TD/></TR>'
                '<TR><TD COLSPAN="2">200</TD></TR></TABLE>'
            )
            table, mode = independent_reference(source)
            self.assertEqual(mode, "strict_original_fragment")
            self.assertEqual([table["row_count"], table["column_count"]], [2, 3])
            self.assertEqual(table["cells"][-1]["column_index"], 1)
            self.assertEqual(table["cells"][2]["text_raw"], "")
            self.assertEqual(compare_table(source, table)["outcome"], "MATCH_RAW")

        def test_table_value_and_span_change(self):
            source = '<TABLE><TR><TD COLSPAN="2">100</TD></TR></TABLE>'
            table, _ = independent_reference(source)
            table["cells"][0]["text_raw"] = "900"
            table["cells"][0]["column_span"] = 1
            result = compare_table(source, table)
            self.assertEqual(result["outcome"], "REVIEW")
            self.assertIn("display_text", result["differences_first_12"][0]["fields"])
            self.assertIn("column_span", result["differences_first_12"][0]["fields"])

        def test_table_limited_repair(self):
            source = "<TABLE><TR><TD>R&D <PUBG: BATTLEGROUNDS></TD></TR></TABLE>"
            table, mode = independent_reference(source)
            self.assertEqual(mode, "strict_after_limited_text_escapes")
            self.assertEqual(table["cells"][0]["text_raw"], "R&D <PUBG: BATTLEGROUNDS>")
            self.assertEqual(compare_table(source, table)["outcome"], "MATCH_RAW")

        def test_structural_damage_not_recovered(self):
            result = compare_table(
                "<TABLE><TR><TD>1</TR></TABLE>", {"row_count": 1, "column_count": 1, "cells": []}
            )
            self.assertEqual(result["outcome"], "REVIEW")
            self.assertEqual(result["reason"], "source_fragment_unverifiable")

        def test_internal_overlap_full_rectangle(self):
            cell = {"row_index": 0, "column_index": 0, "row_span": 2, "column_span": 2}
            table = {
                "row_count": 2,
                "column_count": 3,
                "cells": [cell, {**cell, "row_index": 1, "column_index": 1, "row_span": 1}],
            }
            self.assertIn("overlapping_cell_rectangles", grid_findings(table))
            table["cells"][0]["row_span"] = 1000000000
            self.assertIn("invalid_or_excessive_span", grid_findings(table))

        def test_nfc_source_resolution_and_traversal(self):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                raw = root / "raw"
                raw.mkdir()
                filename = unicodedata.normalize("NFD", "회사.xml")
                (raw / filename).write_text("x", encoding="utf-8")
                index = Sources(root)
                self.assertEqual(
                    index.resolve({"archive_path_normalized": "raw/회사.xml"}).name, filename
                )
                with self.assertRaises(ValueError):
                    index.resolve({"archive_path_raw": "../secret.xml"})
                with self.assertRaises(ValueError):
                    index.resolve({"archive_path_raw": "raw/missing.xml"})

        def test_utf16_and_cp949(self):
            self.assertEqual(decode_bytes("<P>한글</P>".encode("utf-16"))[0], "<P>한글</P>")
            self.assertEqual(decode_bytes("<P>한글</P>".encode("cp949"))[0], "<P>한글</P>")

        def test_deterministic_table_selection(self):
            blocks = [
                {"table": {"cells": [{"text_raw": str(i), "row_span": 1, "column_span": 1}]}}
                for i in range(20)
            ]
            blocks[3]["table"]["cells"][0]["row_span"] = 2
            blocks[6]["table"]["cells"][0]["text_raw"] = ""
            chosen = choose_tables(blocks, 6)
            self.assertTrue({0, 3, 6, 19} <= set(chosen))
            self.assertEqual(len(chosen), 6)

        def test_end_to_end_and_inputs_unchanged(self):
            import contextlib
            import copy
            import io

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "raw").mkdir()
                source_path = root / "raw" / "20240318000420.xml"
                fragment = "<TABLE><TR><TD>100</TD><TD/></TR></TABLE>"
                original = (
                    "<BODY><P><PUBG: BATTLEGROUNDS> R&D</P>" + fragment + "</BODY>"
                ).encode()
                source_path.write_bytes(original)
                table, _ = independent_reference(fragment)
                doc = {
                    "document_id": "d",
                    "document_role": "primary_report",
                    "primary_source_file_id": "s",
                    "parse_summary": {
                        "status": "partial",
                        "parser_name": "DartParser",
                        "parser_version": "2.1.0",
                    },
                    "parse_issues": [
                        {
                            "issue_code": "markup_recovery",
                            "severity": "error",
                            "message": "x",
                            "occurrence_count": 7,
                        },
                        {
                            "issue_code": "markup_recovery",
                            "severity": "error",
                            "message": "y",
                            "occurrence_count": 2,
                        },
                    ],
                    "blocks": [
                        {"block_id": "b1", "text_raw": "<PUBG: BATTLEGROUNDS> R&D"},
                        {"block_id": "b2", "table": {**table, "table_id": "t"}},
                    ],
                }
                package = {
                    "filing_id": "f",
                    "schema_version": "2.1.0",
                    "company": {"corp_name": "크래프톤"},
                    "filing": {
                        "receipt_number": "20240318000420",
                        "document_group": "periodic",
                        "report_name_raw": "테스트",
                    },
                    "source_files": [
                        {
                            "source_file_id": "s",
                            "archive_path_normalized": "raw/20240318000420.xml",
                            "detected_content_format": "dart_xml",
                            "size_bytes": len(original),
                        }
                    ],
                    "documents": [doc],
                }
                new = root / "canonical-repaired-smoke.jsonl"
                new.write_text(json.dumps(package) + "\n", encoding="utf-8")
                old = root / "canonical-smoke.jsonl"
                previous = copy.deepcopy(package)
                previous["documents"][0]["parse_summary"]["status"] = "failed"
                old.write_text(json.dumps(previous) + "\n", encoding="utf-8")
                args = argparse.Namespace(
                    input=new,
                    data_root=root,
                    before=None,
                    output_root=root / "quality",
                    sample_documents=3,
                    tables_per_document=6,
                    all_tables=False,
                )
                hashes = [
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (new, old, source_path)
                ]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(run_audit(args), 0)
                    self.assertEqual(run_audit(args), 0)
                self.assertEqual(
                    hashes,
                    [
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (new, old, source_path)
                    ],
                )
                archives = list((root / "quality").glob("*.zip"))
                self.assertEqual(len(archives), 2)
                with zipfile.ZipFile(archives[0]) as bundle:
                    summary = json.loads(bundle.read("summary.json"))
                    self.assertTrue(summary["completed"])
                    self.assertEqual(summary["issue_codes"][0]["documents"], 1)
                    self.assertEqual(summary["issue_codes"][0]["occurrences"], 9)
                    self.assertEqual(
                        summary["status_transitions"][0],
                        {"before": "failed", "after": "partial", "count": 1},
                    )
                    self.assertEqual(summary["known_20_found"], 1)
                    self.assertEqual(summary["table_checks"]["MATCH_RAW"], 1)
                    self.assertNotIn("20240318000420.xml", bundle.namelist())
                    records = [
                        json.loads(line)
                        for line in bundle.read("known_20_checks.jsonl").splitlines()
                    ]
                    self.assertEqual(len(records), 20)
                    self.assertEqual(records[0]["outcome"], "MATCH")

                # Metadata hash mismatch is never a successful source comparison.
                package["source_files"][0]["sha256"] = "0" * 64
                new.write_text(json.dumps(package) + "\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(run_audit(args), 0)
                latest = max((root / "quality").glob("*.zip"), key=lambda p: p.stat().st_mtime_ns)
                with zipfile.ZipFile(latest) as bundle:
                    summary = json.loads(bundle.read("summary.json"))
                    self.assertEqual(summary["known_20_outcomes"]["REVIEW"], 1)
                    self.assertEqual(summary["table_checks"]["UNVERIFIED_PAIRING"], 1)
                # Missing original remains UNVERIFIED, not a zero/zero MATCH.
                package["source_files"][0]["archive_path_normalized"] = "raw/missing.xml"
                new.write_text(json.dumps(package) + "\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(run_audit(args), 0)
                latest = max((root / "quality").glob("*.zip"), key=lambda p: p.stat().st_mtime_ns)
                with zipfile.ZipFile(latest) as bundle:
                    summary = json.loads(bundle.read("summary.json"))
                    self.assertEqual(summary["source_coverage"]["source_unverified"], 1)
                    self.assertEqual(
                        summary["known_20_outcomes"][
                            "UNVERIFIED_TARGET_ABSENT_OR_SOURCE_UNAVAILABLE"
                        ],
                        1,
                    )

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AuditTests)
    )
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/processed/canonical-v22-smoke.jsonl")
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--before",
        type=Path,
        help="Optional previous JSONL; auto-detects sibling canonical-smoke.jsonl",
    )
    parser.add_argument("--output-root", type=Path, default=Path("data/quality"))
    parser.add_argument("--sample-documents", type=int, default=3)
    parser.add_argument(
        "--tables-per-document",
        type=int,
        default=6,
        help="0 means all tables in selected documents",
    )
    parser.add_argument(
        "--all-tables", action="store_true", help="Compare all DART source tables (may take longer)"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.sample_documents < 0 or args.tables_per_document < 0:
        parser.error("Sampling parameters must be nonnegative")
    try:
        return run_audit(args)
    except (OSError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
