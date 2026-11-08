"""Settings: a real provider needs a model and its key; fake needs neither. No model ID in code.
The app refuses to start without a strong enough JWT_SECRET."""

import pytest
from pydantic import ValidationError

from app.agent.fake import ScriptedFakeModel
from app.config import Settings
from app.main import create_app
from app.security import MIN_SECRET_LENGTH


def test_fake_is_the_default_and_needs_no_model() -> None:
    s = Settings(_env_file=None)
    assert s.llm_provider == "fake"
    assert s.agent_model is None


@pytest.mark.parametrize("provider", ["openrouter", "google"])
def test_real_provider_requires_model(provider) -> None:
    with pytest.raises(ValidationError, match="AGENT_MODEL"):
        Settings(llm_provider=provider, openrouter_api_key="k", google_api_key="k")


def test_blank_values_from_compose_count_as_unset() -> None:
    s = Settings(agent_model="", fallback_model="", openrouter_api_key="", daily_budget_usd="")
    assert s.agent_model is None and s.fallback_model is None and s.openrouter_api_key is None
    assert s.daily_budget_usd is None
    assert Settings(daily_budget_usd=0).daily_budget_usd == 0  # zero is a cap, not unset
    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY"):
        Settings(llm_provider="openrouter", agent_model="some/model", openrouter_api_key="")


@pytest.mark.parametrize(
    ("provider", "key_name"), [("openrouter", "OPENROUTER_API_KEY"), ("google", "GOOGLE_API_KEY")]
)
def test_real_provider_requires_its_key(provider, key_name, monkeypatch) -> None:
    for name in ("OPENROUTER_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError, match=key_name):
        Settings(llm_provider=provider, agent_model="some/model")


@pytest.mark.parametrize("secret", [None, "", "x" * (MIN_SECRET_LENGTH - 1)])
def test_app_refuses_a_missing_or_short_jwt_secret(secret) -> None:
    settings = Settings(_env_file=None, jwt_secret=secret)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        create_app(settings, ScriptedFakeModel(steps=[]))


def test_blank_seed_passwords_and_jwt_secret_count_as_unset() -> None:
    s = Settings(_env_file=None, jwt_secret="", seed_developer_password="")
    assert s.jwt_secret is None and s.seed_developer_password is None


def test_blank_ingest_model_and_key_count_as_unset() -> None:
    s = Settings(_env_file=None, ingest_llm_model="", ingest_openrouter_api_key="")
    assert s.ingest_llm_model is None and s.ingest_openrouter_api_key is None
