"""Reparse only v2.2 partial DART packages into the v2.2.1 overlay."""

from __future__ import annotations

import argparse
from pathlib import Path

from disclosure_agent.parsing.dart_overlay import reparse_dart_overlay

DEFAULT_BASE = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_OUTPUT = Path("data/processed/canonical-dart-221-overlay.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--expected-packages",
        type=int,
        help="Fail before reparsing if the immutable base snapshot has a different candidate count.",
    )
    parser.add_argument(
        "--with-hashes",
        action="store_true",
        help="Recompute source SHA-256 values in overlay source metadata.",
    )
    args = parser.parse_args()

    stats = reparse_dart_overlay(
        corpus_root=args.data_root,
        base_path=args.base,
        output_path=args.output,
        compute_hashes=args.with_hashes,
        expected_packages=args.expected_packages,
    )

    print("\n=== DART 2.2.1 overlay reparse ===")
    for key in sorted(stats):
        print(f"{key:<36} {stats[key]}")
    print(f"OUTPUT                               {args.output}")


if __name__ == "__main__":
    main()
