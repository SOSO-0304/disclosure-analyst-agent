# Disclosure Analyst Agent

기업공시 데이터를 기반으로 사용자의 자연어 질의를 분석하고,  
정형 데이터 조회·계산과 의미 기반 검색, HyperCLOVA X를 결합하여  
근거가 포함된 답변을 생성하는 **공시 분석 Agent**입니다.

정확한 수치 계산은 PostgreSQL의 구조화 데이터를 사용하고,  
사업 전략·투자 방향·기업 비교와 같은 서술형 질의는 공시 문서 검색과  
HyperCLOVA X 기반 grounded generation으로 처리합니다.

모든 최종 답변에는 사용한 **근거 공시**를 표시하며,  
제공된 데이터로 확인할 수 없는 내용은 임의로 생성하지 않는 것을 기본 원칙으로 합니다.

---

## 주요 기능

- 기업명, 연도, 질의 의도 자동 분석
- 연결 매출액 등 정형 재무 수치 조회
- 기업 간 차이·평균·순위 등 deterministic 계산
- 신규시설투자 공시 분석
- CB/BW/EB 등 자금조달 이벤트 분석
- 공급계약 최초 공시·정정·해지 lifecycle 분석
- 사업보고서/분기보고서 기반 의미 검색
- 복수 기업 및 복수 연도 비교
- HyperCLOVA X 기반 근거 답변 생성
- Grounding Validator를 통한 근거 정합성 검증
- 정보가 부족한 경우 `NO_MATCH` / `UNRESOLVED` / `PARTIAL` 처리
- 모든 최종 답변에 근거 공시 표시

---

## 시스템 구성

```text
                     User Question
                           |
                           v
                    Query Planner
                           |
            +--------------+--------------+
            |                             |
            v                             v
     Structured Rail                Semantic Rail
    - Revenue                      - Periodic filings
    - Investment                   - Strategy search
    - Fundraising                  - Narrative context
    - Supply Contract
            |                             |
            +--------------+--------------+
                           |
                           v
                     Evidence Pack
                           |
                           v
                    HyperCLOVA X
                           |
                           v
                 Grounding Validator
                           |
                           v
                    FastAPI /answer
```

정형 수치·계산 질의는 DB와 deterministic logic을 우선 사용하고,  
서술형 질의는 기업/연도/보고서 범위를 적용한 semantic retrieval 결과를 기반으로 생성합니다.

복수 기업·복수 연도 질의에서는 특정 기업이나 특정 연도에 Evidence가 편중되지 않도록  
기업 × 연도 scope 단위로 검색한 뒤 Evidence Pack을 구성합니다.

---

## 평가용 API

### Public Endpoint

```text
http://49.50.142.34
```

### Answer API

```text
GET http://49.50.142.34/answer
```

요청 방식:

```text
GET /answer?question_id={id}&question={질의}
```

예시:

```bash
curl -G "http://49.50.142.34/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은 얼마인가?"
```

응답은 `application/json`이며 아래 5개 필드는 모두 문자열입니다.

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "질의 처리 방식과 검색 상태를 나타내는 실행 요약",
  "answer": "최종 생성 답변 및 근거 공시"
}
```

`think_trace`는 내부 Chain-of-Thought가 아니라  
질의 처리 방식, 검색 상태, Evidence 수, 생성 방식 등을 나타내는 고수준 실행 정보입니다.

헬스체크:

```text
GET http://49.50.142.34/health
```

API 요청/응답 상세 명세는 `docs/api.md`를 참고합니다.

---

## 실행 환경

- Python: `>=3.11,<3.13`
- FastAPI
- PostgreSQL 16
- pgvector
- SQLAlchemy
- Alembic
- HyperCLOVA X
- Docker / Docker Compose

의존성의 기준 파일은 `pyproject.toml`입니다.

---

## 로컬 설치

Python 3.11 또는 3.12 환경을 권장합니다.

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

개발 의존성까지 설치하려면:

```bash
python -m pip install -e ".[dev]"
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

---

## 환경 변수

`.env.example`을 복사하여 `.env`를 생성합니다.

```env
HCX_API_KEY=
CLOVA_STUDIO_API_KEY=

POSTGRES_DB=disclosure
POSTGRES_USER=disclosure
POSTGRES_PASSWORD=change-me
POSTGRES_PORT=5432

DATABASE_URL=postgresql+psycopg://disclosure:change-me@db:5432/disclosure
APP_PORT=8000
```

실제 API Key와 비밀번호는 Repository에 포함하지 않습니다.

---

## Docker 실행

```bash
cp .env.example .env
docker compose up -d --build
```

상태 확인:

```bash
docker compose ps
curl http://localhost:8000/health
```

네이버클라우드 배포 절차와 PostgreSQL 데이터 이관 방법은  
`docs/deployment.md`를 참고합니다.

---

## 데이터 준비

본 시스템은 대회에서 제공된 공시 corpus를 사용합니다.

기본 데이터 구조:

```text
data/
├── manifest.jsonl
├── raw/
│   ├── periodic/
│   ├── major/
│   ├── holding/
│   └── exchange/
└── processed/
```

원본 `data/raw`는 수정하지 않습니다.  
Canonical JSONL과 검색용 chunk 등은 원본에서 재생성 가능한 파생 데이터입니다.

DB schema는 Alembic migration으로 관리합니다.

```bash
alembic upgrade head
```

데이터 파싱 및 적재 스크립트는 `scripts/`에 포함되어 있습니다.  
상세 구조는 `docs/source-layer.md`, 배포/DB 이관 절차는 `docs/deployment.md`를 참고합니다.

---

## Grounding 및 정보 한계 처리

본 시스템은 제공된 공시 corpus에서 확인되는 정보를 기준으로 답변합니다.

다음과 같은 경우 임의의 값을 생성하지 않는 것을 원칙으로 합니다.

- corpus에 존재하지 않는 기업 또는 기간
- 미래 실제 실적
- 실시간 주가 등 외부 정보
- 공시에서 직접 확인할 수 없는 인과관계
- 계획된 투자금액과 실제 집행액의 혼동
- 근거 없는 성공 가능성 또는 미래 확정 예측

질의 처리 결과는 상황에 따라 다음 상태를 사용합니다.

- `ANSWERABLE`: 근거를 확보하여 답변 가능
- `PARTIAL`: 일부 근거만 확인되거나 안전한 완전 답변 생성이 어려움
- `NO_MATCH`: 해당 범위에서 근거를 찾지 못함
- `UNRESOLVED`: 기업/기간 등 분석 대상을 확정할 수 없음

공시된 투자 결정 금액과 실제 집행액, 일반 산업 통계와 특정 기업의 개별 성과 등은  
서로 다른 의미로 취급하며, 근거 범위를 넘어선 단정은 피하도록 설계했습니다.

---

## 검증

기본 단위 테스트:

```bash
pytest -q
```

정적 검사:

```bash
ruff check src tests
ruff format --check src tests
```

회귀 평가 예시:

```bash
DATABASE_URL=postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
python scripts/run_regression_eval.py \
  --cases evals/extended_cases.jsonl \
  --output-jsonl data/quality/regression.jsonl \
  --output-csv data/quality/regression.csv
```

최종 검증에서 확인한 주요 결과:

- Extended regression: 30 cases, `fail=0`, `error=0`
- Multi-company final stress: 4 cases 모두 hard pass
- Public `/answer` structured/hybrid smoke test 완료
- 다중 기업, 다중 연도, 자연어 변형, 정보 부재, 외부 정보 요청 등에 대한 추가 수동 테스트 수행

---

## 데이터 계약

한 manifest 행과 같은 접수번호에 속한 물리 파일들을 하나의 `FilingPackage`로 보존합니다.

```text
FilingPackage
├── company / filing / correction
├── source_files[]              # 원본 파일별 경로·해시·실제 형식·역할
├── documents[]                 # 의미 문서별 파싱 결과
│   ├── sections[]
│   ├── blocks[]                # raw + normalized text
│   ├── tables[]                # 좌표·span·빈 셀·DART 속성
│   └── parse_summary/issues[]  # 성공·부분 성공·실패와 근거
└── package_issues[]
```

Schema version은 `2.2.0`입니다. 새 중첩 표 관계 필드는 기본값이 있어 2.1.0 입력도 읽을 수
있지만, 문자·중첩 표·정정사항 보존 수정은 기존 JSONL에 소급 적용되지 않습니다.
이전 결과는 비교용으로 보관하고 새 출력 경로로 다시 생성합니다.

핵심 원칙:

- `.xml` 확장자를 그대로 믿지 않고 실제 bytes로 DART XML/HTML/PDF를 판별합니다.
- periodic의 본문·별도감사·연결감사 파일을 서로 다른 semantic document로 보존합니다.
- `text_raw`를 남기고 검색용 `text_normalized`는 별도 필드에 둡니다.
- 표의 빈 셀, 행·열 좌표, `rowspan`/`colspan`, `ACODE`, `ACONTEXT`, 단위 정보를 보존합니다.
- XML 복구와 PDF 텍스트 부재를 성공으로 숨기지 않고 `partial`/`failed`로 기록합니다.
- 원본 XML은 수정하지 않고, 단독 `&`와 자연어 `<...>`를 메모리상의 parse buffer에서만
  복구하며 복구 횟수와 대표 위치를 `ParseIssue.occurrence_count`에 기록합니다.
- DART viewer HTML은 보고서 본문이 아니라 PDF의 TOC/offset companion metadata로 취급합니다.

### 2.2.0 보존 규칙

- `&reg;` 등 알려진 HTML entity는 문자로 복원하고, 정의되지 않은 entity는 원문 표현과
  경고를 보존합니다. XML 구조 복구 중에도 정상 문자 참조가 사라지지 않도록 보호합니다.
- `<PUBG: 배틀그라운드>`, `<신설 '23. 3.16.>`와 확인된 unpaired 제품명 표현을 보존합니다.
  정상 namespace/확장 태그와 CDATA, 원본 파일은 변경하지 않습니다.
- BODY를 원래 순서대로 순회하여 COVER·LIBRARY/CORRECTION·직접 텍스트·tail을 보존합니다.
- 중첩 표는 독립 TABLE block으로 한 번만 저장합니다. 부모 셀의 `nested_table_ids`와
  자식 표의 `parent_table_id`/`parent_cell_locator`로 연결합니다. 부모 셀의 `text_raw`에는
  자식 표 내용을 중복 삽입하지 않습니다. 원래 위치는 source locator로 추적합니다.
- 표 ID와 block ID가 달라질 수 있으므로 새/이전 JSONL의 ID를 섞어 검색 인덱스를 만들지 않습니다.
- 구조 복구가 남으면 `partial`을 유지합니다. `success`만으로 무손실을 인증하지 않습니다.

### DART parser 2.2.1 overlay

전체 v2.2 JSONL은 immutable base snapshot으로 유지합니다. v2.2 audit에서
`DartParser + partial + markup_recovery`로 확인된 package만 원본에서 다시 파싱하여
별도 overlay JSONL에 기록합니다. Schema version은 계속 `2.2.0`이고 Dart parser version만
`2.2.1`입니다.

2.2.1은 `ENG=""Snow Corporation"`, `ENG="Accrued Expenses""`처럼 실제 corpus에서 확인된
깨진 `ENG` attribute quote를 구조 복구 전에 좁게 처리합니다. raw source는 수정하지 않으며,
attribute 안의 stray quote 자체도 삭제하지 않고 parse buffer에서 XML entity로 보존합니다.

```bash
python scripts/reparse_dart_overlay.py \
  --data-root data \
  --base data/processed/canonical-v22-smoke.jsonl \
  --output data/processed/canonical-dart-221-overlay.jsonl

python scripts/profile_dart_overlay.py \
  --base data/processed/canonical-v22-smoke.jsonl \
  --overlay data/processed/canonical-dart-221-overlay.jsonl
```

후속 consumer는 `read_effective_canonical(base, overlay)`를 사용합니다. `filing_id`가 merge key이며
동일 receipt number와 corp code를 다시 확인한 뒤 overlay package를 선택합니다. base JSONL은
절대 덮어쓰지 않습니다.

---

## Canonical Parsing 실행

빠른 smoke test에서는 해시 계산을 생략할 수 있습니다.

```bash
python -m disclosure_agent.parsing.batch data \
  --output data/processed/canonical-smoke.jsonl \
  --skip-hashes
```

최종 산출물에는 source SHA-256을 포함합니다.

```bash
python -m disclosure_agent.parsing.batch data \
  --output data/processed/canonical.jsonl
```

산출물:

- `data/processed/canonical.jsonl`: 접수 단위 `FilingPackage` JSONL
- `data/processed/canonical.failures.jsonl`: inventory 단계 실패 기록

파서 내부 오류는 해당 document의 `parse_summary.status=failed`와 `parse_issues`에 기록됩니다.
따라서 단순히 JSONL 행 수만 확인하지 말고 상태별 개수를 함께 확인해야 합니다.

2.2.0 재파싱 및 독립 검증:

```bash
python -m disclosure_agent.parsing.batch data \
  --output data/processed/canonical-v22-smoke.jsonl \
  --skip-hashes

python scripts/canonical_audit.py --self-test

python scripts/canonical_audit.py \
  --input data/processed/canonical-v22-smoke.jsonl \
  --data-root data
```

이전 JSONL을 지정하려면 `--before data/processed/이전파일명.jsonl`을 추가합니다.
검증기는 production parser를 호출하지 않으며 한글/영문 PUBG, 중첩 표의 소유 행·셀,
전체 표 위치를 독립 검사합니다. 표의 값 비교는 기본적으로 표본 검사이고,
`--all-tables`를 추가하면 전체 표를 비교합니다. 원문 구조가 모호한 경우에는
`UNVERIFIED`/`REVIEW`로 남깁니다. 매번 새로운 `data/quality/canonical-audit-*.zip`을
생성하며 원본 공시와 전체 Canonical JSONL은 ZIP에 넣지 않습니다.

---

## 전체 데이터 파이프라인

```text
Canonical Parsing
    -> Facts / Events extraction
    -> PostgreSQL + pgvector
    -> Query Planning / Routing
    -> Structured / Semantic Retrieval
    -> Evidence Pack
    -> HyperCLOVA X Grounded Generation
    -> Grounding Validation
    -> FastAPI /answer
```

중요도 필터와 섹터별 규칙은 Canonical 원본을 지우지 않고 retrieval/파생 단계에만 적용합니다.
이 구조를 통해 질문 기준이 바뀌어도 원문을 다시 훼손하지 않고 검색 및 분석 결과를 재생성할 수 있습니다.
