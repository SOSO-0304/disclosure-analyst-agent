# parsers/common.py

from pathlib import Path
from typing import Any
import html
import re

from lxml import etree


# 실제 공시 내용이 아닌 렌더링 요소
IGNORE_TAGS = {
    "STYLE",
    "SCRIPT",
    "NOSCRIPT",
}


# =========================================================
# 기본 Utility
# =========================================================

def normalize_tag(tag: Any) -> str:
    """
    XML namespace를 제거하고 태그명을 대문자로 통일한다.
    """

    if not isinstance(tag, str):
        return ""

    if "}" in tag:
        tag = tag.split("}", 1)[1]

    return tag.upper()


def clean_text(text: str | None) -> str:
    """
    XML에서 추출한 텍스트의 불필요한 공백을 정리한다.
    """

    if not text:
        return ""

    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_full_text(node) -> str:
    """
    현재 node 아래의 모든 텍스트를 가져온다.
    """

    parts = []

    for text in node.itertext():
        cleaned = clean_text(text)

        if cleaned:
            parts.append(cleaned)

    return " ".join(parts)


def escape_markdown_cell(text: str) -> str:
    """
    Markdown table cell에서 문제가 될 수 있는 문자를 처리한다.
    """

    text = clean_text(text)

    # Markdown column separator escape
    text = text.replace("|", r"\|")

    # line break 제거
    text = text.replace("\n", " ")

    return text


# =========================================================
# XML Loading
# =========================================================

def load_xml(xml_path: Path):
    """
    XML 파일을 읽어 root element를 반환한다.
    """

    parser = etree.XMLParser(
        recover=True,
        huge_tree=True,
        remove_comments=True,
    )

    tree = etree.parse(
        str(xml_path),
        parser,
    )

    root = tree.getroot()

    remove_noise_nodes(root)

    return root


def remove_noise_nodes(root) -> None:
    """
    style / script 등 실제 공시 내용이 아닌 요소 제거.
    """

    for node in list(root.iter()):

        tag = normalize_tag(node.tag)

        if tag not in IGNORE_TAGS:
            continue

        parent = node.getparent()

        if parent is not None:
            parent.remove(node)


# =========================================================
# Table Parser
# =========================================================

def get_table_rows(table_node) -> list:
    """
    현재 TABLE에 직접 속한 TR만 추출한다.

    중첩 TABLE 때문에 내부 TR이 바깥 TABLE에도
    중복으로 포함되는 것을 방지한다.
    """

    rows = []

    for node in table_node.iter():

        if normalize_tag(node.tag) != "TR":
            continue

        # 가장 가까운 TABLE 부모 확인
        parent = node.getparent()

        nearest_table = None

        while parent is not None:

            if normalize_tag(parent.tag) == "TABLE":
                nearest_table = parent
                break

            parent = parent.getparent()

        if nearest_table is table_node:
            rows.append(node)

    return rows


def get_row_cells(tr_node) -> list[str]:
    """
    TR의 직접 자식 요소에서 텍스트를 가져온다.

    TD / TH / TU / TE처럼 특정 태그에 한정하지 않는다.
    실제 값이 존재하는 자식이면 모두 가져온다.
    """

    cells = []

    for child in tr_node:

        if not isinstance(child.tag, str):
            continue

        tag = normalize_tag(child.tag)

        # nested table은 셀 본문에서 별도 처리
        if tag == "TABLE":
            continue

        text = get_full_text(child)

        if text:
            cells.append(text)

    return cells


def parse_table(table_node) -> list[list[str]]:
    """
    XML TABLE을 행렬 형태로 변환한다.
    """

    rows = []

    for tr in get_table_rows(table_node):

        cells = get_row_cells(tr)

        if cells:
            rows.append(cells)

    return rows


def table_to_markdown(
    rows: list[list[str]],
) -> str:
    """
    2차원 배열을 Markdown pipe table로 변환한다.

    예:
    | 구분 | 주식수 | 비율 |
    | --- | --- | --- |
    | 보고 전 | 100 | 5% |
    """

    if not rows:
        return ""

    max_columns = max(
        len(row)
        for row in rows
    )

    if max_columns == 0:
        return ""

    normalized_rows = []

    for row in rows:

        padded = (
            row
            + [""] * (max_columns - len(row))
        )

        normalized_rows.append([
            escape_markdown_cell(cell)
            for cell in padded
        ])

    # 첫 번째 행을 Markdown header로 사용
    header = normalized_rows[0]

    lines = []

    lines.append(
        "| " + " | ".join(header) + " |"
    )

    lines.append(
        "| "
        + " | ".join(
            ["---"] * max_columns
        )
        + " |"
    )

    for row in normalized_rows[1:]:

        lines.append(
            "| "
            + " | ".join(row)
            + " |"
        )

    return "\n".join(lines)


# =========================================================
# Markdown Renderer
# =========================================================

def has_table_ancestor(node) -> bool:
    """
    현재 node가 TABLE 내부에 있는지 확인.
    """

    parent = node.getparent()

    while parent is not None:

        if normalize_tag(parent.tag) == "TABLE":
            return True

        parent = parent.getparent()

    return False


def has_title_ancestor(node) -> bool:

    parent = node.getparent()

    while parent is not None:

        if normalize_tag(parent.tag) in {
            "TITLE",
            "SECTION-TITLE",
        }:
            return True

        parent = parent.getparent()

    return False


def render_node(
    node,
    lines: list[str],
    depth: int = 0,
) -> None:
    """
    XML node를 Markdown으로 렌더링한다.
    """

    if not isinstance(node.tag, str):
        return

    tag = normalize_tag(node.tag)

    if tag in IGNORE_TAGS:
        return

    # -------------------------------------------
    # 제목
    # -------------------------------------------

    if tag in {
        "TITLE",
        "SECTION-TITLE",
    }:

        text = get_full_text(node)

        if text:

            # 문서 구조가 너무 깊어져도
            # Markdown heading은 최대 6단계
            heading = min(
                max(depth + 2, 2),
                6,
            )

            lines.append(
                f"{'#' * heading} {text}"
            )

            lines.append("")

        return

    # -------------------------------------------
    # TABLE
    # -------------------------------------------

    if tag == "TABLE":

        rows = parse_table(node)

        markdown_table = (
            table_to_markdown(rows)
        )

        if markdown_table:

            lines.append(markdown_table)
            lines.append("")

        # 중첩 table이 있다면 추가 렌더링
        for child in node.iter():

            if child is node:
                continue

            if normalize_tag(child.tag) != "TABLE":
                continue

            # 바로 위 table이 현재 node인 경우에만
            parent = child.getparent()

            while (
                parent is not None
                and normalize_tag(parent.tag) != "TABLE"
            ):
                parent = parent.getparent()

            if parent is node:
                render_node(
                    child,
                    lines,
                    depth=depth + 1,
                )

        return

    # -------------------------------------------
    # Paragraph
    # -------------------------------------------

    if tag in {
        "P",
        "PARAGRAPH",
    }:

        # TABLE을 포함한 P라면 전체 itertext를 쓰면
        # 표 내용이 다시 본문으로 중복될 수 있다.
        contains_table = any(
            normalize_tag(child.tag) == "TABLE"
            for child in node.iter()
            if child is not node
            and isinstance(child.tag, str)
        )

        if not contains_table:

            text = get_full_text(node)

            if text:
                lines.append(text)
                lines.append("")

            return

    # -------------------------------------------
    # 일반 노드
    # -------------------------------------------

    children = [
        child
        for child in node
        if isinstance(child.tag, str)
    ]

    if not children:

        # 이미 TABLE 안에서 처리되는 cell은
        # 여기에서 다시 출력하지 않는다.
        if has_table_ancestor(node):
            return

        if has_title_ancestor(node):
            return

        text = clean_text(node.text)

        if text:
            lines.append(text)
            lines.append("")

        return

    # -------------------------------------------
    # Container
    # -------------------------------------------

    for child in children:

        render_node(
            child,
            lines,
            depth=depth + 1,
        )


def parse_xml_to_markdown(
    xml_path: Path,
    company: str,
    doc_group: str,
    document_id: str,
) -> str:
    """
    XML 하나를 Markdown 문자열로 변환한다.
    """

    root = load_xml(xml_path)

    lines = [
        f"# {company}",
        "",
        f"- 문서 ID: `{document_id}`",
        f"- 문서 유형: `{doc_group}`",
        f"- 원본 파일: `{xml_path.name}`",
        "",
        "---",
        "",
    ]

    render_node(
        root,
        lines,
        depth=0,
    )

    markdown = "\n".join(lines)

    # 빈 줄 정리
    markdown = re.sub(
        r"\n{3,}",
        "\n\n",
        markdown,
    )

    return markdown.strip() + "\n"


# =========================================================
# File Save
# =========================================================

def save_markdown(
    markdown: str,
    output_path: Path,
) -> None:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        markdown,
        encoding="utf-8",
    )