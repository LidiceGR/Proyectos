"""Analyze pending research tasks sequentially without executing ReAct actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent import analyze_task


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_checkpoint(path: Path, summary: dict[str, Any]) -> None:
    """Atomically update the batch summary without changing research_tasks.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _read_pending(tasks_path: Path) -> list[dict[str, Any]]:
    """Read structural tasks, checking identifiers before any model call."""
    document = json.loads(tasks_path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("tasks"), list):
        raise ValueError("The task document must contain a tasks array")
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in document["tasks"]:
        if not isinstance(task, dict):
            raise ValueError("All tasks must be JSON objects")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Every task must have a nonempty task_id")
        if task_id in seen:
            raise ValueError(f"Duplicate task_id in task document: {task_id}")
        seen.add(task_id)
        if task.get("status") == "pending":
            pending.append(task)
    return pending


def _validate_selected_task(task: dict[str, Any]) -> None:
    if task.get("task_type") not in {"analyze_sql_fragment", "analyze_sql_workflow"}:
        raise ValueError(f"Unsupported task type: {task.get('task_type')}")
    for field in ("target", "fragment", "evidence"):
        if not isinstance(task.get(field), dict):
            raise ValueError(f"Task {task['task_id']} must contain an object '{field}'")
    if not isinstance(task["target"].get("object_name"), str):
        raise ValueError(f"Task {task['task_id']} has no target object_name")
    if not isinstance(task["fragment"].get("operation"), str):
        raise ValueError(f"Task {task['task_id']} has no fragment operation")


def run_batch(
    tasks_path: str | Path,
    generate: Callable[[str, str], str],
    *,
    model_id: str,
    max_tasks: int = 0,
    output_directory: str | Path | None = None,
    device_provider: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Save each attempt immediately; continue when one task's analysis fails.

    max_tasks=0 means all tasks marked pending. Tasks are not marked completed:
    this is a baseline single-pass investigation with no evidence verification.
    """
    if type(max_tasks) is not int or max_tasks < 0:
        raise ValueError("max_tasks must be an integer greater than or equal to zero")
    source = Path(tasks_path).resolve()
    pending = _read_pending(source)
    selected = pending if max_tasks == 0 else pending[:max_tasks]
    destination = Path(output_directory) if output_directory is not None else source.parent / "agent_runs"
    destination = destination.resolve()
    summary_path = destination / "batch_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"Existing batch summary cannot be overwritten: {summary_path}")

    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "mode": "batch_single_pass_no_tools",
        "started_utc": _utc_now(),
        "finished_utc": None,
        "status": "running",
        "model_id": model_id,
        "tasks_file": str(source),
        "counts": {
            "total_pending": len(pending),
            "selected": len(selected),
            "processed": 0,
            "valid_json": 0,
            "invalid_response": 0,
            "evidence_incomplete": 0,
            "execution_error": 0,
            "not_selected": len(pending) - len(selected),
            "candidate_rules_proposed": 0,
            "requested_actions_proposed": 0,
        },
        "results": [],
        "note": "Research task statuses are not modified. Proposed actions are not executed.",
    }
    _write_checkpoint(summary_path, summary)
    print(f"[batch_started] pending={len(pending)} selected={len(selected)} max_tasks={max_tasks}", flush=True)

    for index, task in enumerate(selected, start=1):
        task_id = task["task_id"]
        print(f"[batch_task_started] current={index} total={len(selected)} task_id={task_id}", flush=True)
        try:
            _validate_selected_task(task)
            result = analyze_task(task, generate, model_id=model_id)
        except Exception as exc:
            result = {
                "schema_version": "1.0",
                "generated_utc": _utc_now(),
                "task_id": task_id,
                "model_id": model_id,
                "stage": "single_task_analysis_no_tools",
                "task_evidence": task.get("evidence"),
                "raw_model_output": None,
                "decision": None,
                "validation_errors": [f"{type(exc).__name__}: {exc}"],
                "status": "execution_error",
                "elapsed_seconds": 0.0,
                "note": "A generated proposal is not a verified or human-approved business rule.",
            }
        result["device"] = device_provider() if device_provider is not None else None
        # Use exclusive creation to guarantee that no previous result is overwritten.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        result_path = destination / f"{task_id}_{stamp}.json"
        destination.mkdir(parents=True, exist_ok=True)
        with result_path.open("x", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write("\n")

        status = result["status"]
        summary["counts"]["processed"] += 1
        summary["counts"][status] += 1
        decision = result.get("decision") or {}
        if status == "valid_json" and decision.get("classification") == "candidate_business_rule" and decision.get("candidate_rule"):
            summary["counts"]["candidate_rules_proposed"] += 1
        if status == "valid_json" and decision.get("requested_action") is not None:
            summary["counts"]["requested_actions_proposed"] += 1
        summary["results"].append({
            "task_id": task_id,
            "status": status,
            "result_file": str(result_path),
            "validation_errors": result.get("validation_errors", []),
            "evidence_status": (result.get("evidence_check") or {}).get("status"),
            "evidence_issues": (result.get("evidence_check") or {}).get("issues", []),
            "evidence_warnings": (result.get("evidence_check") or {}).get("warnings", []),
            "classification": decision.get("classification"),
            "candidate_rule_proposed": bool(decision.get("candidate_rule")),
        })
        _write_checkpoint(summary_path, summary)
        print(f"[batch_task_completed] task_id={task_id} status={status} file={result_path}", flush=True)

    summary["finished_utc"] = _utc_now()
    failed = sum(summary["counts"][name] for name in ("invalid_response", "evidence_incomplete", "execution_error"))
    summary["status"] = "completed_with_errors" if failed else "completed"
    _write_checkpoint(summary_path, summary)
    print(
        "[batch_completed] status={} selected={} valid_json={} evidence_incomplete={} invalid_response={} execution_error={} candidate_rules_proposed={}".format(
            summary["status"], summary["counts"]["selected"],
            summary["counts"]["valid_json"], summary["counts"]["evidence_incomplete"],
            summary["counts"]["invalid_response"], summary["counts"]["execution_error"],
            summary["counts"]["candidate_rules_proposed"],
        ),
        flush=True,
    )
    print(f"[batch_summary_file] path={summary_path}", flush=True)
    return summary
