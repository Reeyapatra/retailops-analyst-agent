from __future__ import annotations

import pytest

from retailops.config import ConfigError, Settings
from retailops.models import get_chat_model


def test_missing_model_name_raises_clear_error() -> None:
    with pytest.raises(ConfigError, match="MODEL_SQL"):
        get_chat_model("sql", Settings(_env_file=None, model_sql=""))


def test_temperature_rejecting_model_falls_back_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    s = Settings(_env_file=None, model_sql="claude-sonnet-5-5", model_supervisor="claude-haiku-5-5")
    sonnet = get_chat_model("sql", s)
    haiku = get_chat_model("supervisor", s)
    assert getattr(sonnet, "temperature", None) in (None,)
    assert getattr(haiku, "temperature", None) == 0
