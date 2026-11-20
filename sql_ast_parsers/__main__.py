"""Command-line interface for the Capstone SQL AST parser."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .parser import parse_sql_sources


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert source-collector SQL files into SQLFluff syntactic AST JSON"
    )
    parser.add_argument("--manifest", required=True, type=Path,
                        help="Path to a source collector sources_manifest.json")
    parser.add_argument("--destination", required=True, type=Path,
                        help="Folder for AST JSON files and ast_manifest.json")
    parser.add_argument("--max-files", type=int, default=0,
                        help="Maximum SQL scripts to parse (0 = all)")
    args = parser.parse_args()

    def report_event(event: dict) -> None:
        print(f"[{event['event']}] " + " ".join(
            f"{key}={value}" for key, value in event.items() if key != "event"
        ), flush=True)

    try:
        result = parse_sql_sources(
            args.manifest, args.destination,
            max_files=args.max_files, on_event=report_event,
        )
        print("AST manifest:", Path(result["destination"]) / "ast_manifest.json")
        return 0 if result["status"] == "ok" else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
