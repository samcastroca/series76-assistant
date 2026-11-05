"""`extract` stage (human-run, network): Gemini vision over the scanned data PDF.

Renders each page with pdfplumber, asks Gemini for each table twice under a strict JSON schema
generated from tables.yaml, and writes the raw runs to data/extracted/<table_id>.json plus a
cell-by-cell diff to data/extracted/discrepancies.md. Nothing else in the project calls this
module: Docker and the tests only use `build` and `load`. See docs/adr/0002."""

import io
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from ingest.extracted import (
    ExtractedTable,
    ExtractionRun,
    column_key,
    diff_runs,
    discrepancies_markdown,
    row_columns,
    run_schema,
)
from ingest.spec import Spec, TableSpec, load_spec

PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "series_76_electric_data.pdf"
RESOLUTION_DPI = 300
RUNS = 2
# Free-tier Gemini answers 503 under load; the SDK default gives up after ~15 s. 429 is not
# retried: on the free tier it usually means the daily request quota is spent, and retrying
# only burns more of it. Few attempts with long waits keep the request count low.
RETRY_ATTEMPTS = 4
RETRY_INITIAL_DELAY_S = 15.0
RETRY_MAX_DELAY_S = 120.0
RETRY_STATUS_CODES = [408, 500, 502, 503, 504]

# (prompt, png bytes, JSON schema) -> JSON text
Generate = Callable[[str, bytes, dict], str]

GROUP_LABELS = {
    "actuator": "",
    "on_off": "On/Off",
    "modulating": "Modulating",
    "unspecified": "(single, not split by service)",
    "hz60": "60 Hz",
    "hz50": "50 Hz",
    "single": "(single column, no frequency)",
}
FIELD_LABELS = {
    "part_number": "Base Part Number",
    "torque_inlb": "Output Torque, In-Lbs.",
    "torque_nm": "Output Torque, Nm",
    "duty_cycle_pct": "Duty Cycle, S4%",
    "cycles_per_hour": "Cycles per Hour",
    "starts_per_hour": "Starts per Hour",
    "speed_s": "Operating Speed (sec)",
    "fla_a": "Full Load Current (FLA) [A]",
    "lra_a": "Locked Rotor Current (LRA) [A]",
}


def build_prompt(spec: Spec, table: TableSpec) -> str:
    on_page = sum(1 for t in spec.tables if t.page == table.page)
    columns = "\n".join(
        f"  - `{column_key(t)}`: "
        + " ".join(filter(None, (GROUP_LABELS[t.group], FIELD_LABELS[t.field])))
        for t in row_columns(spec, table)
    )
    return f"""You are transcribing one table from a scanned datasheet page (image attached).
The page has {on_page} table(s). Transcribe only table number {table.index + 1} from the top,
captioned "{table.title}". It has {table.expected_rows} part-number rows.

Rules:
- Copy every cell exactly as printed, as a string. Keep thousands separators, decimal points,
  asterisks and any other marks. Do not convert units, round, compute, infer or fill in values.
- `rows`: one object per part number, top to bottom, with these keys:
{columns}
- Part numbers have some characters printed in bold. Write each part number as one continuous
  string with no spaces, including any trailing footnote mark such as "*".
- If a block of cells is printed as one merged "N/A", put "N/A" in every cell it covers.
- The Motor Power (Watts) column uses merged cells that span several rows; the value is printed
  centred in the merged cell. Do not put it in `rows`. List each merged cell in
  `motor_power_groups` with its `watts` and the `first_part_number` and `last_part_number` of
  the rows it spans (merged cells are delimited by horizontal rules). Rows printed as N/A have
  no Motor Power.
- `caption`: the caption printed just above the table header.
- If a cell is empty, use null. If a cell is hard to read, give your best reading; a human
  reviews every disagreement.
"""


def json_schema(spec: Spec, table: TableSpec) -> dict:
    schema = run_schema(spec, table).model_json_schema()
    schema["properties"]["rows"]["minItems"] = table.expected_rows
    schema["properties"]["rows"]["maxItems"] = table.expected_rows
    return schema


def extract_table(generate: Generate, spec: Spec, table: TableSpec, png: bytes) -> ExtractionRun:
    text = generate(build_prompt(spec, table), png, json_schema(spec, table))
    strict = run_schema(spec, table).model_validate_json(text)  # rejects unknown/missing keys
    return ExtractionRun.model_validate(strict.model_dump())


def render_pages(pdf_path: Path = PDF_PATH, dpi: int = RESOLUTION_DPI) -> dict[int, bytes]:
    import pdfplumber

    pages: dict[int, bytes] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            buffer = io.BytesIO()
            page.to_image(resolution=dpi).original.save(buffer, format="PNG")
            pages[number] = buffer.getvalue()
    return pages


def gemini_generate(model: str, api_key: str) -> Generate:
    from google import genai
    from google.genai import types

    retry = types.HttpRetryOptions(
        attempts=RETRY_ATTEMPTS,
        initial_delay=RETRY_INITIAL_DELAY_S,
        max_delay=RETRY_MAX_DELAY_S,
        http_status_codes=RETRY_STATUS_CODES,
    )
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=retry))

    def generate(prompt: str, png: bytes, schema: dict) -> str:
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text

    return generate


class ExtractError(Exception):
    pass


def run_extract(
    out_dir: Path,
    generate: Generate | None = None,
    model: str | None = None,
    pages: dict[int, bytes] | None = None,
    spec: Spec | None = None,
    force: bool = False,
) -> list:
    """Extracts every table twice and writes the raw runs and the discrepancy sheet.

    Resumable: a table whose file already exists is kept (and only diffed) unless `force`, so a
    failure halfway through does not repeat the tables already read."""
    spec = spec or load_spec()
    pending = [t for t in spec.tables if force or not (out_dir / f"{t.id}.json").exists()]
    if pending and generate is None:
        settings = get_settings()
        if not settings.ingest_llm_model or settings.google_api_key is None:
            raise SystemExit("extract needs INGEST_LLM_MODEL and GOOGLE_API_KEY in the environment")
        model = settings.ingest_llm_model
        generate = gemini_generate(model, settings.google_api_key.get_secret_value())
    if pending and pages is None:
        pages = render_pages()

    out_dir.mkdir(parents=True, exist_ok=True)
    all_diffs = []
    labels: dict[tuple[str, int], str] = {}
    for table in spec.tables:
        path = out_dir / f"{table.id}.json"
        if table in pending:
            try:
                runs = [
                    extract_table(generate, spec, table, pages[table.page]) for _ in range(RUNS)
                ]
            except Exception as exc:
                done = len(spec.tables) - len(pending) + pending.index(table)
                raise ExtractError(
                    f"{table.id} failed: {exc}\n{done} table(s) are saved in {out_dir}; "
                    "run `extract` again to resume from this table."
                ) from exc
            extracted = ExtractedTable(
                table_id=table.id,
                page=table.page,
                model=model or "unknown",
                extracted_at=datetime.now(UTC).isoformat(timespec="seconds"),
                runs=runs,
            )
            text = extracted.model_dump_json(indent=2) + "\n"
            path.write_text(text, encoding="utf-8", newline="\n")
            status = "extracted"
        else:
            extracted = ExtractedTable.model_validate_json(path.read_text("utf-8"))
            status = "kept (use --force to re-extract)"
        diffs = diff_runs(table.id, *extracted.runs)
        all_diffs += diffs
        for i, row in enumerate(extracted.runs[0].rows, start=1):
            labels[(table.id, i)] = row.get("part_number") or ""
        print(f"{table.id}: {status}, {len(extracted.runs[0].rows)} rows, {len(diffs)} diffs")
    (out_dir / "discrepancies.md").write_text(
        discrepancies_markdown(all_diffs, labels), encoding="utf-8", newline="\n"
    )
    return all_diffs
