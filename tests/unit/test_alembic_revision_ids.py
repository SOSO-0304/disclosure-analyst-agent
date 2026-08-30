from __future__ import annotations

import ast
from pathlib import Path

ALEMBIC_VERSION_NUM_MAX_LENGTH = 32


def test_alembic_revision_ids_fit_version_table() -> None:
    versions_dir = Path(__file__).parents[2] / "alembic" / "versions"
    revision_ids: list[tuple[Path, str]] = []

    for migration_path in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(migration_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != "revision":
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                raise AssertionError(f"{migration_path}: revision must be a string literal")
            revision_ids.append((migration_path, node.value.value))
            break
        else:
            raise AssertionError(f"{migration_path}: missing revision assignment")

    assert revision_ids
    too_long = [
        (path.name, revision)
        for path, revision in revision_ids
        if len(revision) > ALEMBIC_VERSION_NUM_MAX_LENGTH
    ]
    assert not too_long, (
        f"Alembic revision IDs must fit alembic_version.version_num VARCHAR(32): {too_long}"
    )
