"""Build a compact, source-backed model context from existing research tasks.

The initializer remains the owner of segmentation. This module does not infer
business rules or change source evidence; it only removes redundant JSON fields
before presenting an existing logical workflow to the language model.
"""

from __future__ import annotations

from typing import Any


def _conditions(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conditions with their kind and source line when available."""
    found: list[dict[str, Any]] = []
    for field in ("condition_context", "where_conditions"):
        for condition in step.get(field) or []:
            if isinstance(condition, dict):
                entry = {
                    "kind": condition.get("kind", "CONDITION"),
                    "expression": condition.get("expression"),
                }
                source = condition.get("source") or {}
                if isinstance(source, dict) and source.get("line") is not None:
                    entry["line"] = source["line"]
                found.append(entry)
    return found


def _step_summary(step: dict[str, Any]) -> dict[str, Any]:
    """Keep ordered actions and grounded effects without duplicating SQL text."""
    result: dict[str, Any] = {
        "line": step.get("source_line"),
        "operation": step.get("operation"),
    }
    if step.get("target_table"):
        result["target_table"] = step["target_table"]
    if step.get("called_object"):
        result["called_object"] = step["called_object"]
    if step.get("read_tables"):
        result["read_tables"] = step["read_tables"]
    conditions = _conditions(step)
    if conditions:
        result["conditions"] = conditions
    effects = step.get("effects") or {}
    if effects:
        result["effects"] = effects
    return result


def compact_workflow_context(task: dict[str, Any]) -> dict[str, Any]:
    """Expose the original workflow SQL once and retain source-backed steps."""
    fragment = task["fragment"]
    if not isinstance(fragment.get("steps"), list):
        raise ValueError("Workflow tasks require an ordered steps array")
    evidence = task["evidence"]
    return {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "target": task["target"],
        "scope": task.get("scope"),
        "source": {
            "file": evidence.get("source_file"),
            "start_line": evidence.get("start_line"),
            "end_line": evidence.get("end_line"),
        },
        "parameters": task.get("object_context", {}).get("parameters", []),
        "related_objects": task.get("related_objects", []),
        "workflow_sql": fragment.get("sql", ""),
        "ordered_steps": [_step_summary(step) for step in fragment["steps"]],
    }
