.PHONY: run test lint format typecheck check clean

run:
	uv run --python .venv python -m uvicorn masterone.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run --python .venv python -m pytest backend/tests/ -v

lint:
	uv run --python .venv python -m ruff check backend/src/ backend/tests/ backend/migrations/

format:
	uv run --python .venv python -m ruff format backend/src/ backend/tests/ backend/migrations/

typecheck:
	mypy backend/src/

check: lint test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
