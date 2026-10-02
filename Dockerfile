# ---- Stage 1: install the backend package ----
FROM python:3.11-slim AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
RUN pip install --no-cache-dir ./backend

# ---- Stage 2: Production ----
FROM python:3.11-slim

WORKDIR /app

# Copy the installed, non-editable package and its dependencies.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Security: run as non-root user
RUN useradd -m appuser

# Alembic runs from the repository root without importing a source checkout.
COPY backend/alembic.ini backend/alembic.ini
COPY backend/migrations backend/migrations

# Create data directory with correct ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["sh", "-c", "python -m masterone.mdm.migrate && exec uvicorn masterone.main:app --host 0.0.0.0 --port 8000"]
