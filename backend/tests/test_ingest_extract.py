"""The extract stage with a fake `generate`: no network, no Gemini SDK, made-up cell values."""

import json
import sys
from pathlib import Path

import pydantic
import pytest

from app.config import Settings
from ingest.extract import (
    ExtractError,
    build_prompt,
    extract_table,
    generator_from_settings,
    json_schema,
    openrouter_payload,
    portable_schema,
    run_extract,
)
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


def test_schema_has_no_item_limits_and_forbids_extra_keys() -> None:
    schema = json_schema(SPEC, TABLE)
    # Google AI Studio rejects minItems/maxItems with 400 INVALID_ARGUMENT (regression).
    assert "minItems" not in json.dumps(schema) and "maxItems" not in json.dumps(schema)
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
        rows = [{k: cell for k in keys} for _ in range(_table_in(prompt).expected_rows)]
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


def _table_in(prompt: str):
    return next(
        t for t in SPEC.tables if f'"{t.title}"' in prompt and f"{t.expected_rows} part" in prompt
    )


def _schema_answer(prompt: str, schema: dict, cell: str = "1") -> str:
    keys = list(schema["$defs"]["Row"]["properties"])
    rows = [{k: cell for k in keys} for _ in range(_table_in(prompt).expected_rows)]
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


# --- provider selection --------------------------------------------------------------------


def test_openrouter_payload_sends_image_prompt_and_strict_schema() -> None:
    schema = json_schema(SPEC, TABLE)
    payload = openrouter_payload("vendor/model", "the prompt", b"\x89PNG", schema)
    assert payload["model"] == "vendor/model" and payload["temperature"] == 0
    image, text = payload["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert text == {"type": "text", "text": "the prompt"}
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == portable_schema(schema)
    assert payload["provider"] == {"require_parameters": True}


def test_openrouter_uses_its_own_key_never_openrouter_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "agent-key")
    settings = Settings(ingest_llm_provider="openrouter", ingest_llm_model="vendor/model")
    with pytest.raises(SystemExit, match="INGEST_OPENROUTER_API_KEY"):
        generator_from_settings(settings)

    settings = Settings(
        ingest_llm_provider="openrouter",
        ingest_llm_model="vendor/model",
        ingest_openrouter_api_key="ingest-key",
    )
    label, generate = generator_from_settings(settings)
    assert label == "openrouter:vendor/model" and callable(generate)


def test_google_provider_needs_google_key() -> None:
    with pytest.raises(SystemExit, match="GOOGLE_API_KEY"):
        generator_from_settings(Settings(ingest_llm_model="m", google_api_key=None))


def test_portable_schema_is_plain_but_same_shape() -> None:
    strict = json_schema(SPEC, TABLE)
    plain = portable_schema(strict)
    text = json.dumps(plain)
    for keyword in ("$ref", "$defs", "anyOf", "additionalProperties", "title", "null"):
        assert keyword not in text
    rows = plain["properties"]["rows"]
    row = rows["items"]
    assert list(row["properties"]) == [column_key(t) for t in row_columns(SPEC, TABLE)]
    assert row["required"] == list(row["properties"])
    assert {p["type"] for p in row["properties"].values()} == {"string"}
    group = plain["properties"]["motor_power_groups"]["items"]
    assert set(group["required"]) == {"watts", "first_part_number", "last_part_number"}
