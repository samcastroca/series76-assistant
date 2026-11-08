"""Run the evaluation cases against the running API and score them.

The agent runs inside the API, so the model that is billed is the server's: /health reports it.
Tool calls and LLM usage come from GET /api/messages/{id}/trace, which needs the developer role:
the runner logs in with EVAL_EMAIL / EVAL_PASSWORD (a developer account) and sends that token on
every request. Paid providers (openrouter) need --confirm-paid; the human runs those.

    uv run python -m eval.run_eval --fast              # 20 critical cases
    uv run python -m eval.run_eval --category search
    uv run python -m eval.run_eval --confirm-paid      # full suite, paid provider: HUMAN ONLY

Prints a table per category and writes eval/results/<date>-<model>.json. With
LANGSMITH_TRACING=true it also uploads the dataset and logs the run as a LangSmith experiment
(from the scores already computed; the agent is not called again).
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from eval import metrics
from eval.generate_cases import CASES_PATH, EVAL_DIR

RESULTS_DIR = EVAL_DIR / "results"
PAID_PROVIDERS = {"openrouter"}
DATASET_PREFIX = "series76-eval"


class EvalSettings(BaseSettings):
    """The runner's own settings (environment only; no .env file is read)."""

    model_config = SettingsConfigDict(extra="ignore")

    llm_provider: str | None = None
    langsmith_tracing: bool = False
    eval_api_url: str = "http://localhost:8000"
    eval_timeout_s: float = 180.0
    eval_email: str = "developer@example.com"
    eval_password: SecretStr | None = None


class EvalError(Exception):
    """The run cannot start or continue (API down, login failed, not a developer account)."""


# --- Cases -------------------------------------------------------------------------------------


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select(
    cases: list[dict[str, Any]],
    fast: bool = False,
    categories: list[str] | None = None,
    ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    out = [c for c in cases if not fast or c["fast"]]
    if categories:
        out = [c for c in out if c["category"] in categories]
    if ids:
        out = [c for c in out if c["id"] in ids]
    return out


# --- Guards ------------------------------------------------------------------------------------


def paid_guard(
    local_provider: str | None, server_provider: str | None, confirmed: bool
) -> str | None:
    """An error message when a paid provider would be used without --confirm-paid. A server
    that does not report its provider is treated as possibly paid."""
    if confirmed:
        return None
    if server_provider is None:
        return "The API did not report its LLM provider. Re-run with --confirm-paid (human only)."
    paid = {p for p in (local_provider, server_provider) if p in PAID_PROVIDERS}
    if paid:
        return (
            f"LLM provider {', '.join(sorted(paid))} is paid. Re-run with --confirm-paid "
            "(human only)."
        )
    return None


class DeveloperAuth(httpx.Auth):
    """Bearer auth for a developer account (the trace endpoint needs that role). Logs in on the
    first request and again, once, when a token expires mid-run (a long paid run can outlive
    JWT_EXPIRE_MINUTES)."""

    def __init__(self, email: str, password: SecretStr | None) -> None:
        if password is None:
            raise EvalError("Set EVAL_PASSWORD (and EVAL_EMAIL) to a developer account.")
        self.email = email
        self.password = password
        self.token: str | None = None

    def auth_flow(self, request: httpx.Request):
        if request.url.path == "/api/auth/login":
            yield request
            return
        if self.token is None:
            yield from self._login(request)
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code == 401 and _error_code(response) == "token_expired":
            yield from self._login(request)
            request.headers["Authorization"] = f"Bearer {self.token}"
            yield request

    def _login(self, request: httpx.Request):
        body = {"email": self.email, "password": self.password.get_secret_value()}
        response = yield httpx.Request("POST", request.url.join("/api/auth/login"), json=body)
        response.read()
        if response.status_code != 200:
            code = _error_code(response) or response.status_code
            raise EvalError(f"Login as {self.email} failed ({code}).")
        data = response.json()
        if data["user"]["role"] != "developer":
            raise EvalError(
                f"{self.email} is not a developer account; the trace endpoint needs one."
            )
        self.token = data["access_token"]


def _error_code(response: httpx.Response) -> str | None:
    response.read()
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def authenticate(client: httpx.Client, email: str, password: SecretStr | None) -> None:
    """Attach DeveloperAuth and log in now, so a bad account stops the run before any case."""
    client.auth = DeveloperAuth(email, password)
    client.get("/api/auth/me").raise_for_status()


# --- Running -----------------------------------------------------------------------------------


def run_case(client: httpx.Client, case: dict[str, Any]) -> dict[str, Any]:
    session_id: str | None = None
    session_results: list[Any] = []
    turns: list[dict[str, Any]] = []
    for query, expected in zip(case["turns"], case["expected"], strict=True):
        body: dict[str, Any] = {"query": query}
        if session_id:
            body["session_id"] = session_id
        start = time.perf_counter()
        try:
            r = client.post("/api/conversation", json=body)
            error = None if r.status_code == 200 else r.json().get("error", {}).get("code")
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: a non-JSON error body
            r, error = None, type(exc).__name__
        latency_ms = round((time.perf_counter() - start) * 1000)
        if error or r is None:
            turns.append(
                {
                    "query": query,
                    "error": error or "http_error",
                    "latency_ms": latency_ms,
                    "scores": metrics.failed_turn(expected),
                    "usage": [],
                }
            )
            break  # later turns depend on this one
        response = r.json()
        session_id = response["session_id"]
        trace = client.get(f"/api/messages/{response['message_id']}/trace").json()
        calls = trace.get("tool_calls", [])
        session_results.extend(c["result"] for c in calls if c.get("result") is not None)
        turns.append(
            {
                "query": query,
                "answer": response["answer"],
                "response_type": response["response_type"],
                "products": [p["part_number"] for p in response.get("products", [])],
                "warnings": response.get("warnings", []),
                "tool_calls": [{"tool": c["tool_name"], "args": c["args"]} for c in calls],
                "usage": trace.get("usage", []),
                # Why the backend answered with its fallback (call limit, empty answer).
                "internal_error": trace.get("payload", {}).get("internal_error"),
                "latency_ms": latency_ms,
                "scores": metrics.score_turn(expected, response, calls, session_results),
            }
        )
    # Unanswered turns (after a failure) count as failed.
    for expected in case["expected"][len(turns) :]:
        turns.append(
            {
                "error": "skipped",
                "latency_ms": None,
                "usage": [],
                "scores": metrics.failed_turn(expected),
            }
        )
    if session_id:
        client.delete(f"/api/sessions/{session_id}")
    return {
        "id": case["id"],
        "category": case["category"],
        "fast": case["fast"],
        "passed": all(metrics.passed(t["scores"]) for t in turns),
        "turns": turns,
    }


# --- Output ------------------------------------------------------------------------------------

_SHORT = {
    "tool_selection": "tools",
    "argument_accuracy": "args",
    "expected_part_numbers": "parts",
    "response_type": "type",
    "numeric_faithfulness": "nums",
    "coreference": "coref",
    "answer_format": "format",
}


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def format_table(summary: dict[str, Any], categories: dict[str, dict[str, Any]]) -> str:
    header = ["category", "cases", "pass", *(_SHORT[m] for m in metrics.METRICS), "p95 ms"]
    rows = []
    for name, s in [*categories.items(), ("TOTAL", summary)]:
        p95 = s["latency_ms_p95"]
        rows.append(
            [
                name,
                str(s["cases"]),
                _pct(s["pass_rate"]),
                *(_pct(s["metrics"][m]) for m in metrics.METRICS),
                "-" if p95 is None else str(p95),
            ]
        )
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]

    def line(cells: list[str]) -> str:
        return "  ".join(
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(cells, widths, strict=True))
        )

    sep = "  ".join("-" * w for w in widths)
    out = [line(header), sep, *(line(r) for r in rows[:-1]), sep, line(rows[-1])]
    cost = summary["cost_usd_per_turn"]
    out.append(
        f"latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms; "
        f"cost per turn {'n/a (not reported)' if cost is None else '$' + cost}; "
        f"tokens per turn {summary['tokens_per_turn'] or 'n/a'}"
    )
    return "\n".join(out)


def result_path(model: str, today: date, directory: Path = RESULTS_DIR) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "unknown"
    path = directory / f"{today.isoformat()}-{slug}.json"
    n = 2
    while path.exists():
        path = directory / f"{today.isoformat()}-{slug}-{n}.json"
        n += 1
    return path


# --- LangSmith (optional) ----------------------------------------------------------------------


def log_to_langsmith(cases: list[dict[str, Any]], results: list[dict[str, Any]], model: str) -> str:
    """Upload the dataset (named by its content hash, so a changed dataset gets a new name) and
    record the precomputed results as an experiment."""
    from langsmith import Client

    client = Client()
    payload = json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
    name = f"{DATASET_PREFIX}-{hashlib.sha256(payload).hexdigest()[:8]}"
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description="Series 76 agent evaluation cases")
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs": {"id": c["id"], "category": c["category"], "turns": c["turns"]},
                    "outputs": {"expected": c["expected"]},
                    "metadata": {"fast": c["fast"], "notes": c.get("notes", "")},
                }
                for c in cases
            ],
        )
    by_id = {r["id"]: r for r in results}
    examples = [e for e in client.list_examples(dataset_name=name) if e.inputs["id"] in by_id]

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return by_id[inputs["id"]]  # already run: the agent is not called again

    def evaluator(metric: str):
        def evaluate(outputs: dict[str, Any]) -> dict[str, Any]:
            return {"key": metric, "score": metrics.metric_rates(outputs["turns"])[metric]}

        evaluate.__name__ = metric
        return evaluate

    def case_passed(outputs: dict[str, Any]) -> dict[str, Any]:
        return {"key": "passed", "score": outputs["passed"]}

    experiment = client.evaluate(
        target,
        data=examples,
        evaluators=[case_passed, *(evaluator(m) for m in metrics.METRICS)],
        experiment_prefix=model,
        metadata={"model": model},
    )
    return experiment.experiment_name


# --- CLI ---------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Series 76 agent evaluation via the API.")
    p.add_argument("--fast", action="store_true", help="only the cases marked fast")
    p.add_argument("--category", action="append", help="only this category (repeatable)")
    p.add_argument("--ids", nargs="+", help="only these case ids")
    p.add_argument("--api-url", help="API base URL (default: EVAL_API_URL or localhost:8000)")
    p.add_argument("--confirm-paid", action="store_true", help="allow a paid LLM provider")
    p.add_argument("--out", type=Path, help="results file (default: eval/results/<date>-<model>)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = parse_args(argv)
    settings = EvalSettings()
    base_url = args.api_url or settings.eval_api_url

    with httpx.Client(
        base_url=base_url, timeout=settings.eval_timeout_s, transport=transport
    ) as client:
        try:
            health = client.get("/health").json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"API not reachable at {base_url}: {exc}", file=sys.stderr)
            return 1
        server_provider = health.get("llm_provider")
        if message := paid_guard(settings.llm_provider, server_provider, args.confirm_paid):
            print(message, file=sys.stderr)
            return 2
        model = health.get("agent_model") or server_provider or "unknown"

        all_cases = load_cases()
        if unknown := sorted(set(args.ids or []) - {c["id"] for c in all_cases}):
            print(f"Unknown case ids: {', '.join(unknown)}", file=sys.stderr)
            return 1
        cases = select(all_cases, args.fast, args.category, args.ids)
        if not cases:
            print("No cases selected.", file=sys.stderr)
            return 1
        try:
            authenticate(client, settings.eval_email, settings.eval_password)
        except (EvalError, httpx.HTTPError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1

        print(f"Running {len(cases)} cases against {base_url} ({server_provider}: {model})")
        results = []
        for case in cases:
            result = run_case(client, case)
            print(f"  {'PASS' if result['passed'] else 'FAIL'}  {case['id']}")
            results.append(result)

    summary = metrics.summarize(results)
    categories = metrics.by_category(results)
    print()
    print(format_table(summary, categories))

    report = {
        "date": date.today().isoformat(),
        "api_url": base_url,
        "llm_provider": server_provider,
        "agent_model": health.get("agent_model"),
        "fallback_model": health.get("fallback_model"),
        "selection": {"fast": args.fast, "categories": args.category, "ids": args.ids},
        "summary": summary,
        "by_category": categories,
        "cases": results,
    }
    if settings.langsmith_tracing:
        try:
            report["langsmith_experiment"] = log_to_langsmith(cases, results, model)
            print(f"LangSmith experiment: {report['langsmith_experiment']}")
        except Exception as exc:  # optional: never lose the local results over it
            print(f"LangSmith upload failed: {exc}", file=sys.stderr)

    out = args.out or result_path(model, date.today())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
