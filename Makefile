# Targets marked "not implemented" fail on purpose until their milestone lands.
.PHONY: setup data ingest dev test lint eval-smoke eval-full docker-build docker-run

setup:
	uv sync

data:
	@echo "make data: not implemented until M1" && exit 1

ingest:
	@echo "make ingest: not implemented until M3" && exit 1

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
