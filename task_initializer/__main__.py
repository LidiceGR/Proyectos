"""CLI entry point for task initialization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .initializer import initialize_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize traceable SQL research tasks without an LLM")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--relationships", type=Path, required=True)
    parser.add_argument("--ast-directory", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace existing research_tasks.json")
    args = parser.parse_args()

    try:
        result = initialize_tasks(
            inventory_file=args.inventory,
            relationships_file=args.relationships,
            ast_directory=args.ast_directory,
            destination=args.destination,
            overwrite=args.overwrite,
            on_event=lambda event: print("[{}] {}".format(event["event"], " ".join(
                f"{key}={value}" for key, value in event.items() if key != "event"
            )), flush=True),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[task_initialization_failed] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Research tasks: {args.destination / 'research_tasks.json'}")
    print(f"Summary: {result['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
