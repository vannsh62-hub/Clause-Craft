# Production image for the FastAPI service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --upgrade pip && pip install .

COPY alembic ./alembic
COPY alembic.ini ./
COPY clauses ./clauses
COPY playbooks ./playbooks
COPY skills ./skills

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
