from typing import Any

import pytest
from langchain_openai import ChatOpenAI

import sqlchat.llm as llm
from sqlchat.config import Settings
from sqlchat.llm import build_chat_model

MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_ollama_stays_the_default_provider() -> None:
    assert settings().llm_provider == "ollama"


def test_cloudflare_points_the_openai_client_at_workers_ai() -> None:
    model = build_chat_model(
        settings(
            llm_provider="cloudflare",
            llm_model=MODEL,
            cf_api_token="secret",
            cf_account_id="abc123",
        )
    )
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == MODEL
    assert model.openai_api_base == "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "secret"
    assert model.temperature == 0
    assert model.max_retries == 2


@pytest.mark.parametrize(
    "missing",
    [{"cf_api_token": None}, {"cf_account_id": None}, {"cf_account_id": ""}],
)
def test_cloudflare_without_credentials_fails_with_a_clear_message(missing: dict) -> None:
    values: dict[str, Any] = {
        "llm_provider": "cloudflare",
        "llm_model": MODEL,
        "cf_api_token": "secret",
        "cf_account_id": "abc123",
    } | missing
    with pytest.raises(ValueError, match="CF_API_TOKEN and CF_ACCOUNT_ID"):
        build_chat_model(settings(**values))


def test_the_token_stays_out_of_the_settings_repr() -> None:
    assert "secret-token" not in repr(settings(cf_api_token="secret-token"))


def test_cloudflare_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CF_API_TOKEN", "from-env")
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    loaded = settings()
    assert loaded.llm_provider == "cloudflare"
    assert loaded.cf_account_id == "acct"
    assert loaded.cf_api_token is not None
    assert loaded.cf_api_token.get_secret_value() == "from-env"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("ollama", {"base_url": "http://localhost:11434", "reasoning": False}),
        ("google_genai", {"timeout": 30, "max_retries": 2}),
    ],
)
def test_other_providers_keep_their_own_options(
    monkeypatch: pytest.MonkeyPatch, provider: str, expected: dict
) -> None:
    seen: dict[str, Any] = {}

    def fake_init(name: str, model_provider: str, **kwargs: Any) -> str:
        seen.update(name=name, provider=model_provider, **kwargs)
        return "model"

    monkeypatch.setattr(llm, "init_chat_model", fake_init)
    build_chat_model(settings(llm_provider=provider, llm_model="m"))
    assert seen["provider"] == provider
    assert seen["name"] == "m"
    assert seen["temperature"] == 0
    for key, value in expected.items():
        assert seen[key] == value
