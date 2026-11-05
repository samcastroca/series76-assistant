"""Ingest CLI: `python -m ingest.ingest {extract|build|load}`.

- extract: Gemini vision over the scanned PDF -> data/extracted/ (human-run only, network).
- build:   data/extracted/ + data/corrections.yaml -> data/processed/ (deterministic, offline).
- load:    data/processed/catalog.json -> Postgres upsert (deterministic).
"""

import argparse
import asyncio
import sys

from ingest.build import DATA_DIR, BuildError, run_build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingest.ingest", description=__doc__)
    parser.add_argument("stage", choices=["extract", "build", "load"])
    parser.add_argument(
        "--force", action="store_true", help="extract: re-read tables already in data/extracted/"
    )
    args = parser.parse_args(argv)

    if args.stage == "extract":
        from ingest.extract import ExtractError, run_extract

        try:
            diffs = run_extract(DATA_DIR / "extracted", force=args.force)
        except ExtractError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{len(diffs)} discrepancies -> data/extracted/discrepancies.md")
    elif args.stage == "build":
        try:
            records = run_build()
        except BuildError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{len(records)} part numbers -> data/processed/")
    else:
        from ingest.load import run_load

        print(f"{asyncio.run(run_load())} part numbers upserted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
