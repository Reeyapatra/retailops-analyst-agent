# Targets marked "not implemented" fail on purpose until their milestone lands.
.PHONY: setup data ingest dev test lint eval-smoke eval-full docker-build docker-run

setup:
	uv sync

data:
	uv run python -m retailops.data.generate --seed 42 --days 180 --orders-per-day 800

ingest:
	uv run python -m retailops.rag.ingest

dev:
	@echo "make dev: not implemented until M7" && exit 1

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

eval-smoke:  # needs MODEL_* and a provider key; 10 questions, 1 repeat
	uv run python -m evals.run_eval --experiment E1 --limit 10 --repeats 1

eval-full:  # needs MODEL_* and a provider key; add --langsmith to the run_eval lines to log experiments
	uv run python -m evals.run_eval --experiment E0 --repeats 3
	uv run python -m evals.run_eval --experiment E1 --repeats 3
	uv run python -m evals.report

docker-build:
	@echo "make docker-build: not implemented until M7" && exit 1

docker-run:
	@echo "make docker-run: not implemented until M7" && exit 1
