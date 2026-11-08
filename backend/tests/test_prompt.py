"""System prompt: the summary's product context reaches the model and carries no product values."""

from app.agent.middleware import (
    PRODUCT_CONTEXT_PATH,
    SYSTEM_PROMPT_PATH,
    base_system_prompt,
    render_system_prompt,
)
from app.agent.validator import unsupported_numbers, unsupported_part_numbers

CONTEXT = PRODUCT_CONTEXT_PATH.read_text(encoding="utf-8")


def test_prompt_holds_the_rules_then_the_product_context() -> None:
    prompt = base_system_prompt()
    rules = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    assert prompt.startswith(rules)
    assert prompt.index("## Product context") > len(rules)


def test_product_context_is_sent_with_every_turn() -> None:
    assert "## Product context" in render_system_prompt(["X"], ["X"])
    assert "## Product context" in render_system_prompt([], [])


def test_product_context_has_no_product_values() -> None:
    # With no tool results, only the validator's allowlist (50/60 Hz, tolerances, sizes) passes:
    # a number here is one the agent could repeat without a tool backing it.
    assert unsupported_numbers(CONTEXT, []) == []
    assert unsupported_part_numbers(CONTEXT, []) == []
