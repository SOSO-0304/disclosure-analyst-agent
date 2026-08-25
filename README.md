# Disclosure Analyst Agent

공시 원문(XML/HTML)을 공통 Canonical Document로 파싱하고, 이후 Facts/Events 추출·PostgreSQL·pgvector 기반 검색 및 공시 분석 Agent로 확장하기 위한 프로젝트입니다.

## 1. Environment

- Python: 3.12 권장 (`pyproject.toml`: `>=3.11,<3.13`)
- Virtual Environment: `.venv`
- 패키지 설치 방식: editable install (`pip install -e`)

### Windows PowerShell - 최초 1회 설정

프로젝트 루트에서 실행합니다.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

설치 확인:

```powershell
python -c "import disclosure_agent; print(disclosure_agent.__file__)"
```

### macOS / Linux - 최초 1회 설정

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### 평소 개발할 때

`.venv`를 매번 다시 만들거나 패키지를 매번 다시 설치할 필요는 없습니다. 새 터미널을 열었을 때 기존 가상환경만 활성화하면 됩니다.

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

현재 브랜치의 최신 코드를 받을 때:

```powershell
git pull
```

`src/disclosure_agent/`의 Python 코드만 변경된 경우 editable install이 해당 소스를 직접 바라보기 때문에 일반적으로 `pip install`을 다시 할 필요가 없습니다.

다음 경우에는 다시 설치합니다.

- `pyproject.toml`의 dependencies가 변경된 경우
- `.venv`를 삭제하거나 새로 만든 경우
- 개발 dependency가 새로 추가된 경우

```powershell
python -m pip install -e ".[dev]"
```

> `.venv/`는 로컬 개발 환경이므로 Git에 커밋하지 않습니다.

## 2. Working Branch

Canonical parsing 작업 브랜치:

```text
chatgpt/canonical-parsing
```

원격 브랜치를 처음 로컬로 가져오는 경우:

```powershell
git fetch origin
git switch --track origin/chatgpt/canonical-parsing
```

이미 로컬 브랜치가 있다면:

```powershell
git switch chatgpt/canonical-parsing
git pull
```

현재 브랜치 확인:

```powershell
git status
```

## 3. Data Structure

현재 corpus root는 `data/`입니다.

```text
data/
├── manifest.jsonl
├── raw/
│   ├── periodic/
│   ├── major/
│   ├── holding/
│   └── exchange/
└── processed/              # parsing 실행 시 생성
```

`manifest.jsonl`에는 각 공시의 `doc_id`, `corp_code`, `corp_name`, `doc_group`, `doc_subtype`, `rcept_no`, `rcept_dt`, `file_path` 등의 metadata가 들어 있습니다.

원본 데이터와 생성 데이터는 용량이 크므로 Git에 포함하지 않는 것을 원칙으로 합니다.

## 4. Canonical Parsing

### 목적

원본 공시는 문서군별 구조가 서로 다릅니다.

```text
exchange  -> HTML/table 중심 구조
periodic  -> DART XML + SECTION 계층 + 복수 XML 가능
major     -> DART XML
holding   -> DART XML
```

Parsing 단계에서는 이 서로 다른 원본 구조를 하나의 `CanonicalDisclosure` schema로 변환합니다.

```text
Raw XML / HTML
      |
      v
DocumentParser
      |
      +-- ExchangeParser
      +-- PeriodicParser
      +-- MajorParser
      +-- HoldingParser
      |
      v
CanonicalDisclosure
      |
      v
canonical.jsonl
```

이 단계는 Facts/Events 의미 추출 단계와 구분합니다. Parsing에서는 원문 구조·section·table·field·ACODE/AUNIT 등의 정보를 가능한 한 보존하고, 금융적 의미 정규화는 다음 extraction 단계에서 수행합니다.

### 전체 corpus 실행

프로젝트 루트에서:

```powershell
python -m disclosure_agent.parsing.batch data --output data\processed\canonical.jsonl
```

실행 시 원본 XML을 한 번 인덱싱한 뒤 전체 manifest를 순회합니다.

```text
Indexing source XML files once...
Indexed ... XML files for ... receipt numbers.
[100/4204] parsed=100 failed=0
[200/4204] parsed=200 failed=0
...
```

결과 파일:

```text
data/processed/canonical.jsonl
```

한 줄(JSON object)이 하나의 Canonical Disclosure Document입니다.

실패 문서가 존재하면 별도 파일도 생성됩니다.

```text
data/processed/canonical.failures.jsonl
```

### 현재 corpus 기준 검증 현황

초기 전체 실행 기준:

```text
전체        4,204
성공        4,201
실패            3

exchange    1,469 / 1,469
holding     1,083 / 1,083
major         598 /   598
periodic    1,051 / 1,054
```

현재 3건의 periodic 실패는 parser 내부 XML 해석 오류가 아니라 source file resolution 단계의 `FileNotFoundError`로 확인되었습니다. 이후 source index 기반 resolver로 보완 중입니다.

## 5. Canonical Document

공통 최상위 schema는 다음 정보를 보존합니다.

```text
CanonicalDisclosure
├── document_id
├── document_type
├── company
├── metadata
├── revision
├── source_files
└── payload
```

`payload`는 문서 종류에 따라 `periodic`, `exchange`, `major`, `holding`으로 구분됩니다.

예를 들어 exchange table의 계층 구조:

```text
5. 계약기간
├── 시작일 -> 2023-01-30
└── 종료일 -> 2024-10-31
```

는 Canonical field에서 parent path를 유지합니다.

DART XML의 `ACODE`, `AUNIT`, `AUNITVALUE` 역시 다음 Facts/Events extraction에서 활용할 수 있도록 parsing 단계에서 보존합니다.

## 6. Testing

전체 corpus validation과 별개로 코드 regression test는 `pytest`로 실행합니다.

```powershell
pytest -v
```

구분:

```text
pytest
└── parser/model 코드가 의도한 규칙을 계속 지키는지 검사

batch parsing
└── 실제 전체 corpus가 정상적으로 Canonical Document로 변환되는지 검사
```

## 7. Project Structure

```text
disclosure-analyst-agent/
├── src/disclosure_agent/
│   ├── domain/             # Canonical domain models
│   ├── inventory/
│   ├── parsing/            # XML/HTML -> Canonical
│   ├── extractors/         # Canonical -> Facts/Events (next)
│   ├── rendering/
│   ├── storage/            # JSONL / PostgreSQL adapters
│   ├── retrieval/          # pgvector / retrieval (next)
│   ├── llm/
│   └── api/
├── config/
├── alembic/
├── tests/
├── data/
│   ├── manifest.jsonl
│   ├── raw/
│   └── processed/
├── pyproject.toml
├── .gitignore
└── README.md
```

## 8. Pipeline Roadmap

```text
Raw disclosure XML/HTML
        |
        v
[1] Canonical Parsing       <- current
        |
        v
[2] Facts / Events Extraction
        |
        v
[3] PostgreSQL Ingestion
        |
        +-- structured facts/events
        +-- document/chunk metadata
        |
        v
[4] Chunking + Embedding
        |
        v
[5] pgvector Retrieval
        |
        v
[6] Disclosure Analyst Agent
```

새로운 실행 방법이나 dependency가 추가될 때 이 README를 함께 갱신합니다.
