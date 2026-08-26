# Disclosure Analyst Agent

공시 원문을 손실을 추적할 수 있는 Canonical JSON으로 변환하고, 이후 Facts/Events
추출·PostgreSQL/pgvector 검색·HyperCLOVA X 답변으로 연결하기 위한 프로젝트입니다.

현재 작업 단계는 **Canonical Parsing**입니다. Markdown은 원본 저장 형식이 아니라 검색 및
LLM context를 만들기 위한 파생 산출물로 취급합니다.

## 개발 환경

- Python 3.12 권장 (`>=3.11,<3.13`)
- 작업 브랜치: `chatgpt/canonical-parsing`
- 원본 corpus root: `data/`

Windows PowerShell 최초 설정:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

`pyproject.toml`이 바뀌었으므로 기존 가상환경에서도 위의 마지막 설치 명령을 다시
실행해야 합니다. PDF 원문 처리를 위해 `pypdf`가 추가되었습니다.

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

Schema version은 `2.0.0`입니다. 이전 `CanonicalDisclosure` JSONL과 호환되지 않으므로
기존 `canonical.jsonl`은 새 코드로 다시 생성해야 합니다.

핵심 원칙:

- `.xml` 확장자를 그대로 믿지 않고 실제 bytes로 DART XML/HTML/PDF를 판별합니다.
- periodic의 본문·별도감사·연결감사 파일을 서로 다른 semantic document로 보존합니다.
- `text_raw`를 남기고 검색용 `text_normalized`는 별도 필드에 둡니다.
- 표의 빈 셀, 행·열 좌표, `rowspan`/`colspan`, `ACODE`, `ACONTEXT`, 단위 정보를 보존합니다.
- XML 복구와 PDF 텍스트 부재를 성공으로 숨기지 않고 `partial`/`failed`로 기록합니다.
- DART viewer HTML은 보고서 본문이 아니라 PDF의 TOC/offset companion metadata로 취급합니다.

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
