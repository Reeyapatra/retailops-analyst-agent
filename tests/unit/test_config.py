import pytest

import retailops
from retailops.config import Settings, get_settings


def test_package_importable() -> None:
    assert retailops.__version__


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.duckdb_path == "data/retailops.duckdb"
    assert s.app_env == "dev"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_STEPS", "7")
    assert get_settings().max_steps == 7
