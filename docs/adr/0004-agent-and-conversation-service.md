# ADR 0004: Agent, conversation service and response building

Date: 2026-11-06 · Status: accepted

## Context

The agent layer turns a chat message into an answer plus product cards. The rules that matter
most: numbers come only from tool results, `products` is never parsed from the LLM's text, no
internals reach the chat, conversations survive a restart, and the paid key is used sparingly.

## Decision

- **One agent** built with LangChain v1 `create_agent` over the three catalog tools. The tools
  reuse the existing typed schemas and return `content_and_artifact`: JSON for the model, and a
  typed artifact (`ToolOutput`) for the backend. `search_actuators` also carries the full specs
  of the products it shows, used only for the cards; the model and the validator see only the
  compact `SearchResult`.
- **Middleware** (in order): a dynamic system prompt with the session's active part numbers;
  per-model-call usage recording; `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` with
  `exit_behavior="error"` (the other behaviors append internal English text that would become the
  answer); a `wrap_tool_call` that records args, result, latency and errors and replaces raw
  failures with generic messages; `ModelFallbackMiddleware` when `FALLBACK_MODEL` is set.
- **Limits are per turn.** The built-in middleware counts per run, so context counters make the
  validator's retry share the first run's budget.
- **Deterministic response building.** `response_type`, `products`, `clarification` options,
  `highlight_fields`, `suggestions` and `warnings` are derived from the turn's typed tool
  results (`app/services/products.py`). No structured output is asked of the LLM; its text is
  reused only as the clarification question. A turn without tool calls is a `clarification`.
- **Numeric validator.** Every number in the answer must appear in a tool result of the turn or
  the session, or in the allowlist (50/60 Hz, 10 %, 5 %, sizes 1-7). Part numbers and the product
  name are masked from that check; each part number must instead be one that a tool result of
  the turn or the session contains (a short name such as "763C" counts when it is the start of
  one). Ambiguous notation ("1,500") is accepted if any reading is backed. On failure, or if the
  answer shows internals (a tool name, a snake_case field name of the tool schemas, or a JSON
  object), the agent is retried once with a note; if that fails, the unbacked numbers and part
  numbers are stripped (the turn gets `numbers_removed`) and the internals are removed.
  Known gaps, accepted: an allowlisted value (1-7, 10, 50, 60) that is also a catalog value is
  never flagged even if attributed to the wrong product; a number is accepted if it appears in
  any result of the session, not necessarily for the product the sentence names; numbers glued
  to letters ("AC220") are not read.
- **History** sent to the model is the plain text of the last `HISTORY_TURNS` turns. Tool results
  are not replayed; the session prompt lists the active and earlier part numbers, and the agent
  calls the tool again for any value. This keeps prompts small and stops the model from
  repeating numbers from history (history text never backs a number).
- **Persistence** uses our own tables (no LangGraph checkpointer). A turn reads in a short session,
  runs the agent with no connection held, and writes user message, answer, tool calls and usage
  in one transaction. Tools open their own short read-only sessions.
- **Failures.** A provider error is a 503: in an existing session the user message is stored with
  status `error` and the turn's trace (a successful retry of the same query reuses that message
  instead of duplicating it); on a new conversation nothing is stored, because the 503
  body (fixed error shape) cannot return a session id. A call limit or an empty answer gives a
  fixed bilingual fallback answer with `response_type = "error"`; the detail is kept in
  `payload.internal_error`, which only the trace endpoint returns.
- **Daily budget.** With `DAILY_BUDGET_USD` set, the service sums `llm_usage.cost_usd` of the
  current UTC day before running the agent and raises a 503 `budget_exceeded` once the cap is
  reached; nothing is stored, so the app retries like any other failed send. It is a spend
  brake, not a hard limit: turns already in flight when the cap is crossed still finish.
- **Fake provider.** `LLM_PROVIDER=fake` uses `RuleBasedFakeModel`, a LangChain chat model that
  emits tool calls by fixed rules and answers in Spanish without numbers. Tests use
  `ScriptedFakeModel`, which replays a step-by-step script (messages, callables or exceptions).

## Consequences

- The app always gets complete cards, even for search results, without an extra endpoint.
- A turn can cost up to `MODEL_CALL_LIMIT` model calls including the retry.
- The model sees no tool results from earlier turns, so follow-ups cost one more tool call.
- The fallback answer is fixed text, not in the user's language.
