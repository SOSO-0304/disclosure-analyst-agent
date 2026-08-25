"""Batch conversion from manifest/raw corpus to canonical JSONL."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from disclosure_agent.parsing.document_parser import DocumentParser
from disclosure_agent.storage.jsonl import append_canonical

SUPPORTED_SOURCE_SUFFIXES = {".xml", ".html", ".htm", ".pdf"}


def _build_source_index(corpus_root: Path) -> dict[str, list[Path]]:
    """Scan supported source files once and index them by receipt number."""
    index: dict[str, list[Path]] = defaultdict(list)
    raw_root = corpus_root / "raw"
    if not raw_root.exists():
        return index

    for path in raw_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            continue
        # Examples:
        #   20240514001522.xml
        #   20240514001522_viewer.html
        #   20240514001522.pdf
        receipt = path.name.split("_", 1)[0].split(".", 1)[0]
        if receipt.isdigit():
            index[receipt].append(path)

    def priority(path: Path) -> tuple[int, str]:
        suffix = path.suffix.lower()
        if suffix == ".xml":
            rank = 0
        elif suffix in {".html", ".htm"} and "viewer" in path.stem.lower():
            rank = 1
        elif suffix in {".html", ".htm"}:
            rank = 2
        else:  # PDF is preserved as provenance/fallback source.
            rank = 3
        return rank, str(path)

    for paths in index.values():
        paths.sort(key=priority)
    return index


def _resolve_files(corpus_root: Path, row: dict, source_index: dict[str, list[Path]]) -> list[Path]:
    raw_path = corpus_root / row["file_path"]
    if raw_path.is_file():
        return [raw_path]
    if raw_path.is_dir():
        files = sorted(
            (p for p in raw_path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES),
            key=str,
        )
        if files:
            return files
    receipt = str(row["rcept_no"])
    return source_index.get(receipt, [])


def parse_corpus(corpus_root: str | Path, output_path: str | Path) -> Counter:
    root = Path(corpus_root)
    manifest_path = root / "manifest.jsonl"
    output = Path(output_path)
    if output.exists():
        output.unlink()

    with manifest_path.open(encoding="utf-8") as fp:
        rows = [json.loads(line) for line in fp if line.strip()]

    print("Indexing source files once...")
    source_index = _build_source_index(root)
    indexed_files = sum(len(v) for v in source_index.values())
    print(f"Indexed {indexed_files} source files for {len(source_index)} receipt numbers.")

    parser = DocumentParser()
    stats: Counter = Counter()
    failures: list[dict[str, str]] = []
    total = len(rows)

    for number, row in enumerate(rows, 1):
        try:
            files = _resolve_files(root, row, source_index)
            if not files:
                raise FileNotFoundError(row["file_path"])
            document = parser.parse(files, manifest=row)
            append_canonical(output, document)
            stats["parsed"] += 1
            stats[f"parsed:{row['doc_group']}"] += 1
        except Exception as exc:
            stats["failed"] += 1
            stats[f"failed:{row.get('doc_group', 'unknown')}"] += 1
            failures.append({
                "doc_id": str(row.get("doc_id", "")),
                "doc_group": str(row.get("doc_group", "")),
                "file_path": str(row.get("file_path", "")),
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            print(f"FAILED {row.get('doc_id')}: {type(exc).__name__}: {exc}")

        if number % 100 == 0 or number == total:
            print(f"[{number}/{total}] parsed={stats['parsed']} failed={stats['failed']}")

    failure_path = output.with_name(f"{output.stem}.failures.jsonl")
    if failures:
        with failure_path.open("w", encoding="utf-8") as fp:
            for failure in failures:
                fp.write(json.dumps(failure, ensure_ascii=False) + "\n")
    elif failure_path.exists():
        failure_path.unlink()

    return stats


def main() -> None:
    import argparse
    argp = argparse.ArgumentParser()
    argp.add_argument("corpus_root", type=Path)
    argp.add_argument("--output", type=Path, default=Path("data/processed/canonical.jsonl"))
    args = argp.parse_args()
    stats = parse_corpus(args.corpus_root, args.output)
    print("\n=== canonical parsing ===")
    for key in sorted(stats):
        print(f"{key:24} {stats[key]}")


if __name__ == "__main__":
    main()
