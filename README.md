# Disclosure Analyst Agent

기업공시 데이터를 기반으로 사용자의 질문을 이해하고, 필요한 공시를 찾아 **수치 계산·기업 비교·사업 전략 요약**을 수행한 뒤 **근거 공시와 함께 답변하는 AI 공시 분석 Agent**입니다.

핵심 목표는 단순히 자연스러운 답변을 만드는 것이 아니라, **공시에서 확인되는 정보만 사용하고 숫자·기간·기업 범위를 가능한 한 정확하게 맞추는 것**입니다.

---

## 한눈에 보기

이 시스템은 질문에 따라 두 가지 방식으로 동작합니다.

- **정확한 숫자·계산이 필요한 질문**
  - PostgreSQL의 구조화 데이터를 조회
  - 차이·합계·순위 등을 deterministic logic으로 계산
  - LLM이 숫자를 임의 계산하지 않도록 분리

- **사업 전략·투자 방향·기업 비교 같은 서술형 질문**
  - 기업·연도·보고서 범위를 먼저 해석
  - 관련 공시 문서를 semantic retrieval로 검색
  - 검색된 Evidence만 HyperCLOVA X에 전달
  - 생성 결과를 Grounding Validator로 다시 점검

모든 최종 답변에는 **근거 공시**를 표시합니다.

---

## 예시 질문

### 정형 수치 조회

```text
삼성전자의 2025년 연결기준 매출액은 얼마인가?
```

→ DB에서 2025년 연결 매출액을 직접 조회하여 답변합니다.

### 기업 간 비교

```text
LG씨엔에스와 현대오토에버의 2025년 핵심 전략을 비교해줘
```

→ 두 기업의 2025년 공시를 각각 검색한 뒤, 두 기업의 차이가 드러나도록 비교합니다.

### 다중 기업 비교

```text
삼성바이오로직스, 셀트리온, 한미약품의 2025년 성장 전략을 비교해줘
```

→ 한 기업의 검색 결과에 편중되지 않도록 기업별 Evidence를 확보한 뒤 비교합니다.

### 정보 한계 확인

```text
삼성전자의 2099년 실제 매출액은 얼마야?
```

→ 해당 범위의 공시 근거가 없으면 임의의 숫자를 만들지 않고 정보 부족 상태를 반환합니다.

---

## 주요 기능

| 영역 | 기능 |
|---|---|
| 질의 이해 | 기업명, 연도, 보고서 범위, 질의 의도 분석 |
| 정형 분석 | 연결 매출액 등 재무 수치 조회 |
| 계산 | 차이, 합계, 평균, 순위 등 deterministic 계산 |
| 시설투자 | 신규시설투자 공시 조회 및 투자 결정 금액 분석 |
| 자금조달 | CB/BW/EB 등 자금조달 이벤트 분석 |
| 공급계약 | 최초 공시·정정·해지까지 계약 lifecycle 분석 |
| 서술형 검색 | 사업보고서·분기보고서 등 공시 문서 semantic retrieval |
| 비교 분석 | 복수 기업·복수 연도 비교 |
| 생성 | HyperCLOVA X 기반 grounded answer 생성 |
| 검증 | 기업·연도·근거 정합성 검증 |
| 정보 한계 | `ANSWERABLE`, `PARTIAL`, `NO_MATCH`, `UNRESOLVED` 상태 처리 |
| 출처 표시 | 모든 최종 답변에 근거 공시 표시 |

---

## 전체 처리 흐름

```text
사용자 질문
    |
    v
Query Planner
- 기업
- 연도
- 보고서 범위
- 질의 유형
    |
    +---------------------------+
    |                           |
    v                           v
Structured Retrieval       Semantic Retrieval
- 매출                     - 사업 전략
- 시설투자                  - 성장 방향
- 자금조달                  - 기업 비교
- 공급계약                  - 기타 서술형 근거
    |                           |
    +------------+--------------+
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

### 왜 Structured + Semantic을 함께 사용하나요?

모든 질문을 LLM 하나로 처리하면 숫자 계산과 근거 귀속에서 오류가 발생할 수 있습니다.

따라서 본 프로젝트는 다음과 같이 역할을 분리했습니다.

```text
정확한 숫자 / 계산
→ DB + deterministic logic

문맥 이해 / 사업 전략 요약 / 비교
→ Retrieval + HyperCLOVA X
```

이를 통해 정형 질의의 재현성과 서술형 질의의 유연성을 함께 확보하는 것을 목표로 합니다.

---

## 복수 기업·복수 연도 검색

복수 기업을 한 번에 검색하면 특정 기업의 문서만 상위 검색 결과를 차지할 수 있습니다.

예를 들어 아래 질문의 경우:

```text
A사와 B사의 2025년 사업 전략을 비교해줘
```

단일 검색이 아니라 다음과 같이 scope를 분리합니다.

```text
A사 × 2025
B사 × 2025
```

복수 연도까지 포함하면:

```text
A사 × 2024
A사 × 2025
B사 × 2024
B사 × 2025
```

각 scope에서 근거를 확보한 뒤 Evidence Pack을 구성하여 특정 기업·연도에 검색 결과가 편중되는 문제를 줄였습니다.

---

## Grounding 및 정보 한계 처리

본 시스템은 **제공된 공시 corpus에서 확인 가능한 정보만 답변에 사용하는 것**을 설계 원칙으로 합니다.

특히 다음과 같은 구분을 중요하게 다룹니다.

```text
투자 결정 금액 != 실제 집행 금액
계획             != 실제 성과
일반 산업 통계    != 특정 기업의 개별 확률
미래 목표         != 미래 실제 실적
```

질의 처리 결과는 다음 상태 중 하나로 관리합니다.

| 상태 | 의미 |
|---|---|
| `ANSWERABLE` | 필요한 근거를 확보하여 답변 가능 |
| `PARTIAL` | 일부만 확인되거나 완전한 답변을 안전하게 구성하기 어려움 |
| `NO_MATCH` | 요청 범위에 해당하는 근거를 찾지 못함 |
| `UNRESOLVED` | 기업·연도 등 분석 대상을 확정하기 어려움 |

실시간 주가, corpus 밖 기업 정보, 미래 실제 실적 등 **제공 데이터만으로 확인할 수 없는 정보는 외부 데이터로 보완하지 않습니다.**

---

# 평가용 API

## Public Endpoint

```text
http://49.50.142.34
```

## Answer API

```text
GET http://49.50.142.34/answer
```

요청 형식:

```text
GET /answer?question_id={ID}&question={질의}
```

예시:

```bash
curl -G "http://49.50.142.34/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은 얼마인가?"
```

응답은 `application/json`이며 5개 필드는 모두 문자열입니다.

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2025년 연결기준 매출액은 얼마인가?",
  "retrieved_context": "답변 생성에 사용한 검색 근거",
  "think_trace": "질의 처리 방식과 검색 상태에 대한 고수준 실행 요약",
  "answer": "최종 답변 및 근거 공시"
}
```

`think_trace`는 내부 Chain-of-Thought가 아니라, 질의 처리 모드·검색 상태·Evidence 수·생성 방식 등을 나타내는 **고수준 실행 정보**입니다.

헬스체크:

```text
GET http://49.50.142.34/health
```

API 상세 명세: `docs/api.md`

---

# 실행 방법

## 1. 실행 환경

- Python `>=3.11,<3.13`
- FastAPI
- PostgreSQL 16
- pgvector
- SQLAlchemy
- Alembic
- HyperCLOVA X
- Docker / Docker Compose

Python 의존성의 기준 파일은 `pyproject.toml`입니다.

## 2. 환경 변수 설정

`.env.example`을 복사합니다.

```bash
cp .env.example .env
```

예시:

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

## 3. Docker 실행

```bash
docker compose up -d --build
```

상태 확인:

```bash
docker compose ps
curl http://localhost:8000/health
```

네이버클라우드 배포 및 DB 이관 절차는 `docs/deployment.md`를 참고합니다.

---

# 데이터 준비

본 시스템은 **대회에서 제공된 공시 corpus만 사용**합니다.

원본 corpus는 아래 구조로 배치합니다.

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

Canonical JSON, Facts/Events, retrieval chunk 등은 원본에서 재생성 가능한 파생 데이터로 관리합니다.

DB schema 적용:

```bash
alembic upgrade head
```

데이터 파싱 및 적재 스크립트는 `scripts/`에 있습니다.

세부 문서:

- 데이터 계층 / Canonical 구조: `docs/source-layer.md`
- API 명세: `docs/api.md`
- 배포 / PostgreSQL 이관: `docs/deployment.md`

---

# 데이터 파이프라인

```text
대회 제공 공시 원문
    |
    v
Canonical Parsing
    |
    v
Facts / Events Extraction
    |
    v
PostgreSQL + pgvector
    |
    v
Query Planning
    |
    +-------------------------+
    |                         |
    v                         v
Structured Retrieval     Semantic Retrieval
    |                         |
    +------------+------------+
                 |
                 v
            Evidence Pack
                 |
                 v
            HyperCLOVA X
                 |
                 v
        Grounding Validation
                 |
                 v
            FastAPI API
```

### Canonical Parsing

공시 원문을 바로 요약하지 않고 먼저 공시의 원래 구조와 추적 가능한 정보를 보존하는 Canonical JSON으로 변환합니다.

주요 원칙:

- 확장자만 믿지 않고 실제 bytes를 기준으로 XML / HTML / PDF 판별
- 원본 텍스트와 검색용 정규화 텍스트를 분리
- 표의 행·열, 빈 셀, rowspan / colspan, 단위 정보 보존
- 중첩 표 관계 보존
- 정정 공시와 원본 공시의 lineage 추적
- 파싱 실패·부분 성공을 숨기지 않고 상태로 기록

세부 구현 및 parser version 정보는 `docs/source-layer.md`를 참고합니다.

---

# 프로젝트 구조

```text
.
├── src/                    # Agent / parser / retrieval / API 구현
├── scripts/                # 데이터 적재, 검증, 평가 실행 스크립트
├── tests/                  # 단위 테스트
├── evals/                  # 회귀 및 stress 평가 질의
├── alembic/                # DB migration
├── config/                 # 프로젝트 설정
├── docs/
│   ├── api.md              # API 상세 명세
│   ├── deployment.md       # 배포 및 DB 이관
│   └── source-layer.md     # 데이터 / Canonical 상세 설계
├── Dockerfile
├── compose.yaml
├── pyproject.toml
├── .env.example
└── README.md
```

---

# 검증

기본 단위 테스트:

```bash
pytest -q
```

정적 검사:

```bash
ruff check src tests
ruff format --check src tests
```

회귀 평가:

```bash
DATABASE_URL=postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure \
python scripts/run_regression_eval.py \
  --cases evals/extended_cases.jsonl \
  --output-jsonl data/quality/regression.jsonl \
  --output-csv data/quality/regression.csv
```

현재 릴리스 후보에서 확인한 주요 검증 결과:

- Extended regression: 30 cases, `fail=0`, `error=0`
- Final stress cases: 4 cases 모두 hard pass
- Public `/answer` structured / hybrid smoke test 완료
- 다중 기업, 다중 연도, 자연어 변형, 정보 부재, 외부 정보 요청에 대한 추가 수동 테스트 수행

평가용 테스트 정의는 `evals/`를 참고합니다.

---

# 핵심 설계 요약

이 프로젝트는 다음 세 가지 원칙을 중심으로 구현했습니다.

1. **숫자는 가능한 한 구조화된 데이터에서 직접 계산한다.**
2. **LLM은 검색된 공시 근거 안에서 설명과 비교를 수행한다.**
3. **확인할 수 없는 정보는 임의로 채우지 않고 정보 한계를 드러낸다.**

즉, 단순한 공시 검색 챗봇이 아니라 **공시 검색 → 계산 → 비교 → 근거 검증**을 하나의 흐름으로 처리하는 공시 분석 Agent를 목표로 합니다.
