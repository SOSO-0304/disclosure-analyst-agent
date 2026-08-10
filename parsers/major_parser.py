# parsers/major_parser.py

from pathlib import Path

from common import (
    parse_xml_to_markdown,
    save_markdown,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "major"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "parsed"
    / "major"
)


def parse_major():

    success_count = 0
    error_count = 0

    errors = []

    company_dirs = sorted(
        path
        for path in INPUT_DIR.iterdir()
        if path.is_dir()
    )

    for company_dir in company_dirs:

        company = company_dir.name

        print(f"\n[기업] {company}")

        disclosure_dirs = sorted(
            path
            for path in company_dir.iterdir()
            if path.is_dir()
        )

        for disclosure_dir in disclosure_dirs:

            document_id = disclosure_dir.name

            xml_files = sorted(
                disclosure_dir.glob("*.xml")
            )

            if not xml_files:
                continue

            try:

                markdown_parts = []

                for xml_path in xml_files:

                    markdown = (
                        parse_xml_to_markdown(
                            xml_path=xml_path,
                            company=company,
                            doc_group="major",
                            document_id=document_id,
                        )
                    )

                    markdown_parts.append(
                        markdown
                    )

                final_markdown = (
                    "\n\n---\n\n".join(
                        markdown_parts
                    )
                )

                output_path = (
                    OUTPUT_DIR
                    / company
                    / f"{document_id}.md"
                )

                save_markdown(
                    final_markdown,
                    output_path,
                )

                success_count += 1

            except Exception as e:

                error_count += 1

                errors.append({
                    "company": company,
                    "document_id": document_id,
                    "error": str(e),
                })

                print(
                    f"[ERROR] {company} / "
                    f"{document_id}: {e}"
                )

    print("\n============================")
    print("Major Parsing 완료")
    print("============================")
    print(f"성공: {success_count}")
    print(f"실패: {error_count}")

    save_errors(errors)


def save_errors(errors):

    if not errors:
        return

    error_path = (
        OUTPUT_DIR
        / "_parsing_errors.txt"
    )

    error_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        error_path,
        "w",
        encoding="utf-8",
    ) as f:

        for error in errors:

            f.write(
                f"{error['company']} | "
                f"{error['document_id']} | "
                f"{error['error']}\n"
            )


if __name__ == "__main__":
    parse_major()