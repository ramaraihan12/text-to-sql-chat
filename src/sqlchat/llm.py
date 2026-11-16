from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from sqlchat.config import Settings

CLOUDFLARE = "cloudflare"
_CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Chat model for the configured provider. Ollama is the default and needs no API key."""
    kwargs: dict[str, Any] = {"temperature": 0}
    if settings.llm_provider == CLOUDFLARE:
        return _cloudflare(settings)
    if settings.llm_provider == "ollama":
        # Qwen 3.x and similar models think out loud by default. For short SQL that only
        # costs time on a small GPU, so it's switched off.
        kwargs |= {"base_url": settings.ollama_base_url, "reasoning": False}
    else:
        # The client defaults allow a long hang and six retries, all on a metered API.
        kwargs |= {"timeout": 30, "max_retries": 2}
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)


def _cloudflare(settings: Settings) -> BaseChatModel:
    """Workers AI speaks the OpenAI chat API, so the OpenAI client works with a different URL."""
    if settings.cf_api_token is None or not settings.cf_account_id:
        raise ValueError("LLM_PROVIDER=cloudflare needs CF_API_TOKEN and CF_ACCOUNT_ID to be set")
    return init_chat_model(
        settings.llm_model,
        model_provider="openai",
        base_url=_CLOUDFLARE_URL.format(account_id=settings.cf_account_id),
        api_key=settings.cf_api_token,
        temperature=0,
        # A SQL query or one sentence is far below this. It only caps a runaway reply, which
        # would otherwise eat into the shared daily allowance.
        max_tokens=600,
        timeout=30,
        max_retries=2,
    )
