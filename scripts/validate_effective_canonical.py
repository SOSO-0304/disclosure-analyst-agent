#!/usr/bin/env python3
"""Validate an accepted canonical snapshot or a legacy base + overlay view."""

from __future__ import annotations

import argparse
from pathlib import Path

from disclosure_agent.storage.effective_canonical import (
    EffectiveCanonicalExpectations,
    EffectiveCanonicalReader,
)

DEFAULT_BASE = Path("data/processed/canonical-v22-smoke.jsonl")
DEFAULT_OVERLAY = Path("data/processed/canonical-dart-221-overlay.jsonl")
DEFAULT_OVERLAY_MANIFEST = Path("data/processed/effective-canonical.manifest.json")
DEFAULT_SNAPSHOT_MANIFEST = Path("data/processed/canonical-v221-final.manifest.json")

OVERLAY_EXPECTATIONS = EffectiveCanonicalExpectations(
    base_packages=4204,
    overlay_packages=77,
    effective_packages=4204,
    effective_documents=4619,
    effective_success=4602,
    effective_partial=17,
    effective_failed=0,
    effective_tables=1580832,
    replaced_packages=77,
)

SNAPSHOT_EXPECTATIONS = EffectiveCanonicalExpectations(
    base_packages=4204,
    overlay_packages=0,
    effective_packages=4204,
    effective_documents=4619,
    effective_success=4513,
    effective_partial=106,
    effective_failed=0,
    effective_tables=1580832,
    replaced_packages=0,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        help="Accepted standalone canonical JSONL or JSONL.GZ snapshot.",
    )
    parser.add_argument("--base", type=Path)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--no-strict-corpus-counts",
        action="store_true",
        help="Validate merge invariants without the accepted 4,204-package corpus counts.",
    )
    args = parser.parse_args()
    if args.input is not None and (args.base is not None or args.overlay is not None):
        parser.error("--input cannot be combined with --base or --overlay")

    if args.input is not None:
        base_path = args.input
        overlay_path = None
        manifest_path = args.manifest or DEFAULT_SNAPSHOT_MANIFEST
        strict_expectations = SNAPSHOT_EXPECTATIONS
    else:
        base_path = args.base or DEFAULT_BASE
        overlay_path = args.overlay or DEFAULT_OVERLAY
        manifest_path = args.manifest or DEFAULT_OVERLAY_MANIFEST
        strict_expectations = OVERLAY_EXPECTATIONS

    reader = EffectiveCanonicalReader(
        base_path,
        overlay_path,
        expectations=(
            EffectiveCanonicalExpectations()
            if args.no_strict_corpus_counts
            else strict_expectations
        ),
    )

    for _ in reader:
        pass

    manifest = reader.manifest
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    temporary.write_bytes(manifest.to_json_bytes())
    temporary.replace(manifest_path)

    print("=== effective canonical validation ===")
    print(f"base packages                   {manifest.base_packages}")
    print(f"overlay packages                {manifest.overlay_packages}")
    print(f"overlay IDs in base             {manifest.overlay_ids_in_base}")
    print(f"effective packages              {manifest.effective_packages}")
    print(f"effective documents             {manifest.effective_documents}")
    print(f"effective success               {manifest.effective_success}")
    print(f"effective partial               {manifest.effective_partial}")
    print(f"effective failed                {manifest.effective_failed}")
    print(f"effective tables                {manifest.effective_tables}")
    print(f"replaced packages               {manifest.replaced_packages}")
    print(f"replacement identity checks     {manifest.replacement_identity_checks}")
    print(f"duplicate filing IDs            {manifest.duplicate_filing_ids}")
    print(f"base sha256                     {manifest.base_sha256}")
    print(f"overlay sha256                  {manifest.overlay_sha256}")
    print(f"manifest sha256                 {manifest.sha256}")
    print(f"MANIFEST                        {manifest_path}")


if __name__ == "__main__":
    main()
