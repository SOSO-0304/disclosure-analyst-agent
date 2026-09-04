# Answer Regression Quality Gate

## Baseline

The approved answer-regression baseline is `extended-v18`.

- cases: `evals/extended_cases.jsonl`
- total: 30
- automated PASS: 19
- MANUAL_REVIEW: 11
- FAIL: 0
- ERROR: 0
- baseline manifest: `evals/baselines/extended-v18.json`

The 11 manual-review cases were reviewed and accepted for v18. A future run must still review
their answer bodies because HCX narrative generation can vary even when deterministic hard checks pass.

## Pull request gate

GitHub Actions runs an external-dependency-free quality job:

```bash
pytest -q tests/unit
ruff check src tests/unit
ruff format --check src tests/unit
```

The full 30-case answer regression is intentionally not run on every PR because it depends on the
prepared PostgreSQL/pgvector dataset and CLOVA Studio APIs.

## Full regression

Run against the prepared local database:

```bash
python scripts/run_regression_eval.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --cases evals/extended_cases.jsonl \
  --output-jsonl data/quality/answer-regression-extended-current.jsonl \
  --output-csv data/quality/answer-regression-extended-current.csv
```

Then check for automated regressions against v18:

```bash
python scripts/check_regression_baseline.py \
  --baseline evals/baselines/extended-v18.json \
  --results data/quality/answer-regression-extended-current.jsonl
```

The checker fails when:

- a v18 automated PASS case is no longer PASS;
- any case becomes FAIL or ERROR;
- `hard_passed=false`;
- a baseline case is missing or an unexpected case is present.

A MANUAL_REVIEW case may stay MANUAL_REVIEW or improve to PASS. MANUAL_REVIEW is not treated as
semantic approval; review those answer bodies before accepting a release or changing the baseline.

## Updating the baseline

Do not update the baseline merely to make a failing run green. Update it only after:

1. the regression case set intentionally changes;
2. all hard checks pass;
3. every MANUAL_REVIEW answer is inspected;
4. the new baseline commit and case ids are recorded in a new baseline file.
