"""Command-line entry point for the Capstone main coordinator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import apply_overrides, load_config
from .coordinator import PipelineCoordinator


def main() -> int:
    parser = argparse.ArgumentParser(description="Coordinate deterministic Capstone preparation before agent research")
    parser.add_argument("--config", required=True, type=Path, help="Pipeline JSON configuration")
    parser.add_argument("--run-id", help="Optional run identifier override")
    parser.add_argument("--research-mode", choices=("none", "preview", "single", "batch", "react", "multiagent"), help="Optional research override")
    rag_group = parser.add_mutually_exclusive_group()
    rag_group.add_argument("--initialize-rag", dest="initialize_rag", action="store_true", help="Build local BM25 source retrieval index")
    rag_group.add_argument("--no-rag", dest="initialize_rag", action="store_false")
    parser.set_defaults(initialize_rag=None)
    parser.add_argument("--dry-run", action="store_true", help="Print the workflow plan without creating files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config_file = args.config if args.config.is_absolute() else root / args.config
    try:
        config = apply_overrides(
            load_config(config_file.resolve(), root),
            run_id=args.run_id,
            initialize_rag=args.initialize_rag,
            research_mode=args.research_mode,
        )
        PipelineCoordinator(config, root).run(dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, NotImplementedError) as exc:
        print(f"[pipeline_failed] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
