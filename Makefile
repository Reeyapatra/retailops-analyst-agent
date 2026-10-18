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

eval-smoke:
	@echo "make eval-smoke: not implemented until M4" && exit 1

eval-full:
	@echo "make eval-full: not implemented until M4" && exit 1

docker-build:
	@echo "make docker-build: not implemented until M7" && exit 1

docker-run:
	@echo "make docker-run: not implemented until M7" && exit 1
