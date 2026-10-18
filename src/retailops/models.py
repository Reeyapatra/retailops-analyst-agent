"""Provider-agnostic chat model factory driven by environment variables."""

from __future__ import annotations

from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from retailops.config import ConfigError, Settings, get_settings

Role = Literal["supervisor", "sql", "judge"]


def get_chat_model(role: Role, settings: Settings | None = None) -> BaseChatModel:
    """Build the chat model for a role. Raises ConfigError if the model name is not set."""
    s = settings or get_settings()
    name = {"supervisor": s.model_supervisor, "sql": s.model_sql, "judge": s.model_judge}[role]
    if not name:
        raise ConfigError(
            f"No model configured for role '{role}'. Set MODEL_{role.upper()} in .env "
            "(see .env.example) together with the provider API key."
        )
    model = init_chat_model(name, model_provider=s.llm_provider, temperature=0)
    assert isinstance(model, BaseChatModel)
    return model
