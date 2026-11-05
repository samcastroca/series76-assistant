"""The extract stage with a fake `generate`: no network, no Gemini SDK, made-up cell values."""

import json
import sys
from pathlib import Path

import pydantic
import pytest

from ingest.extract import ExtractError, build_prompt, extract_table, json_schema, run_extract
from ingest.extracted import ExtractedTable, column_key, row_columns
from ingest.spec import load_spec

SPEC = load_spec()
TABLE = next(t for t in SPEC.tables if t.id == "xp_24v_modulating")


def _answer(rows: int = TABLE.expected_rows, cell: str = "1", **extra) -> str:
    keys = [column_key(t) for t in row_columns(SPEC, TABLE)]
    return json.dumps(
        {
            "caption": TABLE.title,
            "rows": [{k: cell for k in keys} | extra for _ in range(rows)],
            "motor_power_groups": [],
        }
    )


def test_prompt_names_the_table_and_every_column() -> None:
    prompt = build_prompt(SPEC, TABLE)
    assert TABLE.title in prompt and f"{TABLE.expected_rows} part-number rows" in prompt
    for t in row_columns(SPEC, TABLE):
        assert f"`{column_key(t)}`" in prompt
    assert "motor_power_w" not in prompt


def test_schema_pins_row_count_and_forbids_extra_keys() -> None:
    schema = json_schema(SPEC, TABLE)
    assert schema["properties"]["rows"]["minItems"] == TABLE.expected_rows
    assert schema["properties"]["rows"]["maxItems"] == TABLE.expected_rows
    row_def = next(d for name, d in schema["$defs"].items() if name == "Row")
    assert row_def["additionalProperties"] is False


def test_extract_table_parses_and_rejects_unknown_keys() -> None:
    run = extract_table(lambda *_: _answer(), SPEC, TABLE, b"png")
    assert len(run.rows) == TABLE.expected_rows
    with pytest.raises(pydantic.ValidationError):
        extract_table(lambda *_: _answer(surprise="x"), SPEC, TABLE, b"png")


def test_run_extract_writes_two_runs_and_discrepancies(tmp_path: Path) -> None:
    calls: list[str] = []

    def generate(prompt: str, png: bytes, schema: dict) -> str:
        calls.append(prompt)
        # Answer whatever table the schema describes; the second run of the first table reads
        # every cell differently.
        keys = list(schema["$defs"]["Row"]["properties"])
        cell = "2" if len(calls) == 2 else "1"
        rows = [{k: cell for k in keys} for _ in range(schema["properties"]["rows"]["minItems"])]
        caption = next(t.title for t in SPEC.tables if f'"{t.title}"' in prompt)
        return json.dumps({"caption": caption, "rows": rows, "motor_power_groups": []})

    pages = {t.page: b"png" for t in SPEC.tables}
    diffs = run_extract(tmp_path, generate=generate, model="fake", pages=pages, spec=SPEC)

    assert len(calls) == 2 * len(SPEC.tables)
    for t in SPEC.tables:
        saved = ExtractedTable.model_validate_json((tmp_path / f"{t.id}.json").read_text("utf-8"))
        assert saved.model == "fake" and len(saved.runs) == 2
    first = SPEC.tables[0].id
    assert diffs and {d.table_id for d in diffs} == {first}
    assert first in (tmp_path / "discrepancies.md").read_text("utf-8")


def test_gemini_sdk_not_imported() -> None:
    assert "google.genai" not in sys.modules


def _schema_answer(prompt: str, schema: dict, cell: str = "1") -> str:
    keys = list(schema["$defs"]["Row"]["properties"])
    rows = [{k: cell for k in keys} for _ in range(schema["properties"]["rows"]["minItems"])]
    caption = next(t.title for t in SPEC.tables if f'"{t.title}"' in prompt)
    return json.dumps({"caption": caption, "rows": rows, "motor_power_groups": []})


def test_run_extract_resumes_after_a_failure(tmp_path: Path) -> None:
    calls: list[str] = []
    fail_on = 2 * 3 + 1  # first call of the fourth table

    def flaky(prompt: str, png: bytes, schema: dict) -> str:
        calls.append(prompt)
        if len(calls) == fail_on:
            raise RuntimeError("503 UNAVAILABLE")
        return _schema_answer(prompt, schema)

    pages = {t.page: b"png" for t in SPEC.tables}
    with pytest.raises(ExtractError, match=r"3 table\(s\) are saved"):
        run_extract(tmp_path, generate=flaky, model="fake", pages=pages, spec=SPEC)
    assert len(list(tmp_path.glob("*.json"))) == 3

    calls.clear()
    run_extract(
        tmp_path,
        generate=lambda p, _, s: calls.append(p) or _schema_answer(p, s),
        model="fake",
        pages=pages,
        spec=SPEC,
    )
    assert len(calls) == 2 * (len(SPEC.tables) - 3)  # only the missing tables

    calls.clear()
    run_extract(
        tmp_path,
        generate=lambda p, _, s: calls.append(p) or _schema_answer(p, s),
        model="fake",
        pages=pages,
        spec=SPEC,
        force=True,
    )
    assert len(calls) == 2 * len(SPEC.tables)
