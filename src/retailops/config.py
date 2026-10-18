"""Environment-driven settings. Variable names match .env.example."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    model_supervisor: str = ""
    model_sql: str = ""
    model_judge: str = ""

    duckdb_path: str = "data/retailops.duckdb"
    audit_db_path: str = "data/audit.duckdb"
    vectorstore_path: str = "data/vectorstore"
    checkpoint_db_path: str = "data/checkpoints.sqlite"

    max_steps: int = 25
    max_tokens_per_run: int = 60000
    daily_budget_usd: float = 5.0
    rate_limit_per_min: int = 20
    app_env: str = "dev"


def get_settings() -> Settings:
    return Settings()
