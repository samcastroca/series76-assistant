# ADR 0008: Summary PDF as product context in the prompt

Date: 2026-11-08 · Status: accepted

## Context

`series_76_summary.pdf` explains what the Series 76 actuators are, the enclosures, the service
types and what each data field means. Nothing in the backend read it, so a question such as "what
is a modulating actuator?" or "what does LRA mean?" had no source, and the model could only
decline or answer from memory.

The summary is short (two pages) and disagrees with the data PDF in places: it names IECEx/ATEX
for explosionproof models (the tables list CSA), it lists three power supplies (the tables also
have 220 V 3-phase), and its example part number does not exist. It also contains numbers
(supply voltages, the rotation angle) that the numeric validator would strip from an answer,
because no tool result backs them.

## Decision

- The summary is rewritten by hand as `backend/app/agent/product_context.md` and appended to the
  system prompt on every model call (`middleware.base_system_prompt`), after the rules and before
  the per-session part numbers. `system_prompt.md` stays untouched.
- The rewrite carries no product values: no voltages, no angle ("quarter turn"), no part numbers.
  A test runs the file through the numeric validator with no tool results, so only the allowlist
  (50/60 Hz) can appear.
- Where the summary disagrees with the data, the context says so and points the model to the tool
  results: certifications come from the tool, never claim IECEx/ATEX, get supply options from the
  catalog tool.
- No retrieval or embeddings: the whole text fits in the prompt.

## Consequences

- Concept and field questions get an answer grounded in the provided documents.
- Every model call carries a few hundred more input tokens. The text is static and sits in the
  stable prefix of the prompt, so provider prompt caching can absorb part of it. The cost per turn
  in `backend/eval/results/` predates this change.
- The rewrite has to be kept in sync by hand if the summary changes.
