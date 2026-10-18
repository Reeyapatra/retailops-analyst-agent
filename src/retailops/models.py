"""Provider-agnostic chat model factory driven by environment variables."""

from __future__ import annotations

from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from retailops.config import ConfigError, Settings, get_settings
from retailops.observability import log_event

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
    if _rejects_temperature(model):
        # Some models reject non-default temperature. Fall back to the provider default and
        # say so, because docs/02 asks for temperature 0 wherever the model allows it.
        log_event("temperature_not_supported", model=name, role=role)
        model = init_chat_model(name, model_provider=s.llm_provider)
        assert isinstance(model, BaseChatModel)
    return model


def _rejects_temperature(model: BaseChatModel) -> bool:
    """Detect, without a network call, whether the provider package rejects temperature=0.

    langchain-anthropic validates the request payload locally; building a payload surfaces
    the error before any request is sent. Relies on a private method, so any other failure
    mode is treated as 'accepted' and left to surface on the first real call."""
    build = getattr(model, "_get_request_payload", None)
    if build is None:
        return False
    try:
        build([HumanMessage("probe")])
    except ValueError as e:
        return "temperature" in str(e)
    except Exception:
        return False
    return False
