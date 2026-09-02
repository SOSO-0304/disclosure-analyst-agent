# Answer regression evaluation

통합 `AnswerService` 평가는 두 파일로 나눕니다.

- `evals/regression_cases.jsonl`: 이미 E2E로 검증한 baseline 회귀평가 14문항
- `evals/extended_cases.jsonl`: 새로운 연산·반례·안전성·서술형 취약점을 찾는 확장평가 30문항

전체 평가 풀은 44문항입니다.

## Tier

Baseline 파일:

- `smoke`: 이미 수작업 E2E로 검증한 golden 질의. 기존 기능 회귀를 빠르게 확인합니다.
- `challenge`: 계산 확장, 정보 부족 처리, 분기보고서 서술형 등 baseline에서 추가 검증한 질의입니다.

Extended 파일:

- `extended`: baseline에 직접 과적합되지 않았는지 확인하기 위한 30개 추가 질의입니다. 매출 계산, 설비투자 의미 범위, 자금조달 absence 처리, 계약 lifecycle/정정, 보고서 scope, false premise, missing target을 포함합니다.

서술형은 표현이 조금 달라도 정답일 수 있으므로 `manual_review=true`를 사용할 수 있습니다.
이 경우 기계 판정이 모두 통과하면 `MANUAL_REVIEW`로 기록하고, `FAIL`과 구분합니다.

## Baseline 실행

```bash
python scripts/run_regression_eval.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure
```

현재 baseline 목표는 `FAIL=0`, `ERROR=0`입니다. deterministic/정보부족 항목은 자동 PASS로 두고, 의미 평가가 필요한 투자계획·다중연도 사업변화 서술형만 manual review를 유지합니다.

## Extended 실행

처음에는 baseline과 분리해서 실행합니다.

```bash
python scripts/run_regression_eval.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --cases evals/extended_cases.jsonl
```

Extended에서 FAIL이 나오더라도 한 문항씩 즉시 prompt를 고치지 않습니다. 전체 30문항을 끝까지 실행해 실패 유형을 모은 뒤 반복되는 패턴을 수정합니다.

특정 case만 실행할 수 있습니다.

```bash
python scripts/run_regression_eval.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --cases evals/extended_cases.jsonl \
  --case-id FAC-X03 \
  --case-id FUND-X04
```

기본 결과 파일은 Git에 포함하지 않는 `data/quality/` 아래에 생성됩니다.

- `data/quality/answer-regression.jsonl`
- `data/quality/answer-regression.csv`

Baseline 결과를 보존한 채 extended를 실행하려면 출력 경로를 별도로 지정합니다.

```bash
python scripts/run_regression_eval.py \
  --database-url postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
  --cases evals/extended_cases.jsonl \
  --output-jsonl data/quality/answer-regression-extended.jsonl \
  --output-csv data/quality/answer-regression-extended.csv
```

한 case에서 예외가 발생해도 나머지 case는 계속 실행하며 해당 행을 `ERROR`로 기록합니다.
`FAIL` 또는 `ERROR`가 하나라도 있으면 runner는 exit code 1로 종료합니다.

## 자동 판정 필드

- `expected_mode`: 최상위 answer execution mode
- `expected_status` / `expected_statuses`: 허용되는 분석 상태
- `expected_rails`: lower-level retrieval rail 순서
- `min_evidence`: 최소 Evidence 수
- `must_include`: 답변에 반드시 포함할 안정적인 문자열
- `must_include_any`: 각 그룹에서 하나 이상 포함하면 통과
- `must_not_include`: 환각/잘못된 상태 표현 등 금지 문자열
- `evidence_report_contains`: 모든 Evidence의 보고서 scope 검사
- `min_evidence_per_year`: 다중연도 비교의 Evidence 균형 검사
- `require_citations`: `[E#]`가 존재하고 Evidence 범위를 벗어나지 않는지 검사
- `manual_review`: 자동 hard check 통과 후 사람 검토가 필요한 서술형

## 확장평가 범주

- exact extraction / revenue
- difference / growth / average / ranking
- facility investment aggregation and semantic scope
- fundraising CB/BW/EB/유상증자 aggregation and absence handling
- supply-contract formation → correction → termination lifecycle
- correction-grounding and false-premise rejection
- annual/quarterly report scope
- narrative investment and business-strategy comparison
- explicit user exclusion requirements
- nonexistent company/year, missing company/year, unsupported multi-company narrative

## 실패 유형

runner는 실패 원인을 다음 범주로 모읍니다.

- `routing`: mode/rail 선택 오류
- `status`: ANSWERABLE/PARTIAL/UNRESOLVED 등 상태 오류
- `retrieval`: Evidence 수, 보고서 scope, 연도 균형 오류
- `answer_correctness`: golden 값 또는 필수 내용 누락
- `grounding`: 금지 표현 또는 citation 오류
- `execution`: 예외 발생

평가셋 한 문항이 실패했다고 즉시 prompt를 수정하지 않습니다. 같은 실패 유형이 여러 문항에서 반복되는지 먼저 확인한 뒤 routing, retrieval, extraction, calculation, grounding, generation 중 적절한 계층을 수정합니다.
