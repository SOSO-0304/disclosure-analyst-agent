FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[dev]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY tests ./tests

CMD ["python", "-m", "disclosure_agent.cli"]