"""Settings: a real provider needs a model and its key; fake needs neither. No model ID in code."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_fake_is_the_default_and_needs_no_model() -> None:
    s = Settings(_env_file=None)
    assert s.llm_provider == "fake"
    assert s.agent_model is None


@pytest.mark.parametrize("provider", ["openrouter", "google"])
def test_real_provider_requires_model(provider) -> None:
    with pytest.raises(ValidationError, match="AGENT_MODEL"):
        Settings(llm_provider=provider, openrouter_api_key="k", google_api_key="k")


def test_blank_values_from_compose_count_as_unset() -> None:
    s = Settings(agent_model="", fallback_model="", openrouter_api_key="")
    assert s.agent_model is None and s.fallback_model is None and s.openrouter_api_key is None
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
