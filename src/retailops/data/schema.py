"""Schema catalog access: plain-English table and column descriptions for the warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).with_name("schema_catalog.yaml")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, dict[str, Any]]:
    """Return {table: {description, layer, columns: {column: description}}}."""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    tables: dict[str, dict[str, Any]] = raw["tables"]
    return tables
