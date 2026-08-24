"""Build an inventory of source disclosures."""

from pathlib import Path


def discover_files(root: Path) -> list[Path]:
    """Return files below a raw-data directory in stable order."""
    return sorted(path for path in root.rglob("*") if path.is_file())
