# ADR 0005: Output token cap of 1500

Date: 2026-11-07 · Status: accepted

## Context

`LLM_MAX_TOKENS` caps the output of each model call. With the original 600, the first paid
evaluation (`anthropic/claude-sonnet-5.5` via OpenRouter) returned the fallback answer on three
turns: two comparisons and the 3-phase "no modulating" explanation. In each one the final model
call used exactly 600 output tokens and produced no text, so the conversation service stopped the
turn with `empty_answer` (`backend/eval/results/2026-11-07-anthropic-claude-sonnet-5.5-7.json`).

With 1500 the same turns pass (`…-8.json`). Their final calls used 570 to 805 output tokens,
and one of them used about twice the tokens its visible text needs. That suggests the provider's
default reasoning spends part of the cap, but the raw response is not stored, so this is not
confirmed.

## Decision

- The default `LLM_MAX_TOKENS` is 1500. It stays configurable and bounded.
- No reasoning setting is forced on the model; the cap only has to leave room for it.

## Consequences

- A cap is not a price: a call pays for the tokens it uses, so short answers cost the same.
  Long answers (comparisons, explanations of blocking requirements) now complete instead of
  falling back, and cost more than the failed attempt.
- If a model spends even more on reasoning, `empty_answer` can reappear. The eval runner stores
  `internal_error` per turn to catch it.
