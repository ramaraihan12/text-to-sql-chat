from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The app only ever connects as the read-only role in DATABASE_URL.
    # ADMIN_DATABASE_URL is used by `sqlchat setup` to create tables, seed data and that role.
    database_url: str = "postgresql://sales_reader:sales_reader@localhost:5432/text_to_sql"
    admin_database_url: str = "postgresql://postgres:postgres@localhost:5432/text_to_sql"
    # Can only touch the daily_usage counter. Model-written SQL never runs on this connection.
    usage_database_url: str = "postgresql://usage_counter:usage_counter@localhost:5432/text_to_sql"

    llm_provider: str = "ollama"
    llm_model: str = "qwen3.5:9b"
    ollama_base_url: str = "http://localhost:11434"

    max_rows: int = 200
    statement_timeout_ms: int = 5000

    # A question costs up to three model calls, so these bound what the chat UI can spend.
    max_question_chars: int = 300
    session_question_limit: int = 10
    daily_question_limit: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
