# Disclosure Analyst Agent

공시 원문을 손실을 추적할 수 있는 Canonical JSON으로 변환하고, 이후 Facts/Events
추출·PostgreSQL/pgvector 검색·HyperCLOVA X 답변으로 연결하기 위한 프로젝트입니다.

현재 구현 범위는 **Canonical Parsing → Facts/Events → PostgreSQL/pgvector 검색 →
질의 라우팅 → HyperCLOVA X 기반 근거 답변 → 평가용 FastAPI**입니다.
Markdown/검색 context는 원본 저장 형식이 아니라 파생 산출물로 취급합니다.

## 개발 환경

- Python 3.12 권장 (`>=3.11,<3.13`)
- 작업 브랜치: `model/data-pipeline`
- 원본 corpus root: `data/`

Windows PowerShell 최초 설정:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

`pyproject.toml`이 바뀌었으므로 기존 가상환경에서도 위의 마지막 설치 명령을 다시
실행해야 합니다. PDF 원문 처리를 위해 `pypdf`가 추가되었습니다.

## 평가용 API 및 배포

대회 평가용 API:

```text
GET /answer?question_id={id}&question={질의}
GET /health
```

로컬 Docker 실행:

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```

API 요청/응답 스키마는 `docs/api.md`, 네이버클라우드 배포 절차와 PostgreSQL 데이터
이관 방법은 `docs/deployment.md`를 참고합니다.

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

## Corpus 구조

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

원본 `data/raw`는 수정하지 않습니다. Canonical JSONL과 이후 Markdown/chunk는 언제든
원본에서 재생성할 수 있는 파생 데이터입니다.

## 실행

빠른 smoke test에서는 해시 계산을 생략할 수 있습니다.

```powershell
python -m disclosure_agent.parsing.batch data `
  --output data\processed\canonical-smoke.jsonl `
  --skip-hashes
```

최종 산출물에는 source SHA-256을 포함합니다.

```powershell
python -m disclosure_agent.parsing.batch data `
  --output data\processed\canonical.jsonl
```

배치는 먼저 manifest 전체를 검증하고 원본 파일을 한 번만 인덱싱합니다. 완성된 임시
파일만 최종 경로로 교체하므로 중간 오류가 기존 결과를 덮어쓰지 않습니다.

산출물:

- `data/processed/canonical.jsonl`: 접수 단위 `FilingPackage` JSONL
- `data/processed/canonical.failures.jsonl`: inventory 단계 실패 기록

파서 내부 오류는 해당 document의 `parse_summary.status=failed`와 `parse_issues`에
기록됩니다. 따라서 단순히 JSONL 행 수만 확인하지 말고 상태별 개수를 함께 확인해야 합니다.

## 검증

```powershell
pytest -q
ruff check src tests
ruff format --check src tests
```

전체 corpus 완료 기준은 다음과 같습니다.

- manifest 행 수와 package 수가 일치
- 모든 manifest `n_files`와 resolved source 수가 일치
- `inventory_failures == 0`
- `failed`/`unsupported` document가 0이거나 승인된 예외 목록에만 존재
- correction filing의 원문은 보존되고, lineage 미해결은 경고로 식별

2.2.0 재파싱 및 독립 검증 (기존 결과는 그대로 둡니다):

```powershell
python -m disclosure_agent.parsing.batch data `
  --output data\processed\canonical-v22-smoke.jsonl `
  --skip-hashes
python scripts\canonical_audit.py --self-test
python scripts\canonical_audit.py `
  --input data\processed\canonical-v22-smoke.jsonl `
  --data-root data
```

이전 JSONL을 지정하려면 `--before data\processed\이전파일명.jsonl`을 추가합니다.
검증기는 production parser를 호출하지 않으며 한글/영문 PUBG, 중첩 표의 소유 행·셀,
전체 표 위치를 독립 검사합니다. 표의 값 비교는 기본적으로 표본 검사이고,
`--all-tables`를 추가하면 전체 표를 비교합니다. 원문 구조가 모호한 경우에는
`UNVERIFIED`/`REVIEW`로 남깁니다. 매번 새로운 `data/quality/canonical-audit-*.zip`을
생성하며 원본 공시와 전체 Canonical JSONL은 ZIP에 넣지 않습니다.

## 다음 단계

```text
Canonical JSON
    -> Facts / Events extraction
    -> PostgreSQL + pgvector
    -> query routing / retrieval
    -> 질문별 최소 context 구성
    -> HyperCLOVA X
```

중요도 필터와 섹터별 규칙은 Canonical 원본을 지우지 않고 retrieval/Markdown 단계에만
적용합니다. 이 구조라야 질문 기준이 바뀌어도 원문을 다시 파싱하지 않고 재생성할 수 있습니다.
