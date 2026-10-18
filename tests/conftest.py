"""Shared fixtures. The full default dataset (seed 42, 180 days, 800 orders/day) is built once
per test session because several test modules need realistic volumes."""

from __future__ import annotations

from pathlib import Path

import pytest

from retailops.data.generate import GenConfig, GeneratedData, generate
from retailops.data.load import build_warehouse


@pytest.fixture(scope="session")
def full_data() -> GeneratedData:
    return generate(GenConfig(seed=42, days=180, orders_per_day=800))


@pytest.fixture(scope="session")
def full_db(tmp_path_factory: pytest.TempPathFactory, full_data: GeneratedData) -> Path:
    path = tmp_path_factory.mktemp("full") / "retailops.duckdb"
    build_warehouse(full_data, path)
    return path
