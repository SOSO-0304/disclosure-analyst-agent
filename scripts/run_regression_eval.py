#!/usr/bin/env python3
"""Run batch regression cases through the unified disclosure answer service."""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path

from disclosure_agent.config import get_settings
from disclosure_agent.evaluation.regression import (
    RegressionEvaluation,
    evaluate_answer,
    evaluation_error,
    load_regression_cases,
)
from disclosure_agent.services.answer_service import AnswerService
from disclosure_agent.storage.database import get_engine, session_scope


def _api_key() -> str | None:
    settings = get_settings()
    return settings.clova_studio_api_key or settings.hcx_api_key


def _write_results(
    evaluations: list[RegressionEvaluation],
    *,
    jsonl_path: Path,
    csv_path: Path,
) -> None:
    records = [evaluation.to_record() for evaluation in evaluations]
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with jsonl_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    fieldnames = list(records[0]) if records else []
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _print_summary(evaluations: list[RegressionEvaluation]) -> None:
    verdicts = Counter(evaluation.verdict for evaluation in evaluations)
    failures = Counter(
        failure_type
        for evaluation in evaluations
        for failure_type in evaluation.failure_types
    )
    print()
    print("=== REGRESSION SUMMARY ===")
    print(f"total                           {len(evaluations)}")
    for verdict in ("PASS", "MANUAL_REVIEW", "FAIL", "ERROR"):
        print(f"{verdict.lower():<32}{verdicts.get(verdict, 0)}")
    if failures:
        print("failure_types                   " + ", ".join(
            f"{name}={count}" for name, count in sorted(failures.items())
        ))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--cases", default="evals/regression_cases.jsonl")
    parser.add_argument("--tier", action="append", help="Run only matching tier; repeatable")
    parser.add_argument("--case-id", action="append", help="Run only matching case id; repeatable")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output-jsonl", default="data/quality/answer-regression.jsonl")
    parser.add_argument("--output-csv", default="data/quality/answer-regression.csv")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1200)
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    cases = list(load_regression_cases(args.cases))
    if args.tier:
        selected_tiers = set(args.tier)
        cases = [case for case in cases if case.tier in selected_tiers]
    if args.case_id:
        selected_ids = set(args.case_id)
        cases = [case for case in cases if case.case_id in selected_ids]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("no regression cases selected")

    evaluations: list[RegressionEvaluation] = []
    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        service = AnswerService(session, api_key=_api_key())
        for index, case in enumerate(cases, 1):
            started = time.perf_counter()
            try:
                result = service.answer(
                    case.query,
                    top_k=args.top_k,
                    candidate_k=args.candidate_k,
                    max_total_chars=args.max_total_chars,
                    max_completion_tokens=args.max_completion_tokens,
                )
                duration_ms = round((time.perf_counter() - started) * 1000)
                evaluation = evaluate_answer(case, result, duration_ms=duration_ms)
            except Exception as error:  # noqa: BLE001 - batch must record and continue
                duration_ms = round((time.perf_counter() - started) * 1000)
                evaluation = evaluation_error(case, error, duration_ms=duration_ms)

            evaluations.append(evaluation)
            print(
                f"[{index:02d}/{len(cases):02d}] {evaluation.verdict:<13} "
                f"{case.case_id:<16} mode={evaluation.mode or '-':<28} "
                f"status={evaluation.status or '-'}"
            )
            if evaluation.verdict in {"FAIL", "ERROR"}:
                reason = evaluation.to_record()["failure_reason"]
                print(f"    reason: {reason}")

    jsonl_path = Path(args.output_jsonl)
    csv_path = Path(args.output_csv)
    _write_results(evaluations, jsonl_path=jsonl_path, csv_path=csv_path)
    _print_summary(evaluations)
    print(f"jsonl                           {jsonl_path}")
    print(f"csv                             {csv_path}")

    if any(evaluation.verdict in {"FAIL", "ERROR"} for evaluation in evaluations):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
