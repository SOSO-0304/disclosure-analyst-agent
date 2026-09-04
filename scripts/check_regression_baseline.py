#!/usr/bin/env python3
"""Check a regression JSONL result against an approved non-regression baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("baseline must be a JSON object")
    return payload


def _load_results(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: each row must be an object")
        case_id = str(row.get("case_id", "")).strip()
        if not case_id:
            raise ValueError(f"{path}:{line_number}: missing case_id")
        if case_id in rows:
            raise ValueError(f"{path}:{line_number}: duplicate case_id {case_id}")
        rows[case_id] = row
    return rows


def check_regression_baseline(
    baseline: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    automated = tuple(str(value) for value in baseline.get("automated_pass_ids", []))
    manual = tuple(str(value) for value in baseline.get("manual_review_ids", []))
    expected = set(automated) | set(manual)

    errors: list[str] = []
    if set(automated) & set(manual):
        errors.append("baseline contains case ids in both automated and manual groups")

    declared_total = int(baseline.get("total_cases", len(expected)))
    if declared_total != len(expected):
        errors.append(
            f"baseline total_cases={declared_total} but case id count={len(expected)}"
        )

    actual = set(results)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append("missing cases: " + ", ".join(missing))
    if extra:
        errors.append("unexpected cases: " + ", ".join(extra))

    for case_id in automated:
        row = results.get(case_id)
        if row is None:
            continue
        verdict = row.get("verdict")
        if verdict != "PASS":
            errors.append(f"{case_id}: baseline PASS regressed to {verdict}")

    for case_id in manual:
        row = results.get(case_id)
        if row is None:
            continue
        verdict = row.get("verdict")
        if verdict not in {"MANUAL_REVIEW", "PASS"}:
            errors.append(f"{case_id}: manual-review case regressed to {verdict}")

    for case_id, row in results.items():
        if row.get("verdict") in {"FAIL", "ERROR"}:
            errors.append(f"{case_id}: verdict={row.get('verdict')}")
        if row.get("hard_passed") is False:
            errors.append(f"{case_id}: hard_passed=false")

    return tuple(dict.fromkeys(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="evals/baselines/extended-v18.json")
    parser.add_argument("--results", required=True)
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    results_path = Path(args.results)
    baseline = _load_json(baseline_path)
    results = _load_results(results_path)
    errors = check_regression_baseline(baseline, results)

    print("=== REGRESSION BASELINE CHECK ===")
    print(f"baseline                        {baseline.get('name', baseline_path.name)}")
    print(f"expected_cases                  {baseline.get('total_cases', '-')}")
    print(f"actual_cases                    {len(results)}")
    print(f"automated_pass_baseline         {len(baseline.get('automated_pass_ids', []))}")
    print(f"manual_review_baseline          {len(baseline.get('manual_review_ids', []))}")

    if errors:
        print("status                          FAIL")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("status                          PASS")
    manual_count = sum(
        1 for row in results.values() if row.get("verdict") == "MANUAL_REVIEW"
    )
    if manual_count:
        print(
            f"manual_review_required          {manual_count} "
            "(자동 hard check 통과와 별개로 답변 본문 검토 필요)"
        )


if __name__ == "__main__":
    main()
