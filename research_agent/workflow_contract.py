"""A source-backed checklist for PRIMARY SQL workflow interpretation."""
from __future__ import annotations
from typing import Any

def source_contract(task: dict[str, Any]) -> dict[str, Any]:
    """Represent the main workflow only, with provenance for the final prompt."""
    checks = []
    operations = []
    source = (task.get("evidence") or {}).get("source_file")
    for step in (task.get("fragment") or {}).get("steps") or []:
        operations.append({"operation": step.get("operation"),
                           "called_object": step.get("called_object"),
                           "target_table": step.get("target_table"),
                           "effects": step.get("effects"),
                           "source_line": step.get("source_line")})
        for cond in (step.get("condition_context") or []) + (step.get("where_conditions") or []):
            if isinstance(cond, dict):
                checks.append({"expression": cond.get("expression"),
                               "source_file": source,
                               "line": (cond.get("source") or {}).get("line", step.get("source_line"))})
    return {"primary_object": (task.get("target") or {}).get("object_name"),
            "primary_source_file": source, "primary_conditions": checks,
            "primary_operations": operations}

