"""Command-line entry point for Capstone inventory generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import build_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structural metadata from SQLFluff AST and source manifests")
    parser.add_argument("--ast-manifest", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()

    def report(event: dict) -> None:
        print(f"[{event['event']}] " + " ".join(
            f"{key}={value}" for key, value in event.items() if key != "event"
        ), flush=True)

    try:
        result = build_inventory(args.ast_manifest, args.source_manifest, args.destination, on_event=report)
        print("Metadata directory:", result["destination"])
        return 0 if result["status"] == "ok" else 1
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
