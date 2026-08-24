
# Disclosure Analyst Agent

공시 데이터를 파싱하고 분석하기 위한 프로젝트입니다.

## Environment

- Python 3.12
- Virtual Environment: `.venv`

가상환경 활성화:

```bash
source .venv/bin/activate
````

패키지 설치:

```bash
pip install -r requirements.txt
```

## Data

원본 데이터는 아래 경로에 위치합니다.

```text
data/raw/
├── periodic/
├── major/
├── holding/
└── exchange/
```

`data/raw/`와 `data/parsed/`는 Git에 포함하지 않습니다.

## Parsing

프로젝트 루트에서 각 parser를 실행합니다.

```bash
python parsers/periodic_parser.py
python parsers/major_parser.py
python parsers/holding_parser.py
python parsers/exchange_parser.py
```

파싱된 데이터는 Markdown 형식으로 아래 경로에 생성됩니다.

```text
data/parsed/
├── periodic/
├── major/
├── holding/
└── exchange/
```

## Project Structure

```text
disclosure-analyst-agent/
├── src/disclosure_agent/
│   ├── domain/
│   ├── inventory/
│   ├── parsing/
│   ├── extractors/
│   ├── rendering/
│   ├── storage/
│   ├── retrieval/
│   ├── llm/
│   └── api/
├── config/
├── alembic/
├── tests/
├── data/
│   ├── raw/
│   ├── canonical/
│   ├── retrieval/
│   └── quality/
├── parsers/                 # legacy data-ingestion scripts
├── pyproject.toml
├── .gitignore
└── README.md
```

```

지금은 이 정도로 두고, 이후 **chunking → embedding → vector DB → RAG 실행 방법**이 만들어질 때마다 README에 섹션을 하나씩 추가하는 방식이 좋아.
```
