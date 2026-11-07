"""Chat model construction from settings. Builds the provider clients with dummy keys and never
calls them: no network, no real key."""

import json
import subprocess
import sys
from pathlib import Path

from app.agent.factory import build_chat_model
from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]
DUMMY = "dummy-not-a-real-key"


def _settings(provider: str) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=provider,
        agent_model="some/model",
        openrouter_api_key=DUMMY,
        google_api_key=DUMMY,
        llm_timeout_s=60,
    )


def test_openrouter_timeout_is_in_milliseconds_and_output_bounded() -> None:
    # Regression: ChatOpenRouter's `timeout` is in milliseconds (SDK `timeout_ms`). Passing the
    # seconds value made every connection time out after 60 ms (httpx.ConnectTimeout -> 503).
    settings = _settings("openrouter")
    model = build_chat_model(settings, "some/model")
    assert model.request_timeout == 60_000
    assert model.temperature == 0 and model.max_tokens == settings.llm_max_tokens


def test_google_timeout_is_in_seconds_and_output_bounded() -> None:
    # In a subprocess: building the Gemini client imports google.genai, and the ingest tests
    # assert that the deterministic stages never load it in the test process.
    script = (
        "import json; from app.agent.factory import build_chat_model; "
        "from app.config import Settings; "
        f"s = Settings(llm_provider='google', agent_model='some/model', google_api_key='{DUMMY}', "
        "llm_timeout_s=60); m = build_chat_model(s, 'some/model'); "
        "print(json.dumps([m.timeout, m.temperature, m.max_output_tokens, s.llm_max_tokens]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, check=True
    )
    timeout, temperature, max_tokens, expected_tokens = json.loads(out.stdout.splitlines()[-1])
    assert timeout == 60
    assert temperature == 0 and max_tokens == expected_tokens
