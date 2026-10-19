from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from sqlchat.config import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Chat model for the configured provider. Ollama is the default and needs no API key."""
    kwargs: dict[str, Any] = {"temperature": 0}
    if settings.llm_provider == "ollama":
        # Qwen 3.x and similar models think out loud by default. For short SQL that only
        # costs time on a small GPU, so it's switched off.
        kwargs |= {"base_url": settings.ollama_base_url, "reasoning": False}
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)
