---
name: add-eval-case
description: Turn an observed agent failure, a new edge case, or a new tool behavior into a case in the evaluation dataset. Use when the user reports a wrong answer, when a manual check fails, or when a tool is added or changed.
---

# Add an evaluation case

The eval dataset is how we prove the agent works on cases beyond our own examples. Every real failure becomes a case **before** it is fixed, so the fix is measurable and regressions are caught.

## Files

- `backend/eval/cases_manual.jsonl`: hand-written cases (edge cases, traps, multi-turn). One JSON object per line.
- `backend/eval/generate_cases.py`: generates template cases from `backend/data/processed/catalog.json`.
- `backend/eval/cases.jsonl`: generated output (manual + templated). Never edit by hand; regenerate.

## Case format

```json
{
  "id": "not-found-summary-example",
  "category": "invalid_part",
  "turns": ["What is the torque of 761A00-11380000/A?"],
  "expected": [
    {
      "tools": ["get_actuator"],
      "tool_args": [{"tool": "get_actuator", "args": {"part_number": "761A00-11380000/A"}}],
      "response_type": "not_found",
      "part_numbers": [],
      "ordered": false,
      "coreference": false,
      "must_not_contain_specs": true
    }
  ],
  "notes": "Example from the summary PDF; it does not exist in the data tables."
}
```

- `category`: one of `detail`, `search`, `comparison`, `multi_turn`, `invalid_part`, `missing_data`, `ambiguous`, `no_match`, `certification_tolerance`, `out_of_scope`.
- `expected` has one entry per turn.
- `tools`: tools that must be called (repeat a name to require several calls); `[]` means no tool call; `null` means not checked.
- `tool_args`: each entry must match a distinct call of that tool on the listed arguments (part numbers normalized, numbers compared by value).
- `part_numbers` lists exactly the products in `products` (order-insensitive unless `ordered: true`); `null` means not checked (e.g. closest alternatives).
- `alternatives` (optional): other accepted outcomes, each `{"response_type", "part_numbers"}`; the turn passes those two metrics if the primary or any alternative matches as a whole.
- `must_not_contain_specs: true` (not found, no published data): no number with a spec unit (in-lb, Nm, s, A, W, %) may appear; voltages, frequencies and sizes are allowed.
- `coreference: true` on a follow-up turn whose tool arguments must target the product from earlier turns.
- `fast` is not set in `cases_manual.jsonl`: the ~20 cases used by `make eval-fast` are listed in `FAST_IDS` in `generate_cases.py`, which must name exactly 20.

## Steps

1. **Reproduce** the failure and write down the exact user turns. Keep the user's wording, including typos or mixed languages.
2. **Classify** it: is the bug in ingestion, a tool, the prompt or tool descriptions, the validator, or the products builder? Write that in `notes`.
3. **Expected values come from data, not memory.**
   - If the case depends on catalog values, add a template to `generate_cases.py` that computes them from `catalog.json`, or reference part numbers only (the runner compares `products` against the case's `part_numbers`).
   - Never type torque, current, speed or power values into a case by hand.
4. **Check for duplicates** with the same intent; prefer adding a new wording to an existing case's family over a near-copy.
5. **Add a negative twin** when useful: a similar query that must *not* trigger the behavior (for example, an unambiguous part number must not trigger a clarification).
6. **Regenerate**: `make eval-cases` (`tests/test_eval_cases.py` fails if `cases.jsonl` is stale).
7. **Run the free check only**: `make eval-fast` against an API started with `LLM_PROVIDER=google` or `fake` (the runner logs in as the developer demo user to read traces); the runner refuses `openrouter` without `--confirm-paid`. Never run `make eval-full`; the human runs paid evaluations.
8. If the case reveals a deterministic bug (tool, ingestion, validator), also add a unit test in `backend/tests/` that fails before the fix.

## Done when

- [ ] Case added with `id`, `category`, `turns`, per-turn `expected`, and `notes`.
- [ ] No hand-typed catalog numbers.
- [ ] `cases.jsonl` regenerated.
- [ ] Unit test added if the root cause is deterministic code.
