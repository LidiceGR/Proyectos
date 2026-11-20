"""CLI for single-pass research of one task or a batch of pending tasks."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from lib.artifact_io import write_json
from .agent import analyze_task, load_task, render_prompt
from .batch import run_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze structural SQL research tasks with a local LLM")
    parser.add_argument("--tasks", required=True, type=Path, help="Existing research_tasks.json")
    parser.add_argument("--task-id", help="Task ID for single-task mode; defaults to first pending task")
    parser.add_argument("--preview", action="store_true", help="Show one prompt without loading Qwen")
    parser.add_argument("--batch", action="store_true", help="Analyze all pending tasks sequentially")
    parser.add_argument("--max-tasks", type=int, default=0, help="Batch limit; 0 means all pending tasks")
    parser.add_argument("--model-id", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--output-directory", type=Path, help="Output directory for individual agent runs")
    args = parser.parse_args()

    if args.batch and (args.task_id is not None or args.preview):
        parser.error("--batch cannot be combined with --task-id or --preview")
    if args.max_tasks < 0 or (not args.batch and args.max_tasks != 0):
        parser.error("--max-tasks must be nonnegative and is only supported with --batch")

    if args.batch:
        from llm.local_qwen import LocalQwen

        # The same model instance handles every task. Loading occurs lazily on
        # the first generation, so empty batches do not load any model weights.
        model = LocalQwen(model_id=args.model_id, max_new_tokens=args.max_new_tokens)
        summary = run_batch(
            args.tasks, model.generate, model_id=args.model_id,
            max_tasks=args.max_tasks,
            output_directory=args.output_directory,
            device_provider=lambda: model.device,
        )
        return 0 if summary["status"] == "completed" else 1

    task = load_task(args.tasks, args.task_id)
    print(f"[task_selected] id={task['task_id']} object={task['target']['object_name']} operation={task['fragment']['operation']}", flush=True)
    if args.preview:
        print("\n--- PROMPT PREVIEW (MODEL NOT LOADED) ---")
        print(render_prompt(task))
        return 0

    from llm.local_qwen import LocalQwen

    model = LocalQwen(model_id=args.model_id, max_new_tokens=args.max_new_tokens)
    result = analyze_task(task, model.generate, model_id=args.model_id)
    result["device"] = model.device
    output_dir = args.output_directory or args.tasks.parent / "agent_runs"
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"{task['task_id']}_{suffix}.json"
    if path.exists():
        raise FileExistsError(f"Output already exists: {path}")
    write_json(path, result)
    print(f"[agent_completed] status={result['status']} elapsed_seconds={result['elapsed_seconds']}", flush=True)
    if result["decision"] is not None:
        print(json.dumps(result["decision"], indent=2, ensure_ascii=False), flush=True)
    if result["validation_errors"]:
        print(f"Validation issues: {result['validation_errors']}", flush=True)
    print(f"Result file: {path}", flush=True)
    return 0 if result["status"] == "valid_json" else 1


if __name__ == "__main__":
    raise SystemExit(main())
