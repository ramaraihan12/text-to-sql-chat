from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The app only ever connects as the read-only role in DATABASE_URL.
    # ADMIN_DATABASE_URL is used by `sqlchat setup` to create tables, seed data and that role.
    database_url: str = "postgresql://sales_reader:sales_reader@localhost:5432/text_to_sql"
    admin_database_url: str = "postgresql://postgres:postgres@localhost:5432/text_to_sql"

    llm_provider: str = "ollama"
    llm_model: str = "qwen3.5:9b"
    ollama_base_url: str = "http://localhost:11434"

    max_rows: int = 200
    statement_timeout_ms: int = 5000


@lru_cache
def get_settings() -> Settings:
    return Settings()
